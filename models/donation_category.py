# -*- coding: utf-8 -*-
from odoo import fields, models


class DonationCategory(models.Model):
    _name = 'donation.category'
    _description = 'Donation Category'
    _order = 'sequence, name'

    name = fields.Char(string='Name', required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    account_id = fields.Many2one(
        'account.account',
        string='Income Account',
        required=True,
        domain="[('deprecated', '=', False)]",
        help='Revenue account credited when the donation payment is received.',
    )
    description = fields.Text(string='Description', translate=True)
