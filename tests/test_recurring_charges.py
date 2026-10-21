# -*- coding: utf-8 -*-
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.donation_management.models.donation import MAX_FAILED_CHARGES

PROCESS = 'odoo.addons.donation_management.models.donation_payment.DonationPayment._process_token_payment'


@tagged('post_install', '-at_install')
class TestRecurringCharges(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Donor', 'email': 'donor@example.com'})
        provider = cls.env['payment.provider'].search([('code', '=', 'demo')], limit=1) \
            or cls.env['payment.provider'].search([], limit=1)
        method = cls.env['payment.method'].with_context(active_test=False).search([('code', '=', 'card')], limit=1) \
            or cls.env['payment.method'].with_context(active_test=False).search([], limit=1)
        cls.token = cls.env['payment.token'].create({
            'provider_id': provider.id,
            'payment_method_id': method.id,
            'partner_id': cls.partner.id,
            'provider_ref': 'tok-test',
            'payment_details': '4242',
        })

    def _rule(self):
        return self.env['donation.recurring.rule'].create({
            'partner_id': self.partner.id,
            'amount': 25.0,
            'currency_id': self.env.company.currency_id.id,
            'frequency': 'monthly',
            'next_donation_date': fields.Date.today(),
            'payment_token_id': self.token.id,
        })

    def test_charged_gift_moves_to_next_date(self):
        rule = self._rule()
        due = rule.next_donation_date
        with patch(PROCESS, autospec=True, return_value=True):
            rule.action_process_recurring_donations()
        self.assertGreater(rule.next_donation_date, due)
        self.assertFalse(rule.charging_donation_id)

    def test_unfinished_charge_is_not_charged_again(self):
        """A charge cut off mid-run blocks the gift until someone checks it."""
        rule = self._rule()
        donation = self.env['donation.donation'].with_context(from_website=True).create({
            'partner_id': self.partner.id,
            'amount': 25.0,
            'currency_id': self.env.company.currency_id.id,
            'recurring_rule_id': rule.id,
        })
        rule.charging_donation_id = donation
        with patch(PROCESS, autospec=True, return_value=True) as charge:
            rule.action_process_recurring_donations()
        charge.assert_not_called()
        self.assertEqual(rule.charging_donation_id, donation)
        self.assertTrue(rule.activity_ids)
        rule.action_clear_unfinished_charge()
        self.assertFalse(rule.charging_donation_id)

    def test_repeated_declines_pause_the_gift(self):
        rule = self._rule()
        with patch(PROCESS, autospec=True, return_value=False) as charge:
            for _attempt in range(MAX_FAILED_CHARGES):
                rule.action_process_recurring_donations()
            self.assertEqual(rule.state, 'paused')
            rule.next_donation_date = fields.Date.today() - timedelta(days=1)
            rule.action_process_recurring_donations()
        self.assertEqual(charge.call_count, MAX_FAILED_CHARGES)

    def test_charge_key_follows_gift_and_due_date(self):
        rule = self._rule()
        key = rule._charge_idempotency_key()
        self.assertEqual(key, rule._charge_idempotency_key())
        rule.next_donation_date = fields.Date.today() + timedelta(days=30)
        self.assertNotEqual(key, rule._charge_idempotency_key())
