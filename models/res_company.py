# -*- coding: utf-8 -*-
from odoo import fields, models

DEFAULT_STATEMENT_NOTE = (
    'No goods or services were provided in exchange for these contributions, '
    'other than intangible religious benefits.'
)


class ResCompany(models.Model):
    _inherit = 'res.company'

    donation_statement_note = fields.Text(
        string='Year-End Statement Note',
        translate=True,
        default=DEFAULT_STATEMENT_NOTE,
        help='Printed at the bottom of each year-end giving statement. US donors need a '
             'statement about goods or services provided in exchange for gifts of $250 or more.',
    )
