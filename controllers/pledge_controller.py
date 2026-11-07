# -*- coding: utf-8 -*-
from odoo import http, _
from odoo.http import request
from odoo.tools import float_round

from odoo.addons.donation_management.models.donation_pledge import PLEDGE_FREQUENCY_SELECTION


class DonationPledgePortal(http.Controller):

    def _own_editable_pledge(self, pledge_id):
        """The signed-in donor's own pledge, if they may change it; otherwise None."""
        pledge = request.env['donation.pledge'].sudo().browse(pledge_id).exists()
        if not pledge or pledge.partner_id != request.env.user.partner_id:
            return None
        if not pledge._portal_can_edit():
            return None
        return pledge

    @http.route('/my/pledges/<int:pledge_id>/edit', type='http', auth='user', website=True, methods=['GET'])
    def pledge_edit(self, pledge_id, **kw):
        pledge = self._own_editable_pledge(pledge_id)
        if not pledge:
            return request.redirect('/my/donations')
        return request.render('donation_management.portal_pledge_edit', {
            'pledge': pledge,
            'frequencies': PLEDGE_FREQUENCY_SELECTION,
            'error': kw.get('error'),
            'page_name': 'donations',
        })

    @http.route('/my/pledges/<int:pledge_id>/edit', type='http', auth='user', website=True, methods=['POST'])
    def pledge_save(self, pledge_id, amount=None, frequency=None, **kw):
        pledge = self._own_editable_pledge(pledge_id)
        if not pledge:
            return request.redirect('/my/donations')
        try:
            value = float_round(float((amount or '').replace(',', '').replace('$', '').strip()), 2)
        except ValueError:
            value = 0.0
        if value <= 0:
            return request.redirect(f'/my/pledges/{pledge.id}/edit?error=amount')
        if frequency not in dict(PLEDGE_FREQUENCY_SELECTION):
            frequency = pledge.frequency
        if value != pledge.amount or frequency != pledge.frequency:
            # sudo keeps the donor as the author of the tracked change.
            pledge.write({'amount': value, 'frequency': frequency})
            pledge.message_post(body=_('Changed online by %s.', request.env.user.partner_id.name))
        return request.redirect('/my/donations?pledge_updated=1')
