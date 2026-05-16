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
