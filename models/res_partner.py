# -*- coding: utf-8 -*-
from odoo import models, fields, api


class ResPartner(models.Model):
    _inherit = 'res.partner'

    donation_ids = fields.One2many('donation.donation', 'partner_id', string='Donations')
    donation_count = fields.Integer(string='Donation Count', compute='_compute_donation_stats')
    total_donated = fields.Monetary(string='Total Donated', compute='_compute_donation_stats', 
                                     currency_field='currency_id')
    
    recurring_rule_ids = fields.One2many('donation.recurring.rule', 'partner_id', 
                                          string='Recurring Donations')
    recurring_rule_count = fields.Integer(string='Active Recurring', compute='_compute_recurring_stats')
    
    is_donor = fields.Boolean(string='Is Donor', compute='_compute_is_donor', store=True)

    @api.depends('donation_ids.amount', 'donation_ids.state')
    def _compute_donation_stats(self):
        for partner in self:
            confirmed_donations = partner.donation_ids.filtered(lambda d: d.state == 'confirmed')
            partner.donation_count = len(confirmed_donations)
            partner.total_donated = sum(confirmed_donations.mapped('amount'))

    @api.depends('recurring_rule_ids.state')
    def _compute_recurring_stats(self):
        for partner in self:
            partner.recurring_rule_count = len(partner.recurring_rule_ids.filtered(
                lambda r: r.state == 'active'
            ))

    @api.depends('donation_count')
    def _compute_is_donor(self):
        for partner in self:
            partner.is_donor = partner.donation_count > 0

    def action_view_donations(self):
        """Open donations view for this partner"""
        self.ensure_one()
        return {
            'name': 'Donations',
            'type': 'ir.actions.act_window',
            'res_model': 'donation.donation',
            'view_mode': 'tree,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id},
        }