# -*- coding: utf-8 -*-
from odoo import fields, models


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    donation_fee_coverage_percent = fields.Float(
        string='Donation Processing Fee %',
        help='Percentage added when a donor covers processing fees using this provider. '
             'Leave 0 to use the system default (Settings → Technical → Parameters).',
    )

    def _get_primary_payment_method(self):
        self.ensure_one()
        pms = self.with_context(active_test=False).payment_method_ids.filtered('active')
        if not pms:
            pms = self.with_context(active_test=False).payment_method_ids[:1]
        return pms[:1]

    def _donation_payment_operation(self, is_validation=False):
        """Return the payment.transaction operation for website donations."""
        self.ensure_one()
        if is_validation:
            return 'validation'
        if self.code == 'helcim' and self.helcim_checkout_mode == 'helcimpay_js':
            return 'online_direct'
        redirect_form_view = self._get_redirect_form_view(is_validation=False)
        return 'online_redirect' if redirect_form_view else 'online_direct'

    def get_donation_fee_coverage_percent(self):
        """Fee % from primary payment method, then provider, then system default."""
        self.ensure_one()
        payment_method = self._get_primary_payment_method()
        if payment_method:
            pm = payment_method._donation_fee_config_record()
            if pm.donation_fee_coverage_percent:
                return pm.donation_fee_coverage_percent
        if self.donation_fee_coverage_percent:
            return self.donation_fee_coverage_percent
        return float(self.env['ir.config_parameter'].sudo().get_param(
            'donation_management.fee_coverage_percent', '3.0',
        ))
