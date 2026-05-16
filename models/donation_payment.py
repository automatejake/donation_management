# -*- coding: utf-8 -*-
from odoo import models, api


class DonationPayment(models.Model):
    _inherit = 'donation.donation'

    def _process_token_payment(self):
        """Process payment using saved payment token"""
        self.ensure_one()
        
        if not self.payment_token_id:
            return False
        
        # Create payment transaction
        tx_values = self._prepare_payment_transaction_values()
        
        tx = self.env['payment.transaction'].create(tx_values)
        self.payment_transaction_id = tx.id
        
        # Process the transaction using the token
        try:
            tx._send_payment_request()
            
            # Check if payment was successful
            if tx.state == 'done':
                self.action_confirm()
                return True
            elif tx.state == 'pending':
                self.state = 'pending'
                return True
            else:
                return False
                
        except Exception as e:
            self.message_post(
                body=f"Payment processing failed: {str(e)}",
                subject="Payment Error"
            )
            return False

    def _prepare_payment_transaction_values(self):
        """Prepare values for payment transaction"""
        self.ensure_one()
        
        return {
            'provider_id': self.payment_token_id.provider_id.id,
            'payment_method_id': self.payment_token_id.payment_method_id.id,
            'reference': self.name,
            'amount': self.amount,
            'currency_id': self.currency_id.id,
            'partner_id': self.partner_id.id,
            'token_id': self.payment_token_id.id,
            'operation': 'offline',
            # 'callback_model_id': self.env['ir.model']._get('donation.donation').id,
            'callback_res_id': self.id,
            'callback_method': '_handle_payment_transaction_callback',
        }

    def _handle_payment_transaction_callback(self, **kwargs):
        """Handle callback from payment transaction"""
        self.ensure_one()
        
        tx = self.payment_transaction_id
        
        if tx.state == 'done':
            if self.state != 'confirmed':
                self.action_confirm()
        elif tx.state == 'cancel':
            self.state = 'cancelled'
        elif tx.state == 'error':
            self.state = 'draft'
            self.message_post(
                body="Payment failed. Please try again or contact support.",
                subject="Payment Error"
            )


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _reconcile_after_done(self):
        """Override to handle donation reconciliation"""
        res = super()._reconcile_after_done()
        
        # Handle donation-specific logic
        for tx in self:
            donations = self.env['donation.donation'].search([
                ('payment_transaction_id', '=', tx.id),
                ('state', '!=', 'confirmed')
            ])
            
            for donation in donations:
                donation.action_confirm()
        
        return res