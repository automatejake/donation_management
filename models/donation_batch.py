# -*- coding: utf-8 -*-
from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError
from datetime import datetime, time

from odoo.tools import float_compare, float_is_zero
from odoo.tools.misc import formatLang

PAYMENT_KIND_SELECTION = [
    ('check', 'Check'),
    ('cash', 'Cash'),
]


class DonationBatch(models.Model):
    """One Sunday's offering: every envelope, check and loose cash counted together.

    Posting makes one donation per line (so each donor's history and year-end
    statement include it) and a single journal entry whose debit is the whole
    deposit, so the bank feed's one deposit line matches it in one step.
    """
    _name = 'donation.batch'
    _description = 'Offering Batch'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default='New')
    label = fields.Char(
        string='Batch Name',
        help='For example "Sunday offering" or "Feast day collection".',
    )
    date = fields.Date(string='Date', required=True, default=fields.Date.context_today, tracking=True)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(related='company_id.currency_id', string='Currency')
    journal_id = fields.Many2one(
        'account.journal',
        string='Deposit To',
        required=True,
        check_company=True,
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id)]",
        default=lambda self: self._default_journal(),
        help='The bank account the money is deposited into.',
    )
    default_category_id = fields.Many2one(
        'donation.category',
        string='Default Fund',
        help='Fund filled in on each new line. Change it on a line when an envelope is marked for another fund.',
    )
    counted_total = fields.Monetary(
        string='Counted Total',
        currency_field='currency_id',
        tracking=True,
        help='The total written on the counting sheet. The batch can only be posted when the lines add up to it.',
    )
    line_ids = fields.One2many('donation.batch.line', 'batch_id', string='Gifts', copy=True)
    entered_total = fields.Monetary(
        string='Entered Total', currency_field='currency_id', compute='_compute_totals', store=True,
    )
    difference = fields.Monetary(
        string='Difference', currency_field='currency_id', compute='_compute_totals', store=True,
    )
    line_count = fields.Integer(string='Gifts', compute='_compute_totals', store=True)
    check_total = fields.Monetary(
        string='Checks', currency_field='currency_id', compute='_compute_totals', store=True,
    )
    cash_total = fields.Monetary(
        string='Cash', currency_field='currency_id', compute='_compute_totals', store=True,
    )
    state = fields.Selection(
        [('draft', 'Draft'), ('posted', 'Posted'), ('cancelled', 'Cancelled')],
        string='Status', default='draft', required=True, copy=False, tracking=True,
    )
    move_id = fields.Many2one('account.move', string='Deposit Entry', readonly=True, copy=False)
    donation_ids = fields.One2many('donation.donation', 'batch_id', string='Donations', readonly=True)
    donation_count = fields.Integer(compute='_compute_donation_count')

    @api.model
    def _default_journal(self):
        return self.env['account.journal'].search([
            ('company_id', '=', self.env.company.id),
            ('type', '=', 'bank'),
        ], limit=1)

    @api.depends('line_ids.amount', 'line_ids.payment_kind', 'counted_total')
    def _compute_totals(self):
        for batch in self:
            lines = batch.line_ids
            batch.entered_total = sum(lines.mapped('amount'))
            batch.check_total = sum(lines.filtered(lambda l: l.payment_kind == 'check').mapped('amount'))
            batch.cash_total = sum(lines.filtered(lambda l: l.payment_kind == 'cash').mapped('amount'))
            batch.line_count = len(lines)
            batch.difference = (batch.counted_total - batch.entered_total) if batch.counted_total else 0.0

    @api.depends('donation_ids')
    def _compute_donation_count(self):
        for batch in self:
            batch.donation_count = len(batch.donation_ids)

    @api.depends('name', 'label')
    def _compute_display_name(self):
        for batch in self:
            batch.display_name = f'{batch.name} {batch.label}' if batch.label else batch.name

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('donation.batch') or 'New'
        return super().create(vals_list)

    def unlink(self):
        if any(batch.state == 'posted' for batch in self):
            raise UserError(_('A posted batch cannot be deleted. Cancel it first.'))
        return super().unlink()

    # ------------------------------------------------------------------
    # Posting
    # ------------------------------------------------------------------

    def _check_ready_to_post(self):
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_('Only a draft batch can be posted.'))
        if not self.line_ids:
            raise UserError(_('Add at least one gift before posting the batch.'))
        rounding = self.currency_id.rounding
        bad = self.line_ids.filtered(lambda l: float_compare(l.amount, 0.0, precision_rounding=rounding) <= 0)
        if bad:
            raise UserError(_('Every gift needs an amount above zero (line %s).', bad[0].sequence_number))
        if self.counted_total and not float_is_zero(self.difference, precision_rounding=rounding):
            raise UserError(_(
                'The gifts add up to %(entered)s but the counted total is %(counted)s. '
                'Find the difference of %(diff)s before posting.',
                entered=self._fmt(self.entered_total),
                counted=self._fmt(self.counted_total),
                diff=self._fmt(abs(self.difference)),
            ))

    def _fmt(self, amount):
        return formatLang(self.env, amount, currency_obj=self.currency_id)

    def _anonymous_donor(self):
        partner = self.env.ref('donation_management.partner_anonymous_donor', raise_if_not_found=False)
        if not partner:
            raise UserError(_('The "Anonymous" donor record is missing. Upgrade the Donations app.'))
        return partner

    def _create_deposit_move(self, line_partners):
        """One debit for the whole deposit; one credit per gift, to its fund's income account."""
        self.ensure_one()
        donation_model = self.env['donation.donation']
        probe = donation_model.new({'company_id': self.company_id.id, 'currency_id': self.currency_id.id})
        outstanding_account = probe._get_outstanding_receipts_account(self.journal_id)
        label = _('Offering %s', self.display_name)
        credit_lines = []
        for line in self.line_ids:
            revenue_account = donation_model.new({
                'category_id': line.category_id.id,
            })._get_revenue_account()
            if not revenue_account:
                raise UserError(_(
                    'Fund "%(fund)s" has no income account. Set one under Donations > Configuration > Donation Categories.',
                    fund=line.category_id.display_name or _('(none)'),
                ))
            credit_lines.append(Command.create({
                'name': line.memo or label,
                'partner_id': line_partners[line.id].id,
                'account_id': revenue_account.id,
                'credit': line.amount,
                'debit': 0.0,
            }))
        debit_line = Command.create({
            'name': label,
            'account_id': outstanding_account.id,
            'debit': self.entered_total,
            'credit': 0.0,
        })
        move = self.env['account.move'].with_company(self.company_id).create({
            'move_type': 'entry',
            'journal_id': self.journal_id.id,
            'date': self.date,
            'ref': self.display_name,
            'line_ids': [debit_line] + credit_lines,
        })
        move.action_post()
        return move

    def action_post(self):
        for batch in self:
            batch._check_ready_to_post()
            anonymous = batch._anonymous_donor()
            line_partners = {line.id: line.partner_id or anonymous for line in batch.line_ids}
            move = batch._create_deposit_move(line_partners)
            donation_vals = []
            for line in batch.line_ids:
                donation_vals.append({
                    'partner_id': line_partners[line.id].id,
                    # Noon, so the gift shows on the batch's date in any US time zone.
                    'date': datetime.combine(batch.date, time(12, 0)),
                    'amount': line.amount,
                    'currency_id': batch.currency_id.id,
                    'company_id': batch.company_id.id,
                    'category_id': line.category_id.id,
                    'campaign_id': line.campaign_id.id,
                    'journal_id': batch.journal_id.id,
                    'purpose': line.memo,
                    'payment_kind': line.payment_kind,
                    'check_number': line.check_number,
                    'batch_id': batch.id,
                    'account_move_id': move.id,
                })
            # Envelope gifts get their receipt number and appear on the year-end
            # statement; no thank-you email per envelope.
            self.env['donation.donation'].with_context(skip_donation_email=True).create(donation_vals)
            batch.write({'state': 'posted', 'move_id': move.id})
        return True

    def action_cancel(self):
        for batch in self:
            if batch.move_id and batch.move_id.state == 'posted':
                batch.move_id.button_draft()
                batch.move_id.button_cancel()
            batch.donation_ids.with_context(skip_donation_email=True).write({'state': 'cancelled'})
            batch.write({'state': 'cancelled'})
        return True

    def action_reset_to_draft(self):
        for batch in self:
            if batch.state != 'cancelled':
                raise UserError(_('Only a cancelled batch can go back to draft.'))
            batch.donation_ids.write({'batch_id': False})
            batch.write({'state': 'draft', 'move_id': False})
        return True

    def action_view_donations(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Donations'),
            'res_model': 'donation.donation',
            'view_mode': 'list,form',
            'domain': [('batch_id', '=', self.id)],
        }

    def action_view_move(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.move_id.id,
        }


class DonationBatchLine(models.Model):
    _name = 'donation.batch.line'
    _description = 'Offering Batch Gift'
    _order = 'sequence, id'

    batch_id = fields.Many2one('donation.batch', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    sequence_number = fields.Integer(string='#', compute='_compute_sequence_number')
    company_id = fields.Many2one(related='batch_id.company_id', store=True)
    currency_id = fields.Many2one(related='batch_id.currency_id')
    partner_id = fields.Many2one(
        'res.partner',
        string='Donor',
        help='Leave empty for loose cash with no name; it is recorded as Anonymous.',
    )
    category_id = fields.Many2one(
        'donation.category',
        string='Fund',
        required=True,
        default=lambda self: self._default_category(),
    )
    campaign_id = fields.Many2one('donation.campaign', string='Campaign')
    payment_kind = fields.Selection(PAYMENT_KIND_SELECTION, string='Type', required=True, default='check')
    check_number = fields.Char(string='Check #')
    amount = fields.Monetary(string='Amount', currency_field='currency_id')
    memo = fields.Char(string='Memo')

    @api.model
    def _default_category(self):
        category_id = self.env.context.get('batch_default_category_id')
        if category_id:
            return category_id
        return self.env['donation.category'].search([], limit=1)

    @api.depends('batch_id.line_ids')
    def _compute_sequence_number(self):
        for batch in self.mapped('batch_id'):
            for number, line in enumerate(batch.line_ids, start=1):
                line.sequence_number = number
        for line in self.filtered(lambda l: not l.batch_id):
            line.sequence_number = 0

    @api.onchange('payment_kind')
    def _onchange_payment_kind(self):
        if self.payment_kind == 'cash':
            self.check_number = False
