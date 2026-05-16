# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from dateutil.relativedelta import relativedelta


class Donation(models.Model):
    _name = 'donation.donation'
    _description = 'Donation'
    _inherit = ['portal.mixin', 'mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default='New')
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        index=True,
    )
    partner_id = fields.Many2one('res.partner', string='Donor', required=True, tracking=True)
    date = fields.Datetime(string='Donation Date', required=True, default=fields.Datetime.now, tracking=True)
    amount = fields.Monetary(string='Amount', required=True, tracking=True)
    currency_id = fields.Many2one('res.currency', string='Currency', required=True, 
                                   default=lambda self: self.env.company.currency_id)
    
    # Donation details
    campaign_id = fields.Many2one('donation.campaign', string='Campaign', tracking=True)
    category_id = fields.Many2one(
        'donation.category',
        string='Donation Category',
        tracking=True,
        help='Maps this donation to the configured income account on the invoice.',
    )
    purpose = fields.Text(string='Purpose/Message')
    is_anonymous = fields.Boolean(string='Anonymous Donation', default=False)
    
    # Payment information
    payment_transaction_id = fields.Many2one('payment.transaction', string='Payment Transaction', readonly=True)
    payment_token_id = fields.Many2one('payment.token', string='Payment Token', 
                                        help='Saved payment method for recurring donations')
    payment_state = fields.Selection(related='payment_transaction_id.state', string='Payment Status', store=True)
    
    # Recurring donation fields
    is_recurring = fields.Boolean(string='Recurring Donation', default=False)
    recurring_rule_id = fields.Many2one('donation.recurring.rule', string='Recurring Rule', readonly=True,
                                        help='Parent recurring rule if this is an automated donation')
    frequency = fields.Selection([
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('yearly', 'Yearly'),
    ], string='Frequency')
    next_donation_date = fields.Date(string='Next Donation Date')
    
    # Status and accounting
    state = fields.Selection([
        ('draft', 'Draft'),
        ('pending', 'Pending Payment'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='draft', required=True, tracking=True)
    
    invoice_id = fields.Many2one('account.move', string='Invoice', readonly=True)
    tax_receipt_number = fields.Char(string='Tax Receipt Number', readonly=True, copy=False)
    
    # Portal access
    access_token = fields.Char('Access Token', copy=False)
    
    def _compute_access_url(self):
        super()._compute_access_url()
        for donation in self:
            donation.access_url = f'/my/donations/{donation.id}'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('donation.donation') or 'New'
        donations = super().create(vals_list)
        
        # Create recurring rule if needed
        for donation in donations:
            if donation.is_recurring and not donation.recurring_rule_id and donation.payment_token_id:
                donation._create_recurring_rule()
        
        return donations

    def _create_recurring_rule(self):
        """Create a recurring donation rule"""
        self.ensure_one()
        
        next_date = self._calculate_next_date(self.date.date())
        
        rule = self.env['donation.recurring.rule'].create({
            'partner_id': self.partner_id.id,
            'company_id': self.company_id.id or self.env.company.id,
            'amount': self.amount,
            'currency_id': self.currency_id.id,
            'frequency': self.frequency,
            'next_donation_date': next_date,
            'payment_token_id': self.payment_token_id.id,
            'campaign_id': self.campaign_id.id if self.campaign_id else False,
            'category_id': self.category_id.id if self.category_id else False,
            'purpose': self.purpose,
            'is_anonymous': self.is_anonymous,
            'state': 'active',
            'source_donation_id': self.id,
        })
        
        self.recurring_rule_id = rule.id
        return rule

    def _calculate_next_date(self, from_date):
        """Calculate next donation date based on frequency"""
        if self.frequency == 'monthly':
            return from_date + relativedelta(months=1)
        elif self.frequency == 'quarterly':
            return from_date + relativedelta(months=3)
        elif self.frequency == 'yearly':
            return from_date + relativedelta(years=1)
        return False

    def action_confirm(self):
        """Confirm the donation"""
        for donation in self:
            if donation.state not in ('draft', 'pending'):
                continue

            donation.write({
                'state': 'confirmed',
            })
            
            # Generate tax receipt if needed
            if not donation.tax_receipt_number:
                donation.tax_receipt_number = self.env['ir.sequence'].next_by_code('donation.tax.receipt')
            
            # Create invoice if accounting integration is needed
            donation._create_invoice()
            
            # Send confirmation email
            donation._send_confirmation_email()

    def action_cancel(self):
        """Cancel the donation"""
        self.write({'state': 'cancelled'})
        
        # Deactivate recurring rule if exists
        if self.recurring_rule_id:
            self.recurring_rule_id.action_deactivate()

    def _create_invoice(self):
        """Create invoice for the donation (optional - for accounting purposes)"""
        self.ensure_one()
        
        if self.invoice_id:
            return self.invoice_id
        
        # Get donation product (should be configured in settings)
        donation_product = self.env.ref('donation_management.product_donation', raise_if_not_found=False)
        if not donation_product:
            return False
        
        line_vals = {
            'product_id': donation_product.id,
            'name': f'Donation - {self.name}',
            'quantity': 1,
            'price_unit': self.amount,
        }
        if self.category_id and self.category_id.account_id:
            line_vals['account_id'] = self.category_id.account_id.id

        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'company_id': self.company_id.id or self.env.company.id,
            'partner_id': self.partner_id.id,
            'invoice_date': self.date.date(),
            'currency_id': self.currency_id.id,
            'invoice_line_ids': [(0, 0, line_vals)],
        })
        
        self.invoice_id = invoice.id
        return invoice

    def _send_confirmation_email(self):
        """Send donation confirmation email to donor"""
        self.ensure_one()
        
        template = self.env.ref('donation_management.email_template_donation_confirmation', raise_if_not_found=False)
        if template:
            template.send_mail(self.id, force_send=True)

    def _get_portal_return_action(self):
        """Return action for portal"""
        return {
            'type': 'ir.actions.act_url',
            'url': '/my/donations',
            'target': 'self',
        }


class DonationRecurringRule(models.Model):
    _name = 'donation.recurring.rule'
    _description = 'Recurring Donation Rule'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'next_donation_date'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default='New')
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        index=True,
    )
    partner_id = fields.Many2one('res.partner', string='Donor', required=True, tracking=True)
    amount = fields.Monetary(string='Amount', required=True)
    currency_id = fields.Many2one('res.currency', string='Currency', required=True)
    
    frequency = fields.Selection([
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('yearly', 'Yearly'),
    ], string='Frequency', required=True)
    
    next_donation_date = fields.Date(string='Next Donation Date', required=True, tracking=True)
    payment_token_id = fields.Many2one('payment.token', string='Payment Token', required=True,
                                        domain="[('partner_id', '=', partner_id)]")
    
    campaign_id = fields.Many2one('donation.campaign', string='Campaign')
    category_id = fields.Many2one('donation.category', string='Donation Category')
    purpose = fields.Text(string='Purpose/Message')
    is_anonymous = fields.Boolean(string='Anonymous Donation', default=False)
    
    state = fields.Selection([
        ('active', 'Active'),
        ('paused', 'Paused'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='active', required=True, tracking=True)
    
    source_donation_id = fields.Many2one('donation.donation', string='Original Donation', readonly=True)
    donation_ids = fields.One2many('donation.donation', 'recurring_rule_id', string='Generated Donations')
    donation_count = fields.Integer(string='Donation Count', compute='_compute_donation_count')
    
    @api.depends('donation_ids')
    def _compute_donation_count(self):
        for rule in self:
            rule.donation_count = len(rule.donation_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('donation.recurring.rule') or 'New'
        return super().create(vals_list)

    def action_process_recurring_donations(self):
        """Process all due recurring donations - called by cron"""
        today = fields.Date.today()
        
        rules = self.search([
            ('state', '=', 'active'),
            ('next_donation_date', '<=', today),
        ])
        
        for rule in rules:
            try:
                rule._create_recurring_donation()
            except Exception as e:
                # Log error but continue processing other rules
                rule.message_post(
                    body=f"Failed to process recurring donation: {str(e)}",
                    subject="Recurring Donation Error"
                )

    def _create_recurring_donation(self):
        """Create a new donation based on this recurring rule"""
        self.ensure_one()
        
        if self.state != 'active':
            return False
        
        # Create the donation
        donation = self.env['donation.donation'].create({
            'partner_id': self.partner_id.id,
            'company_id': self.company_id.id or self.env.company.id,
            'amount': self.amount,
            'currency_id': self.currency_id.id,
            'campaign_id': self.campaign_id.id if self.campaign_id else False,
            'category_id': self.category_id.id if self.category_id else False,
            'purpose': self.purpose,
            'is_anonymous': self.is_anonymous,
            'is_recurring': True,
            'recurring_rule_id': self.id,
            'payment_token_id': self.payment_token_id.id,
            'state': 'draft',
        })
        
        # Process payment
        payment_success = donation._process_token_payment()
        
        if payment_success:
            # Calculate next donation date
            next_date = self._calculate_next_date()
            self.next_donation_date = next_date
        else:
            # If payment fails, notify donor and optionally pause
            self._handle_payment_failure(donation)
        
        return donation

    def _calculate_next_date(self):
        """Calculate next donation date"""
        current_date = self.next_donation_date
        
        if self.frequency == 'monthly':
            return current_date + relativedelta(months=1)
        elif self.frequency == 'quarterly':
            return current_date + relativedelta(months=3)
        elif self.frequency == 'yearly':
            return current_date + relativedelta(years=1)
        
        return current_date

    def _handle_payment_failure(self, donation):
        """Handle failed payment for recurring donation"""
        self.message_post(
            body=f"Payment failed for recurring donation {donation.name}",
            subject="Recurring Donation Payment Failed"
        )
        
        # Send notification to donor
        template = self.env.ref('donation_management.email_template_recurring_payment_failed', raise_if_not_found=False)
        if template:
            template.send_mail(self.id, force_send=True)

    def action_pause(self):
        """Pause recurring donations"""
        self.write({'state': 'paused'})

    def action_resume(self):
        """Resume recurring donations"""
        self.write({'state': 'active'})

    def action_deactivate(self):
        """Cancel recurring donations"""
        self.write({'state': 'cancelled'})


class DonationCampaign(models.Model):
    _name = 'donation.campaign'
    _description = 'Donation Campaign'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Campaign Name', required=True, tracking=True)
    description = fields.Html(string='Description')
    goal_amount = fields.Monetary(string='Goal Amount', currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', string='Currency', required=True,
                                   default=lambda self: self.env.company.currency_id)
    
    start_date = fields.Date(string='Start Date')
    end_date = fields.Date(string='End Date')
    
    active = fields.Boolean(default=True)
    
    donation_ids = fields.One2many('donation.donation', 'campaign_id', string='Donation Lines')
    donation_count = fields.Integer(string='Donation Count', compute='_compute_donation_stats')
    total_raised = fields.Monetary(string='Total Raised', compute='_compute_donation_stats', currency_field='currency_id')
    progress_percentage = fields.Float(string='Progress %', compute='_compute_donation_stats')
    
    @api.depends('donation_ids.amount', 'donation_ids.state', 'goal_amount')
    def _compute_donation_stats(self):
        for campaign in self:
            confirmed_donations = campaign.donation_ids.filtered(lambda d: d.state == 'confirmed')
            campaign.donation_count = len(confirmed_donations)
            campaign.total_raised = sum(confirmed_donations.mapped('amount'))
            
            if campaign.goal_amount:
                campaign.progress_percentage = (campaign.total_raised / campaign.goal_amount) * 100
            else:
                campaign.progress_percentage = 0.0