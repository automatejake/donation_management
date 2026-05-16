# -*- coding: utf-8 -*-
from odoo import fields, models


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    donation_fee_coverage_percent = fields.Float(
        string='Donation Processing Fee %',
        help='Percentage added when a donor covers processing fees using this provider. '
             'Leave 0 to use the system default (Settings → Technical → Parameters).',
    )

    def get_donation_fee_coverage_percent(self):
        """Return the fee % for this provider (falls back to global config when unset)."""
        self.ensure_one()
        if self.donation_fee_coverage_percent:
            return self.donation_fee_coverage_percent
        return float(self.env['ir.config_parameter'].sudo().get_param(
            'donation_management.fee_coverage_percent', '3.0',
        ))
