# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.donation_management.controllers.donation_controller import DonationWebsiteController

REQUEST = 'odoo.addons.donation_management.controllers.donation_controller.request'


@tagged('post_install', '-at_install')
class TestDonationRoutes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.victim = cls.env['res.partner'].create({'name': 'Victim', 'email': 'victim@example.com'})
        cls.token = cls.env['payment.token'].create({
            'provider_id': cls.env['payment.provider'].search([], limit=1).id,
            'payment_method_id': cls.env['payment.method'].with_context(active_test=False).search([], limit=1).id,
            'partner_id': cls.victim.id,
            'provider_ref': 'tok-victim',
            'payment_details': '4242',
        })
        cls.controller = DonationWebsiteController()

    def _request(self, user):
        fake = MagicMock()
        fake.env = self.env(user=user)
        fake.redirect = lambda url, **kw: url
        return patch(REQUEST, fake)

    def test_saved_methods_are_not_listed_by_email(self):
        tokens = type(self.controller).donation_tokens.__wrapped__
        with self._request(self.env.ref('base.public_user')):
            self.assertEqual(tokens(self.controller, email='victim@example.com'), [])

    def test_only_the_owner_may_use_a_saved_method(self):
        with self._request(self.env.ref('base.public_user')):
            self.assertFalse(self.controller._own_payment_token(self.token.id))
        with self._request(self.env.ref('base.user_admin')):
            self.assertFalse(self.controller._own_payment_token(self.token.id))
        owner = self.env['res.users'].create({
            'name': 'Victim', 'login': 'victim-login', 'partner_id': self.victim.id,
            'groups_id': [(6, 0, [self.env.ref('base.group_portal').id])],
        })
        with self._request(owner):
            self.assertEqual(self.controller._own_payment_token(self.token.id), self.token)

    def test_helcim_js_complete_needs_the_checkout_token(self):
        donation = self.env['donation.donation'].with_context(from_website=True).create({
            'partner_id': self.victim.id,
            'amount': 10.0,
            'currency_id': self.env.company.currency_id.id,
            'state': 'pending',
        })
        donation._portal_ensure_token()
        complete = type(self.controller).donation_helcim_js_complete.__wrapped__
        with self._request(self.env.ref('base.public_user')):
            for token in ('', 'wrong'):
                url = complete(self.controller, donation_id=donation.id, access_token=token)
                self.assertIn('error=invalid_payment', url)
        self.assertTrue(donation.exists())
        self.assertEqual(donation.state, 'pending')
