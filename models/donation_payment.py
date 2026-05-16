# -*- coding: utf-8 -*-
from odoo import models
from odoo.addons.payment.models.payment_transaction import PaymentTransaction as PaymentTransactionBase


class DonationPayment(models.Model):
    _inherit = 'donation.donation'

    def _process_token_payment(self):
        self.ensure_one()
        if not self.payment_token_id:
            return False

        tx = self.env['payment.transaction'].create(self._prepare_payment_transaction_values())
        self.payment_transaction_id = tx.id

        try:
            tx._send_payment_request()
            if tx.state == 'done':
                tx._post_process()
                return True
            if tx.state == 'pending':
                self.state = 'pending'
                return True
            return False
        except Exception as e:
            self.message_post(
                body=f"Payment processing failed: {str(e)}",
                subject="Payment Error",
            )
            return False

    def _prepare_payment_transaction_values(self):
        self.ensure_one()
        return {
            'provider_id': self.payment_token_id.provider_id.id,
            'payment_method_id': self.payment_token_id.payment_method_id.id,
            'reference': self.name,
            'amount': self.amount,
            'currency_id': self.currency_id.id,
            'partner_id': self.partner_id.id,
            'token_id': self.payment_token_id.id,
            'operation': 'online_token',
        }

    def _finalize_after_payment(self, tx):
        """After online payment: charge now, save card for later, or one-time."""
        self.ensure_one()
        if tx.token_id:
            self.payment_token_id = tx.token_id.id

        if self._is_recurring_signup():
            if self._recurring_starts_later():
                self._finalize_recurring_setup()
            else:
                self._finalize_recurring_with_first_charge()
            return

        self._complete_donation()


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _post_process(self):
        """Donation payments: post receipts/revenue entry only (no invoice, no AR payment)."""
        donations = self.env['donation.donation'].search([
            ('payment_transaction_id', 'in', self.ids),
        ])
        donations_by_tx = {}
        for donation in donations:
            donations_by_tx.setdefault(donation.payment_transaction_id.id, donation)

        donation_txs = self.filtered(lambda t: t.id in donations_by_tx)
        other_txs = self - donation_txs

        if other_txs:
            super()._post_process(other_txs)

        for tx in donation_txs:
            PaymentTransactionBase._post_process(tx)
            donation = donations_by_tx.get(tx.id)
            if not donation:
                continue
            if tx.state == 'done':
                donation._finalize_after_payment(tx)
            elif tx.state == 'cancel':
                donation.action_cancel()
            elif tx.state == 'error' and donation.state == 'pending':
                donation.write({'state': 'draft'})
