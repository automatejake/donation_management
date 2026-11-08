# -*- coding: utf-8 -*-
"""The /donate page with Odoo's standard payment form, for any provider."""
import json
from datetime import timedelta

from odoo import Command, fields
from odoo.tests.common import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestDonationCheckout(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.method = cls.env['payment.method'].with_context(active_test=False).search(
            [('code', '=', 'card')], limit=1,
        )
        # Only this provider, so the page offers exactly what the test expects.
        cls.env['payment.provider'].search([]).write({'state': 'disabled'})
        cls.provider = cls.env['payment.provider'].create({
            'name': 'Checkout test provider',
            'code': 'none',
            'state': 'test',
            'is_published': True,
            'company_id': cls.env.company.id,
            'payment_method_ids': [Command.set(cls.method.ids)],
            'allow_tokenization': True,
        })
        cls.method.active = True
        cls.mary = new_test_user(
            cls.env, login='checkout-mary', password='checkout-mary-pw',
            groups='base.group_portal', name='Mary Checkout', email='checkout-mary@example.com',
        )

    def _rpc(self, url, params):
        res = self.url_open(
            url,
            data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'id': 1, 'params': params}),
            headers={'Content-Type': 'application/json'},
        )
        self.assertEqual(res.status_code, 200)
        return res.json()

    def _give(self, donation_values, **params):
        values = {
            'amount': '50',
            'frequency': 'one_time',
            'name': 'Guest Donor',
            'email': 'guest-donor@example.com',
            **donation_values,
        }
        return self._rpc('/donate/transaction', {
            'provider_id': self.provider.id,
            'payment_method_id': self.method.id,
            'token_id': None,
            'amount': 1.0,  # what the browser says; the gift decides
            'flow': 'direct',
            'tokenization_requested': False,
            'landing_route': '/payment/confirmation',
            'is_validation': False,
            'access_token': 'x',
            'donation_values': values,
            **params,
        })

    def _donation(self, reference):
        return self.env['donation.donation'].sudo().search([('name', '=', reference)])

    def test_page_shows_the_standard_payment_form(self):
        res = self.url_open('/donate')
        self.assertEqual(res.status_code, 200)
        self.assertIn('id="o_payment_form"', res.text)
        self.assertIn('data-transaction-route="/donate/transaction"', res.text)
        self.assertIn('Donate Now', res.text)
        self.assertIn('o_payment_method_%s' % self.method.id, res.text)

    def test_guest_gift_charges_the_gift_amount(self):
        result = self._give({'cover_fees': 'on', 'fee_percent': '3'})['result']
        donation = self._donation(result['reference'])
        self.assertEqual(donation.state, 'pending')
        tx = donation.payment_transaction_id
        self.assertEqual(tx.amount, 51.5, 'The gift plus the covered fee, not the browser amount.')
        self.assertEqual(tx.provider_id, self.provider)
        self.assertEqual(tx.operation, 'online_direct')
        self.assertFalse(tx.tokenize)
        self.assertEqual(donation.partner_id.email, 'guest-donor@example.com')

    def test_guest_cannot_save_a_card_for_a_one_time_gift(self):
        result = self._give({}, tokenization_requested=True)['result']
        self.assertFalse(self._donation(result['reference']).payment_transaction_id.tokenize)

    def test_recurring_gift_saves_the_card(self):
        result = self._give({
            'frequency': 'monthly', 'recurring_start_date': str(fields.Date.today()),
        })['result']
        tx = self._donation(result['reference']).payment_transaction_id
        self.assertTrue(tx.tokenize)
        self.assertEqual(tx.amount, 50.0)

    def test_recurring_gift_starting_later_only_checks_the_card(self):
        start = fields.Date.today() + timedelta(days=10)
        result = self._give({'frequency': 'weekly', 'recurring_start_date': str(start)})['result']
        tx = self._donation(result['reference']).payment_transaction_id
        self.assertEqual(tx.operation, 'validation')
        self.assertTrue(tx.tokenize)

    def test_bad_gift_leaves_nothing_behind(self):
        before = self.env['donation.donation'].sudo().search_count([])
        body = self._give({'amount': '0'})
        self.assertIn('valid donation amount', body['error']['data']['message'])
        body = self._give({'email': ''})
        self.assertIn('email', body['error']['data']['message'])
        self.assertEqual(self.env['donation.donation'].sudo().search_count([]), before)

    def test_recurring_needs_a_method_that_can_be_saved(self):
        self.provider.allow_tokenization = False
        before = self.env['donation.donation'].sudo().search_count([])
        body = self._give({
            'frequency': 'monthly', 'recurring_start_date': str(fields.Date.today()),
        })
        self.assertIn('recurring', body['error']['data']['message'])
        self.assertEqual(self.env['donation.donation'].sudo().search_count([]), before)

    def test_saved_card_for_a_gift_that_starts_later(self):
        token = self.env['payment.token'].create({
            'provider_id': self.provider.id,
            'payment_method_id': self.method.id,
            'partner_id': self.mary.partner_id.id,
            'provider_ref': 'checkout-card',
            'payment_details': '4242',
        })
        self.authenticate('checkout-mary', 'checkout-mary-pw')
        start = fields.Date.today() + timedelta(days=7)
        result = self._give(
            {'frequency': 'monthly', 'recurring_start_date': str(start)},
            flow='token', token_id=token.id, payment_method_id=None,
        )['result']
        self.assertIn('/donate/confirmation/', result['donation_redirect'])
        donation = self.env['donation.donation'].sudo().search(
            [('partner_id', '=', self.mary.partner_id.id)], order='id desc', limit=1,
        )
        self.assertEqual(donation.state, 'scheduled')
        self.assertEqual(donation.recurring_rule_id.payment_token_id, token)

    def test_someone_elses_saved_card_is_refused(self):
        other = self.env['res.partner'].create({'name': 'Someone else'})
        token = self.env['payment.token'].create({
            'provider_id': self.provider.id,
            'payment_method_id': self.method.id,
            'partner_id': other.id,
            'provider_ref': 'not-yours',
            'payment_details': '1111',
        })
        self.authenticate('checkout-mary', 'checkout-mary-pw')
        body = self._give({}, flow='token', token_id=token.id, payment_method_id=None)
        self.assertIn('saved payment method', body['error']['data']['message'])
