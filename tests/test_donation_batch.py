# -*- coding: utf-8 -*-
from datetime import date

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import Form, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestDonationBatch(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.groups_id |= cls.env.ref('donation_management.group_donation_manager')
        cls.company = cls.env.company
        cls.bank = cls.company_data['default_journal_bank']
        method_line = cls.bank.inbound_payment_method_line_ids[:1]
        cls.outstanding = method_line.payment_account_id or cls.env['account.account'].create({
            'name': 'Outstanding Receipts (test)',
            'code': 'OUTREC1',
            'account_type': 'asset_current',
            'reconcile': True,
        })
        method_line.payment_account_id = cls.outstanding
        income = cls.company_data['default_account_revenue']
        cls.building_income = cls.env['account.account'].create({
            'name': 'Building Fund Income',
            'code': 'BLD400',
            'account_type': 'income',
        })
        cls.general = cls.env['donation.category'].create({'name': 'General Fund', 'account_id': income.id})
        cls.building = cls.env['donation.category'].create({
            'name': 'Building Fund', 'account_id': cls.building_income.id,
        })
        cls.maria = cls.env['res.partner'].create({'name': 'Maria Popescu', 'email': 'maria@example.com'})
        cls.george = cls.env['res.partner'].create({'name': 'George Haddad'})
        cls.anonymous = cls.env.ref('donation_management.partner_anonymous_donor')

    def _batch(self, counted=0.0, lines=None):
        lines = lines if lines is not None else [
            {'partner_id': self.maria.id, 'category_id': self.general.id, 'payment_kind': 'check',
             'check_number': '1041', 'amount': 100.0},
            {'partner_id': self.george.id, 'category_id': self.building.id, 'payment_kind': 'check',
             'check_number': '220', 'amount': 250.0},
            {'category_id': self.general.id, 'payment_kind': 'cash', 'amount': 37.0},
        ]
        return self.env['donation.batch'].create({
            'label': 'Sunday offering',
            'date': date(2026, 11, 15),
            'journal_id': self.bank.id,
            'counted_total': counted,
            'line_ids': [Command.create(vals) for vals in lines],
        })

    def test_totals_and_difference(self):
        batch = self._batch(counted=400.0)
        self.assertEqual(batch.entered_total, 387.0)
        self.assertEqual(batch.check_total, 350.0)
        self.assertEqual(batch.cash_total, 37.0)
        self.assertEqual(batch.difference, 13.0)
        self.assertEqual(batch.line_count, 3)

    def test_cannot_post_until_lines_match_counted_total(self):
        batch = self._batch(counted=400.0)
        with self.assertRaises(UserError):
            batch.action_post()
        self.assertEqual(batch.state, 'draft')
        self.assertFalse(batch.donation_ids)
        batch.counted_total = 387.0
        batch.action_post()
        self.assertEqual(batch.state, 'posted')

    def test_post_makes_one_gift_per_line_and_one_deposit(self):
        batch = self._batch(counted=387.0)
        batch.action_post()

        donations = batch.donation_ids
        self.assertEqual(len(donations), 3)
        self.assertTrue(all(d.state == 'confirmed' for d in donations))
        self.assertTrue(all(d.tax_receipt_number for d in donations))
        maria_gift = donations.filtered(lambda d: d.partner_id == self.maria)
        self.assertEqual(maria_gift.amount, 100.0)
        self.assertEqual(maria_gift.check_number, '1041')
        self.assertEqual(maria_gift.payment_kind, 'check')
        self.assertEqual(maria_gift.date.date(), date(2026, 11, 15))
        cash_gift = donations.filtered(lambda d: d.payment_kind == 'cash')
        self.assertEqual(cash_gift.partner_id, self.anonymous)

        move = batch.move_id
        self.assertEqual(move.state, 'posted')
        self.assertEqual(set(donations.mapped('account_move_id').ids), {move.id})
        debit_lines = move.line_ids.filtered(lambda l: l.debit)
        self.assertEqual(len(debit_lines), 1, 'one deposit line so the bank deposit matches in one step')
        self.assertEqual(debit_lines.account_id, self.outstanding)
        self.assertEqual(debit_lines.debit, 387.0)
        building_credit = move.line_ids.filtered(lambda l: l.account_id == self.building_income)
        self.assertEqual(building_credit.credit, 250.0)
        self.assertEqual(building_credit.partner_id, self.george)

    def test_gifts_reach_year_end_statement_but_anonymous_does_not(self):
        self._batch(counted=387.0).action_post()
        wizard = self.env['tax.receipt.annual.create'].create({
            'start_date': date(2026, 1, 1),
            'end_date': date(2026, 12, 31),
            'company_id': self.company.id,
        })
        wizard.generate_annual_receipts()
        statements = self.env['donation.tax.receipt'].search([('type', '=', 'annual')])
        self.assertEqual(statements.partner_id, self.maria | self.george)
        self.assertEqual(statements.filtered(lambda s: s.partner_id == self.maria).amount, 100.0)

    def test_cancel_batch_cancels_gifts_and_entry(self):
        batch = self._batch()
        batch.action_post()
        gift = batch.donation_ids[:1]
        with self.assertRaises(UserError):
            gift.action_cancel()
        batch.action_cancel()
        self.assertEqual(batch.state, 'cancelled')
        self.assertEqual(batch.move_id.state, 'cancel')
        self.assertTrue(all(d.state == 'cancelled' for d in batch.donation_ids))
        batch.action_reset_to_draft()
        self.assertEqual(batch.state, 'draft')
        self.assertFalse(batch.donation_ids)
        batch.action_post()
        self.assertEqual(len(batch.donation_ids), 3)

    def test_empty_or_zero_lines_are_refused(self):
        with self.assertRaises(UserError):
            self._batch(lines=[]).action_post()
        with self.assertRaises(UserError):
            self._batch(lines=[{'partner_id': self.maria.id, 'category_id': self.general.id,
                                'payment_kind': 'cash', 'amount': 0.0}]).action_post()

    def test_posted_batch_cannot_be_deleted(self):
        batch = self._batch()
        batch.action_post()
        with self.assertRaises(UserError):
            batch.unlink()

    def test_new_line_takes_the_batch_default_fund(self):
        with Form(self.env['donation.batch']) as form:
            form.journal_id = self.bank
            form.default_category_id = self.building
            with form.line_ids.new() as line:
                line.partner_id = self.maria
                line.amount = 20.0
        batch = form.record
        self.assertEqual(batch.line_ids.category_id, self.building)
