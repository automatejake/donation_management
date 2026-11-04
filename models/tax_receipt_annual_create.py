# -*- coding: utf-8 -*-
from odoo import api, models


class TaxReceiptAnnualCreate(models.TransientModel):
    _inherit = 'tax.receipt.annual.create'

    @api.model
    def _prepare_annual_tax_receipt(self, partner, partner_dict):
        vals = super()._prepare_annual_tax_receipt(partner, partner_dict)
        vals['period_start_date'] = self.start_date
        return vals
