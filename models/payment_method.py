# -*- coding: utf-8 -*-
from odoo import fields, models


class PaymentMethod(models.Model):
    _inherit = 'payment.method'

    donation_fee_coverage_percent = fields.Float(
        string='Donation Processing Fee %',
        help='Percentage added when a donor covers processing fees with this payment method. '
             'Leave 0 to use the provider or system default.',
    )

    def _donation_fee_config_record(self):
        """Return the record that holds the fee (primary method for card brands)."""
        self.ensure_one()
        if not self.is_primary and self.primary_payment_method_id:
            return self.primary_payment_method_id
        return self

    def get_donation_fee_coverage_percent(self):
        """Fee % for this method, then provider, then system default."""
        self.ensure_one()
        pm = self._donation_fee_config_record()
        if pm.donation_fee_coverage_percent:
            return pm.donation_fee_coverage_percent
        provider = self.provider_ids[:1]
        if provider and provider.donation_fee_coverage_percent:
            return provider.donation_fee_coverage_percent
        return float(self.env['ir.config_parameter'].sudo().get_param(
            'donation_management.fee_coverage_percent', '3.0',
        ))
