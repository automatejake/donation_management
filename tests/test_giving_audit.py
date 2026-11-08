# -*- coding: utf-8 -*-
"""End-to-end checks of giving, written for the November 2026 audit.

Each test follows one thing a donor or the church office does: give once,
start a recurring gift, have the monthly charge run, manage a gift from the
donor's account, download history, and receive the thank-you email.

Tests whose name ends in ``_new_database`` run against books exactly as a
new Odoo 18 database leaves them. The others first set up the books the way
a church would (an outstanding receipts account on the Bank and Cash
journals), so they test giving rather than that setting.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields, http
from odoo.tests.common import HttpCase, TransactionCase, new_test_user, tagged


class GivingCommon:

    @classmethod
    def _setup_giving(cls):
        env = cls.env
        cls.company = env.company
        cls.currency = cls.company.currency_id
        cls.provider = env['payment.provider'].search(
            [('code', '=', 'helcim'), ('company_id', '=', cls.company.id)], limit=1,
        ) or env['payment.provider'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.provider.write({'state': 'test', 'helcim_api_token': 'audit-test-token'})
        cls.method = env['payment.method'].with_context(active_test=False).search(
            [('code', '=', 'card')], limit=1,
        )
        cls.income = env['account.account'].search([
            ('account_type', '=', 'income'),
            ('company_ids', 'in', cls.company.id),
        ], limit=1)
        cls.fund = env['donation.category'].create({
            'name': 'General Fund', 'account_id': cls.income.id,
        })
        cls.mary = new_test_user(
            env, login='audit-mary', password='audit-mary-pw', groups='base.group_portal',
            name='Mary Thompson', email='audit-mary@example.com',
        )
        cls.other = new_test_user(
            env, login='audit-other', password='audit-other-pw', groups='base.group_portal',
            name='Other Donor', email='audit-other@example.com',
        )
        cls.card = env['payment.token'].create({
            'provider_id': cls.provider.id,
            'payment_method_id': cls.method.id,
            'partner_id': cls.mary.partner_id.id,
            'provider_ref': 'audit-card',
            'payment_details': '4242',
        })

    @classmethod
    def _set_up_books(cls):
        """What a church office does once: outstanding receipts on Bank and Cash."""
        receipts = cls.env['account.account'].search([
            ('account_type', '=', 'asset_current'),
            ('company_ids', 'in', cls.company.id),
            ('reconcile', '=', True),
        ], limit=1)
        for journal in cls.env['account.journal'].search([
            ('company_id', '=', cls.company.id), ('type', 'in', ('bank', 'cash')),
        ]):
            journal.inbound_payment_method_line_ids.filtered(
                lambda line: not line.payment_account_id,
            ).payment_account_id = receipts

    def _website_gift(self, amount=50.0, frequency='one_time', start=None, partner=None):
        """A gift as the /donate form leaves it, before the card is charged."""
        donation = self.env['donation.donation'].with_context(from_website=True).create({
            'partner_id': (partner or self.mary.partner_id).id,
            'amount': amount,
            'currency_id': self.currency.id,
            'category_id': self.fund.id,
            'frequency': frequency,
            'recurring_start_date': start,
            'state': 'draft',
        })
        tx = self.env['payment.transaction'].create({
            'provider_id': self.provider.id,
            'payment_method_id': self.method.id,
            'reference': donation.name,
            'amount': amount if not start or start <= fields.Date.today() else 0.0,
            'currency_id': self.currency.id,
            'partner_id': donation.partner_id.id,
            'operation': (
                'validation' if start and start > fields.Date.today() else 'online_direct'
            ),
            'tokenize': frequency != 'one_time',
        })
        donation.write({'payment_transaction_id': tx.id, 'state': 'pending'})
        return donation, tx

    def _card_approved(self, tx, save_card=False):
        """The provider says yes (and saves the card when asked)."""
        if save_card:
            tx.token_id = self.card
        tx._set_done()
        tx._post_process()


@tagged('post_install', '-at_install')
class TestGivingNewDatabase(GivingCommon, TransactionCase):
    """A church that has just installed giving and changed nothing in Accounting."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_giving()

    def test_online_gift_is_recorded_new_database(self):
        donation, tx = self._website_gift(50.0)
        self._card_approved(tx)
        self.assertEqual(
            donation.state, 'confirmed',
            'The card was charged, so the gift must be recorded and thanked.',
        )
        self.assertTrue(donation.account_move_id)

    def test_office_records_check_new_database(self):
        donation = self.env['donation.donation'].create({
            'partner_id': self.mary.partner_id.id,
            'amount': 100.0,
            'currency_id': self.currency.id,
            'category_id': self.fund.id,
        })
        self.assertEqual(donation.state, 'confirmed')
        self.assertEqual(donation.account_move_id.state, 'posted')


@tagged('post_install', '-at_install')
class TestGivingFlows(GivingCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_giving()
        cls._set_up_books()

    def test_one_time_gift(self):
        donation, tx = self._website_gift(50.0)
        self._card_approved(tx)
        self.assertEqual(donation.state, 'confirmed')
        self.assertTrue(donation.tax_receipt_number)
        move = donation.account_move_id
        self.assertEqual(move.state, 'posted')
        self.assertEqual(
            move.line_ids.filtered(lambda line: line.account_id == self.income).credit, 50.0,
        )
        self.assertFalse(donation.recurring_rule_id)

    def test_monthly_gift_starting_today_charges_now_and_next_month(self):
        today = fields.Date.today()
        donation, tx = self._website_gift(100.0, 'monthly', today)
        self._card_approved(tx, save_card=True)
        self.assertEqual(donation.state, 'confirmed')
        rule = donation.recurring_rule_id
        self.assertTrue(rule)
        self.assertEqual(rule.state, 'active')
        self.assertEqual(rule.payment_token_id, self.card)
        self.assertEqual(rule.amount, 100.0)
        self.assertEqual(rule.next_donation_date, donation._calculate_next_date(today))

    def test_weekly_gift_starting_later_only_saves_the_card(self):
        start = fields.Date.today() + timedelta(days=7)
        donation, tx = self._website_gift(20.0, 'weekly', start)
        self._card_approved(tx, save_card=True)
        self.assertEqual(donation.state, 'scheduled')
        self.assertFalse(donation.account_move_id, 'Nothing was charged yet.')
        self.assertEqual(donation.recurring_rule_id.next_donation_date, start)

    def _due_rule(self, amount=100.0):
        return self.env['donation.recurring.rule'].create({
            'partner_id': self.mary.partner_id.id,
            'amount': amount,
            'currency_id': self.currency.id,
            'frequency': 'monthly',
            'category_id': self.fund.id,
            'next_donation_date': fields.Date.today(),
            'payment_token_id': self.card.id,
        })

    def _charge_run(self, outcome):
        def send(tx):
            if outcome == 'done':
                tx._set_done()
            else:
                tx._set_error('Declined')
        Tx = type(self.env['payment.transaction'])
        with patch.object(Tx, '_send_payment_request', autospec=True, side_effect=send):
            self.env['donation.recurring.rule'].action_process_recurring_donations()

    def test_monthly_charge_run_records_the_gift(self):
        rule = self._due_rule()
        due = rule.next_donation_date
        self._charge_run('done')
        gift = rule.donation_ids
        self.assertEqual(len(gift), 1)
        self.assertEqual(gift.state, 'confirmed')
        self.assertTrue(gift.tax_receipt_number)
        self.assertEqual(gift.account_move_id.state, 'posted')
        self.assertEqual(rule.next_donation_date, rule._calculate_next_date(due))
        # Running again the same day charges nothing more.
        self._charge_run('done')
        self.assertEqual(len(rule.donation_ids), 1)

    def test_declined_card_retries_then_pauses(self):
        rule = self._due_rule()
        for _attempt in range(3):
            self._charge_run('error')
        self.assertEqual(rule.state, 'paused')
        self.assertFalse(rule.donation_ids.filtered(lambda d: d.state == 'confirmed'))

    def test_year_end_statement_adds_online_recurring_and_check_gifts(self):
        donation, tx = self._website_gift(50.0)
        self._card_approved(tx)
        rule = self._due_rule(100.0)
        self._charge_run('done')
        self.env['donation.donation'].create({
            'partner_id': self.mary.partner_id.id,
            'amount': 250.0,
            'currency_id': self.currency.id,
            'category_id': self.fund.id,
        })
        today = fields.Date.today()
        wizard = self.env['tax.receipt.annual.create'].create({
            'start_date': today.replace(month=1, day=1),
            'end_date': today.replace(month=12, day=31),
            'company_id': self.company.id,
        })
        wizard.generate_annual_receipts()
        statement = self.env['donation.tax.receipt'].search([
            ('type', '=', 'annual'), ('partner_id', '=', self.mary.partner_id.id),
        ])
        self.assertEqual(statement.amount, 400.0)
        self.assertEqual(len(rule.donation_ids), 1)

    def test_thank_you_email_names_the_donor_and_amount(self):
        donation, tx = self._website_gift(50.0)
        self._card_approved(tx)
        template = self.env.ref('donation_management.email_template_donation_confirmation')
        body = template._render_field('body_html', donation.ids)[donation.id]
        self.assertIn('Mary Thompson', body)
        self.assertIn(donation.name, body)
        self.assertNotIn('{{', body, 'Placeholders must be filled in, not shown to the donor.')

    def test_scheduled_and_failed_emails_fill_in_details(self):
        start = fields.Date.today() + timedelta(days=7)
        donation, tx = self._website_gift(20.0, 'weekly', start)
        self._card_approved(tx, save_card=True)
        for xmlid, record in (
            ('donation_management.email_template_recurring_scheduled', donation),
            ('donation_management.email_template_recurring_payment_failed',
             donation.recurring_rule_id),
        ):
            body = self.env.ref(xmlid)._render_field('body_html', record.ids)[record.id]
            self.assertIn('Mary Thompson', body, xmlid)
            self.assertNotIn('{{', body, xmlid)


@tagged('post_install', '-at_install')
class TestDonorAccount(GivingCommon, HttpCase):
    """What a signed-in donor can do from My Account > Donations."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_giving()
        cls._set_up_books()
        cls.gift = cls.env['donation.donation'].create({
            'partner_id': cls.mary.partner_id.id,
            'amount': 75.0,
            'currency_id': cls.currency.id,
            'category_id': cls.fund.id,
        })
        cls.rule = cls.env['donation.recurring.rule'].create({
            'partner_id': cls.mary.partner_id.id,
            'amount': 25.0,
            'currency_id': cls.currency.id,
            'frequency': 'monthly',
            'next_donation_date': fields.Date.today() + timedelta(days=10),
            'payment_token_id': cls.card.id,
        })

    def _post(self, url):
        return self.url_open(url, data={'csrf_token': http.Request.csrf_token(self)})

    def test_donor_sees_history_and_recurring_gifts(self):
        self.authenticate('audit-mary', 'audit-mary-pw')
        res = self.url_open('/my/donations')
        self.assertEqual(res.status_code, 200)
        self.assertIn(self.gift.name, res.text)
        self.assertIn(self.rule.name, res.text)
        res = self.url_open('/my/donations/%s' % self.gift.id)
        self.assertEqual(res.status_code, 200)
        self.assertIn(self.gift.tax_receipt_number, res.text)

    def test_donor_downloads_history_csv(self):
        self.authenticate('audit-mary', 'audit-mary-pw')
        res = self.url_open('/my/donations/export?export_format=csv')
        self.assertEqual(res.status_code, 200)
        self.assertIn('text/csv', res.headers['Content-Type'])
        self.assertIn(self.gift.name, res.text)

    def test_donor_downloads_history_pdf(self):
        self.authenticate('audit-mary', 'audit-mary-pw')
        res = self.url_open('/my/donations/export?export_format=pdf')
        self.assertEqual(res.status_code, 200, 'The PDF button must give the donor a file.')

    def test_donor_pauses_resumes_and_cancels(self):
        self.authenticate('audit-mary', 'audit-mary-pw')
        self._post('/my/recurring_donations/%s/pause' % self.rule.id)
        self.assertEqual(self.rule.state, 'paused')
        self._post('/my/recurring_donations/%s/resume' % self.rule.id)
        self.assertEqual(self.rule.state, 'active')
        self._post('/my/recurring_donations/%s/cancel' % self.rule.id)
        self.assertEqual(self.rule.state, 'cancelled')

    def test_another_donor_cannot_touch_the_gift(self):
        self.authenticate('audit-other', 'audit-other-pw')
        self._post('/my/recurring_donations/%s/cancel' % self.rule.id)
        self.assertEqual(self.rule.state, 'active')
        res = self.url_open('/my/donations')
        self.assertNotIn(self.gift.name, res.text)
        self.assertNotIn(self.rule.name, res.text)
        res = self.url_open('/my/donations/%s' % self.gift.id)
        self.assertNotIn(self.gift.tax_receipt_number, res.text)
