# -*- coding: utf-8 -*-
from odoo import http
from odoo.addons.payment_helcim.controllers.main import HelcimController
from odoo.http import request


class DonationHelcimController(HelcimController):
    """Extend HelcimPay.js status handling for website donations."""

    @http.route(HelcimController._helcim_js_payment_status_url, type='json', auth='public')
    def process_payment_status(
        self, reference, event_status, event_message, tokenization_requested
    ):
        result = super().process_payment_status(
            reference, event_status, event_message, tokenization_requested
        )
        if result.get('status') != 'success' or not result.get('transaction_id'):
            return result

        tx = request.env['payment.transaction'].sudo().browse(result['transaction_id'])
        if not tx.exists():
            return result

        donation = request.env['donation.donation'].sudo().search(
            [('payment_transaction_id', '=', tx.id)],
            limit=1,
        )
        if not donation:
            return result

        if tx.state == 'done' and not tx.is_post_processed:
            tx._post_process()

        donation._portal_ensure_token()
        result['donation_id'] = donation.id
        result['access_token'] = donation.access_token
        return result
