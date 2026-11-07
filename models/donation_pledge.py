# -*- coding: utf-8 -*-
from datetime import datetime, time

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

PLEDGE_FREQUENCY_SELECTION = [
    ('weekly', 'Weekly'),
    ('monthly', 'Monthly'),
    ('yearly', 'Once a year'),
]

PLEDGE_STATUS_SELECTION = [
    ('upcoming', 'Not started'),
    ('on_track', 'On track'),
    ('behind', 'Behind'),
    ('later', 'Gives at year end'),
    ('done', 'Fulfilled'),
]


class DonationPledge(models.Model):
    """A family's stewardship commitment for one campaign (usually a year).

    Nothing is charged from a pledge. Progress counts the family's confirmed
    gifts dated inside the campaign's dates (to the pledge's fund, when one is
    set), however they gave: online, recurring, or envelope in a Sunday batch.
    """
    _name = 'donation.pledge'
    _description = 'Stewardship Pledge'
    _inherit = ['mail.thread']
    _order = 'campaign_id desc, partner_id'
    _check_company_auto = True

    campaign_id = fields.Many2one(
        'donation.campaign', string='Stewardship Campaign', required=True, index=True,
        ondelete='restrict', tracking=True,
        help='For example "2027 Stewardship". The campaign needs a start and end date.',
    )
    partner_id = fields.Many2one('res.partner', string='Family', required=True, index=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(related='company_id.currency_id', string='Currency')
    category_id = fields.Many2one(
        'donation.category', string='Fund',
        help='Only gifts to this fund count toward the pledge. Leave empty to count gifts to every fund.',
    )
    amount = fields.Monetary(string='Pledged', required=True, currency_field='currency_id', tracking=True)
    frequency = fields.Selection(
        PLEDGE_FREQUENCY_SELECTION, string='Plans to Give', required=True, default='monthly',
    )
    note = fields.Text(string='Note')
    date_start = fields.Date(related='campaign_id.start_date', string='Starts')
    date_end = fields.Date(related='campaign_id.end_date', string='Ends')

    given = fields.Monetary(string='Given So Far', compute='_compute_progress', currency_field='currency_id')
    expected = fields.Monetary(string='Expected by Now', compute='_compute_progress', currency_field='currency_id')
    remaining = fields.Monetary(string='Still to Give', compute='_compute_progress', currency_field='currency_id')
    progress = fields.Float(string='Progress', compute='_compute_progress')
    status = fields.Selection(PLEDGE_STATUS_SELECTION, string='Status', compute='_compute_progress')

    _sql_constraints = [
        ('pledge_unique', 'unique(campaign_id, partner_id)',
         'This family already has a pledge for this campaign. Change that pledge instead.'),
        ('pledge_positive', 'CHECK(amount > 0)', 'A pledge needs an amount above zero.'),
    ]

    @api.constrains('campaign_id')
    def _check_campaign_dates(self):
        for pledge in self:
            campaign = pledge.campaign_id
            if not campaign.start_date or not campaign.end_date:
                raise ValidationError(_(
                    'Give campaign "%s" a start and end date before taking pledges for it.', campaign.name,
                ))

    def _gift_domain(self):
        self.ensure_one()
        campaign = self.campaign_id
        domain = [
            ('partner_id', '=', self.partner_id.id),
            ('state', '=', 'confirmed'),
            ('company_id', 'in', [self.company_id.id, False]),
            ('date', '>=', datetime.combine(campaign.start_date, time.min)),
            ('date', '<=', datetime.combine(campaign.end_date, time.max)),
        ]
        if self.category_id:
            domain.append(('category_id', '=', self.category_id.id))
        return domain

    def _expected_by(self, today):
        """What a family keeping its pledge would have given by `today`."""
        self.ensure_one()
        start, end = self.campaign_id.start_date, self.campaign_id.end_date
        if today < start:
            return 0.0
        if today >= end:
            return self.amount
        if self.frequency == 'yearly':
            return 0.0
        total_days = (end - start).days + 1
        share = ((today - start).days + 1) / total_days
        return self.currency_id.round(self.amount * share)

    @api.depends('amount', 'frequency', 'category_id', 'partner_id', 'campaign_id.start_date', 'campaign_id.end_date')
    def _compute_progress(self):
        today = fields.Date.context_today(self)
        Donation = self.env['donation.donation'].sudo()
        for pledge in self:
            if not (pledge.campaign_id.start_date and pledge.campaign_id.end_date and pledge.partner_id):
                pledge.given = pledge.expected = pledge.progress = 0.0
                pledge.remaining = pledge.amount
                pledge.status = 'upcoming'
                continue
            given = sum(Donation.search(pledge._gift_domain()).mapped('amount'))
            expected = pledge._expected_by(today)
            pledge.given = given
            pledge.expected = expected
            pledge.remaining = max(pledge.amount - given, 0.0)
            pledge.progress = min(100.0 * given / pledge.amount, 100.0) if pledge.amount else 0.0
            if pledge.amount and given >= pledge.amount:
                pledge.status = 'done'
            elif today < pledge.campaign_id.start_date:
                pledge.status = 'upcoming'
            elif pledge.frequency == 'yearly' and today < pledge.campaign_id.end_date:
                pledge.status = 'later'
            elif given >= expected * 0.95:
                pledge.status = 'on_track'
            else:
                pledge.status = 'behind'

    @api.depends('partner_id', 'campaign_id')
    def _compute_display_name(self):
        for pledge in self:
            pledge.display_name = f'{pledge.campaign_id.name or ""}: {pledge.partner_id.name or ""}'

    def action_view_gifts(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Gifts toward this pledge'),
            'res_model': 'donation.donation',
            'view_mode': 'list,form',
            'domain': self._gift_domain(),
        }

    @api.model
    def _pledges_enabled(self):
        group = self.env.ref('donation_management.group_donation_pledges', raise_if_not_found=False)
        return bool(group) and group in self.env.ref('base.group_user').sudo().implied_ids

    @api.model
    def _portal_pledges(self, partner):
        """Pledges a donor sees on My Account: their own, for campaigns running now."""
        if not partner or not self._pledges_enabled():
            return self.browse()
        today = fields.Date.context_today(self)
        return self.sudo().search([
            ('partner_id', '=', partner.id),
            ('campaign_id.start_date', '<=', today),
            ('campaign_id.end_date', '>=', today),
        ])


class DonationCampaign(models.Model):
    _inherit = 'donation.campaign'

    pledge_ids = fields.One2many('donation.pledge', 'campaign_id', string='Pledges')
    pledge_count = fields.Integer(string='Pledges', compute='_compute_pledge_totals')
    pledged_total = fields.Monetary(
        string='Total Pledged', compute='_compute_pledge_totals', currency_field='currency_id',
    )
    pledge_given_total = fields.Monetary(
        string='Given Toward Pledges', compute='_compute_pledge_totals', currency_field='currency_id',
    )

    @api.depends('pledge_ids.amount')
    def _compute_pledge_totals(self):
        for campaign in self:
            pledges = campaign.pledge_ids
            campaign.pledge_count = len(pledges)
            campaign.pledged_total = sum(pledges.mapped('amount'))
            campaign.pledge_given_total = sum(pledges.mapped('given'))

    def action_view_pledges(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Pledges'),
            'res_model': 'donation.pledge',
            'view_mode': 'list,form',
            'domain': [('campaign_id', '=', self.id)],
            'context': {'default_campaign_id': self.id},
        }


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    group_donation_pledges = fields.Boolean(
        string='Stewardship Pledges',
        implied_group='donation_management.group_donation_pledges',
        help='Families pledge a yearly amount; the office sees who is on track and each family sees '
             'its own progress on My Account.',
    )
