# -*- coding: utf-8 -*-
from odoo import models, fields, api, _, Command
from odoo.exceptions import UserError
from dateutil.relativedelta import relativedelta

DONATION_FREQUENCY_SELECTION = [
    ('one_time', 'One Time'),
    ('weekly', 'Weekly'),
    ('biweekly', 'Bi-weekly'),
    ('monthly', 'Monthly'),
]


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
    base_amount = fields.Monetary(
        string='Donation Amount',
        help='Amount before optional fee coverage.',
    )
    cover_fees = fields.Boolean(string='Donor Covered Fees', default=False)
    fee_amount = fields.Monetary(string='Fee Amount', default=0.0)
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        required=True,
        default=lambda self: self.env.company.currency_id,
    )

    campaign_id = fields.Many2one('donation.campaign', string='Campaign', tracking=True)
    category_id = fields.Many2one(
        'donation.category',
        string='Donation Category',
        tracking=True,
        help='Revenue is posted to the income account configured on this category.',
    )
    purpose = fields.Text(string='Purpose/Message')

    journal_id = fields.Many2one(
        'account.journal',
        string='Journal',
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id)]",
        check_company=True,
        help='Bank or cash journal used to record check and cash donations.',
    )
    payment_transaction_id = fields.Many2one(
        'payment.transaction', string='Payment Transaction', readonly=True, copy=False,
    )
    account_move_id = fields.Many2one(
        'account.move',
        string='Journal Entry',
        readonly=True,
        copy=False,
        help='Posted entry: debit outstanding receipts, credit revenue.',
    )
    payment_token_id = fields.Many2one(
        'payment.token',
        string='Payment Token',
        help='Saved payment method for recurring donations.',
    )

    frequency = fields.Selection(
        selection=DONATION_FREQUENCY_SELECTION,
        string='Schedule',
        default='one_time',
        required=True,
        tracking=True,
    )
    is_recurring = fields.Boolean(
        string='Recurring Donation',
        compute='_compute_is_recurring',
        store=True,
    )
    recurring_start_date = fields.Date(
        string='Recurring Start Date',
        help='Date of the first automatic charge for this recurring donation.',
    )
    recurring_rule_id = fields.Many2one(
        'donation.recurring.rule',
        string='Recurring Rule',
        readonly=True,
        help='Parent recurring rule if this is an automated donation.',
    )
    next_donation_date = fields.Date(
        string='Next Donation Date',
        related='recurring_rule_id.next_donation_date',
        readonly=True,
    )

    state = fields.Selection([
        ('draft', 'Draft'),
        ('pending', 'Pending Payment'),
        ('scheduled', 'Scheduled'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='draft', required=True, tracking=True)

    tax_receipt_number = fields.Char(string='Tax Receipt Number', readonly=True, copy=False)
    access_token = fields.Char('Access Token', copy=False)

    @api.depends('frequency')
    def _compute_is_recurring(self):
        for donation in self:
            donation.is_recurring = donation.frequency != 'one_time'

    def _compute_access_url(self):
        super()._compute_access_url()
        for donation in self:
            donation.access_url = f'/my/donations/{donation.id}'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('donation.donation') or 'New'
            if not vals.get('base_amount') and vals.get('amount'):
                vals['base_amount'] = vals['amount']
        donations = super().create(vals_list)
        for donation in donations:
            if donation._is_manual_donation():
                donation._complete_donation()
        return donations

    def _is_manual_donation(self):
        """Backend cash/check donation (not created from the website payment flow)."""
        self.ensure_one()
        return (
            not self.env.context.get('from_website')
            and not self.payment_transaction_id
        )

    def _recurring_starts_later(self):
        """True when the first charge should wait until the selected start date."""
        self.ensure_one()
        start = self.recurring_start_date or fields.Date.context_today(self)
        return start > fields.Date.context_today(self)

    def _create_recurring_rule(self, first_charge_completed=False):
        self.ensure_one()
        if not self.is_recurring or self.recurring_rule_id:
            return False
        if not self.payment_token_id:
            return False

        start = self.recurring_start_date or fields.Date.context_today(self)
        if first_charge_completed:
            next_date = self._calculate_next_date(start)
        else:
            next_date = start

        rule = self.env['donation.recurring.rule'].create({
            'partner_id': self.partner_id.id,
            'company_id': self.company_id.id or self.env.company.id,
            'amount': self.base_amount or self.amount,
            'currency_id': self.currency_id.id,
            'frequency': self.frequency,
            'next_donation_date': next_date,
            'payment_token_id': self.payment_token_id.id,
            'campaign_id': self.campaign_id.id if self.campaign_id else False,
            'category_id': self.category_id.id if self.category_id else False,
            'purpose': self.purpose,
            'cover_fees': self.cover_fees,
            'state': 'active',
            'source_donation_id': self.id,
        })
        self.recurring_rule_id = rule.id
        return rule

    def _calculate_next_date(self, from_date):
        if self.frequency == 'weekly':
            return from_date + relativedelta(weeks=1)
        if self.frequency == 'biweekly':
            return from_date + relativedelta(weeks=2)
        if self.frequency == 'monthly':
            return from_date + relativedelta(months=1)
        return False

    def _complete_donation(self):
        """Mark confirmed, post accounting (receipts / revenue), send receipt email."""
        self.ensure_one()
        if self.state == 'cancelled':
            return

        vals = {'state': 'confirmed'}
        if not self.tax_receipt_number:
            vals['tax_receipt_number'] = (
                self.env['ir.sequence'].next_by_code('donation.tax.receipt') or False
            )
        if self.state != 'confirmed' or vals.get('tax_receipt_number'):
            self.with_context(skip_donation_complete=True).write(vals)

        if not self.account_move_id:
            move = self._create_donation_account_move()
            self.with_context(skip_donation_complete=True).write({'account_move_id': move.id})

        if not self.env.context.get('skip_donation_email'):
            self._send_confirmation_email()

    def action_cancel(self):
        for donation in self:
            if donation.account_move_id and donation.account_move_id.state == 'posted':
                donation.account_move_id.button_draft()
                donation.account_move_id.button_cancel()
        self.write({'state': 'cancelled'})
        if self.recurring_rule_id:
            self.recurring_rule_id.action_deactivate()

    def _get_revenue_account(self):
        self.ensure_one()
        if self.category_id.account_id:
            return self.category_id.account_id
        product = self.env.ref('donation_management.product_donation', raise_if_not_found=False)
        if product:
            account = product.property_account_income_id or product.categ_id.property_account_income_categ_id
            if account:
                return account
        return self.env['account.account']

    def _get_donation_journal(self):
        self.ensure_one()
        if self.journal_id:
            return self.journal_id
        tx = self.payment_transaction_id
        if tx and tx.provider_id.journal_id:
            return tx.provider_id.journal_id
        return self.env['account.journal'].search([
            ('company_id', '=', self.company_id.id or self.env.company.id),
            ('type', 'in', ('bank', 'cash')),
        ], limit=1)

    def _get_outstanding_receipts_account(self, journal):
        self.ensure_one()
        method_line = journal.inbound_payment_method_line_ids[:1]
        if not method_line or not method_line.payment_account_id:
            raise UserError(_(
                'Journal "%(journal)s" has no inbound payment method with an outstanding receipts account. '
                'Configure it under Accounting → Configuration → Journals.',
                journal=journal.display_name,
            ))
        return method_line.payment_account_id

    def _create_donation_account_move(self):
        """Post debit outstanding receipts / credit revenue (no invoice, no AR)."""
        self.ensure_one()
        if self.account_move_id:
            return self.account_move_id

        revenue_account = self._get_revenue_account()
        if not revenue_account:
            raise UserError(_(
                'Set a donation category with an income account before recording this donation.',
            ))

        journal = self._get_donation_journal()
        if not journal:
            raise UserError(_(
                'Select a bank or cash journal on the donation, or configure one for company %(company)s.',
                company=(self.company_id or self.env.company).display_name,
            ))

        outstanding_account = self._get_outstanding_receipts_account(journal)
        move_date = fields.Date.to_date(self.date)
        label = _('Donation %s', self.name)
        company = self.company_id or self.env.company
        amount_currency = self.amount
        balance = self.currency_id._convert(
            amount_currency,
            company.currency_id,
            company,
            move_date,
        )

        line_vals = []
        liquidity_vals = {
            'name': label,
            'partner_id': self.partner_id.id,
            'account_id': outstanding_account.id,
            'debit': balance if balance > 0 else 0.0,
            'credit': -balance if balance < 0 else 0.0,
        }
        revenue_vals = {
            'name': label,
            'partner_id': self.partner_id.id,
            'account_id': revenue_account.id,
            'debit': -balance if balance < 0 else 0.0,
            'credit': balance if balance > 0 else 0.0,
        }
        if self.currency_id != company.currency_id:
            liquidity_vals.update({
                'currency_id': self.currency_id.id,
                'amount_currency': amount_currency,
            })
            revenue_vals.update({
                'currency_id': self.currency_id.id,
                'amount_currency': -amount_currency,
            })
        line_vals.extend([liquidity_vals, revenue_vals])

        move = self.env['account.move'].with_company(company).create({
            'move_type': 'entry',
            'journal_id': journal.id,
            'date': move_date,
            'ref': self.name,
            'partner_id': self.partner_id.id,
            'currency_id': self.currency_id.id,
            'line_ids': [Command.create(vals) for vals in line_vals],
        })
        move.action_post()
        return move

    def _send_confirmation_email(self):
        self.ensure_one()
        template = self.env.ref(
            'donation_management.email_template_donation_confirmation',
            raise_if_not_found=False,
        )
        if template:
            template.send_mail(self.id, force_send=True)

    def _send_recurring_scheduled_email(self):
        self.ensure_one()
        template = self.env.ref(
            'donation_management.email_template_recurring_scheduled',
            raise_if_not_found=False,
        )
        if template:
            template.send_mail(self.id, force_send=True)

    def _is_recurring_signup(self):
        """Website recurring enrollment not yet linked to a rule."""
        self.ensure_one()
        return (
            self.is_recurring
            and not self.recurring_rule_id
            and bool(self.recurring_start_date)
        )

    def _finalize_recurring_setup(self):
        """Save payment method; first charge on a future start date."""
        self.ensure_one()
        if not self.payment_token_id:
            raise UserError(_('A saved payment method is required for recurring donations.'))
        if not self.recurring_start_date:
            raise UserError(_('A recurring start date is required.'))

        self._create_recurring_rule(first_charge_completed=False)
        self.write({'state': 'scheduled'})
        self._send_recurring_scheduled_email()
        if self.recurring_rule_id:
            self.recurring_rule_id.message_post(
                body=_(
                    'Recurring donation scheduled. First charge on %(date)s, then %(frequency)s.',
                    date=self.recurring_start_date,
                    frequency=dict(self._fields['frequency'].selection).get(self.frequency, self.frequency),
                ),
            )

    def _finalize_recurring_with_first_charge(self):
        """Charge now (start date is today), then schedule the next charge per frequency."""
        self.ensure_one()
        if not self.payment_token_id:
            raise UserError(_('A saved payment method is required for recurring donations.'))
        self._complete_donation()
        self._create_recurring_rule(first_charge_completed=True)
        if self.recurring_rule_id:
            self.recurring_rule_id.message_post(
                body=_(
                    'First donation received. Next charge scheduled for %(date)s (%(frequency)s).',
                    date=self.recurring_rule_id.next_donation_date,
                    frequency=dict(self._fields['frequency'].selection).get(self.frequency, self.frequency),
                ),
            )

    def _get_portal_return_action(self):
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
    cover_fees = fields.Boolean(string='Donor Covered Fees', default=False)
    currency_id = fields.Many2one('res.currency', string='Currency', required=True)

    frequency = fields.Selection(
        selection=[f for f in DONATION_FREQUENCY_SELECTION if f[0] != 'one_time'],
        string='Frequency',
        required=True,
    )

    next_donation_date = fields.Date(string='Next Donation Date', required=True, tracking=True)
    payment_token_id = fields.Many2one(
        'payment.token',
        string='Payment Token',
        required=True,
        domain="[('partner_id', '=', partner_id)]",
    )

    campaign_id = fields.Many2one('donation.campaign', string='Campaign')
    category_id = fields.Many2one('donation.category', string='Donation Category')
    purpose = fields.Text(string='Purpose/Message')

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
        today = fields.Date.today()
        rules = self.search([
            ('state', '=', 'active'),
            ('next_donation_date', '<=', today),
        ])
        for rule in rules:
            try:
                rule._create_recurring_donation()
            except Exception as e:
                rule.message_post(
                    body=f"Failed to process recurring donation: {str(e)}",
                    subject="Recurring Donation Error",
                )

    def _create_recurring_donation(self):
        self.ensure_one()
        if self.state != 'active':
            return False

        amount = self.amount
        if self.cover_fees:
            fee_pct = self.payment_token_id.provider_id.get_donation_fee_coverage_percent()
            amount = round(self.amount * (1 + fee_pct / 100), 2)

        donation = self.env['donation.donation'].with_context(from_website=True).create({
            'partner_id': self.partner_id.id,
            'company_id': self.company_id.id or self.env.company.id,
            'amount': amount,
            'base_amount': self.amount,
            'cover_fees': self.cover_fees,
            'fee_amount': amount - self.amount if self.cover_fees else 0.0,
            'currency_id': self.currency_id.id,
            'campaign_id': self.campaign_id.id if self.campaign_id else False,
            'category_id': self.category_id.id if self.category_id else False,
            'purpose': self.purpose,
            'frequency': self.frequency,
            'recurring_rule_id': self.id,
            'payment_token_id': self.payment_token_id.id,
            'state': 'draft',
        })

        if donation._process_token_payment():
            self.next_donation_date = self._calculate_next_date(self.next_donation_date)
        else:
            self._handle_payment_failure(donation)
        return donation

    def _calculate_next_date(self, from_date):
        if self.frequency == 'weekly':
            return from_date + relativedelta(weeks=1)
        if self.frequency == 'biweekly':
            return from_date + relativedelta(weeks=2)
        if self.frequency == 'monthly':
            return from_date + relativedelta(months=1)
        return from_date

    def _handle_payment_failure(self, donation):
        self.message_post(
            body=f"Payment failed for recurring donation {donation.name}",
            subject="Recurring Donation Payment Failed",
        )
        template = self.env.ref(
            'donation_management.email_template_recurring_payment_failed',
            raise_if_not_found=False,
        )
        if template:
            template.send_mail(self.id, force_send=True)

    def action_pause(self):
        self.write({'state': 'paused'})

    def action_resume(self):
        self.write({'state': 'active'})

    def action_deactivate(self):
        self.write({'state': 'cancelled'})


class DonationCampaign(models.Model):
    _name = 'donation.campaign'
    _description = 'Donation Campaign'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Campaign Name', required=True, tracking=True)
    description = fields.Html(string='Description')
    goal_amount = fields.Monetary(string='Goal Amount', currency_field='currency_id')
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        required=True,
        default=lambda self: self.env.company.currency_id,
    )

    start_date = fields.Date(string='Start Date')
    end_date = fields.Date(string='End Date')
    active = fields.Boolean(default=True)

    donation_ids = fields.One2many('donation.donation', 'campaign_id', string='Donation Lines')
    donation_count = fields.Integer(string='Donation Count', compute='_compute_donation_stats')
    total_raised = fields.Monetary(
        string='Total Raised',
        compute='_compute_donation_stats',
        currency_field='currency_id',
    )
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
