# -*- coding: utf-8 -*-
from datetime import date, datetime

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestYearEndStatements(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.alice = cls.env['res.partner'].create({'name': 'Alice Donor', 'email': 'alice@example.com'})
        cls.bob = cls.env['res.partner'].create({'name': 'Bob Donor'})
        cls.carol = cls.env['res.partner'].create({'name': 'Carol Donor', 'email': 'carol@example.com'})
        cls.carol.tax_receipt_option = 'none'

    def _gift(self, partner, amount, when, state='confirmed'):
        donation = self.env['donation.donation'].with_context(from_website=True).create({
            'partner_id': partner.id,
            'amount': amount,
            'currency_id': self.company.currency_id.id,
            'date': when,
        })
        donation.with_context(skip_donation_complete=True).write({'state': state})
        return donation

    def _generate(self, start=date(2026, 1, 1), end=date(2026, 12, 31)):
        wizard = self.env['tax.receipt.annual.create'].create({
            'start_date': start,
            'end_date': end,
            'company_id': self.company.id,
        })
        wizard.generate_annual_receipts()
        return self.env['donation.tax.receipt'].search([
            ('type', '=', 'annual'),
            ('donation_date', '=', end),
        ])

    def test_one_statement_per_donor_with_their_gifts(self):
        a1 = self._gift(self.alice, 50.0, datetime(2026, 1, 1, 9, 0))
        a2 = self._gift(self.alice, 25.0, datetime(2026, 12, 31, 23, 0))
        b1 = self._gift(self.bob, 100.0, datetime(2026, 6, 15, 12, 0))
        self._gift(self.alice, 10.0, datetime(2025, 12, 31, 12, 0))  # previous year
        self._gift(self.alice, 10.0, datetime(2027, 1, 1, 0, 30))  # next year
        self._gift(self.alice, 10.0, datetime(2026, 3, 1), state='cancelled')
        self._gift(self.bob, 10.0, datetime(2026, 3, 1), state='pending')

        statements = self._generate()

        self.assertEqual(len(statements), 2)
        alice = statements.filtered(lambda s: s.partner_id == self.alice)
        bob = statements.filtered(lambda s: s.partner_id == self.bob)
        self.assertEqual(alice.amount, 75.0)
        self.assertEqual(alice.donation_ids, a1 | a2)
        self.assertEqual(alice.period_start_date, date(2026, 1, 1))
        self.assertEqual(bob.amount, 100.0)
        self.assertEqual(bob.donation_ids, b1)
        self.assertEqual(a1.tax_receipt_id, alice)

    def test_donors_who_opted_out_get_no_statement(self):
        self._gift(self.carol, 40.0, datetime(2026, 5, 1))
        with self.assertRaises(UserError):
            self._generate()

    def test_running_again_does_not_duplicate(self):
        self._gift(self.alice, 50.0, datetime(2026, 2, 1))
        self._generate()
        with self.assertRaises(UserError):
            self._generate()
        self.assertEqual(
            self.env['donation.tax.receipt'].search_count([('partner_id', '=', self.alice.id)]), 1,
        )

    def test_gift_receipt_numbers_keep_their_own_sequence(self):
        number = self.env['ir.sequence'].next_by_code('donation_management.gift.receipt')
        self.assertTrue(number.startswith('TR'))

    def test_statement_pdf_lists_gifts_and_note(self):
        self._gift(self.alice, 50.0, datetime(2026, 2, 1))
        statement = self._generate()
        html, _ = self.env['ir.actions.report']._render_qweb_html(
            'donation_base.report_donation_tax_receipt', statement.ids,
        )
        html = html.decode()
        self.assertIn('Year-End Giving Statement', html)
        self.assertIn(statement.donation_ids.name, html)
        self.assertIn('intangible religious benefits', html)
        self.assertEqual(html.count('Year-End Giving Statement'), 1)

    def test_email_statements_skips_donors_without_email(self):
        self._gift(self.alice, 50.0, datetime(2026, 2, 1))
        self._gift(self.bob, 20.0, datetime(2026, 2, 1))
        statements = self._generate()
        mails_before = self.env['mail.mail'].search_count([])
        result = statements.action_email_statements()
        self.assertEqual(self.env['mail.mail'].search_count([]), mails_before + 1)
        self.assertIn('Bob Donor', result['params']['message'])
        mail = self.env['mail.mail'].search([], order='id desc', limit=1)
        self.assertIn(self.alice, mail.recipient_ids)
        self.assertTrue(mail.attachment_ids)
