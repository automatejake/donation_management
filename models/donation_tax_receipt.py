# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class DonationTaxReceipt(models.Model):
    """Year-end giving statements on top of OCA donation_base's annual tax receipts."""
    _inherit = 'donation.tax.receipt'

    donation_ids = fields.One2many(
        'donation.donation', 'tax_receipt_id', string='Donations', readonly=True,
    )
    period_start_date = fields.Date(
        string='Period Start',
        help='First day covered by a year-end statement. The period ends on the Donation Date.',
    )

    @api.model
    def update_tax_receipt_annual_dict(self, tax_receipt_annual_dict, start_date, end_date, company):
        """Feed confirmed donations into the annual receipt wizard, one statement per donor."""
        res = super().update_tax_receipt_annual_dict(
            tax_receipt_annual_dict, start_date, end_date, company
        )
        donations = self.env['donation.donation'].search([
            ('state', '=', 'confirmed'),
            ('company_id', '=', company.id),
            ('date', '>=', fields.Datetime.to_datetime(start_date)),
            ('date', '<', fields.Datetime.to_datetime(end_date + timedelta(days=1))),
            ('tax_receipt_id', '=', False),
        ], order='date, id')
        for donation in donations:
            partner = donation.partner_id.commercial_partner_id
            if partner.tax_receipt_option == 'none':
                continue
            amount = donation.currency_id._convert(
                donation.amount, company.currency_id, company, fields.Date.to_date(donation.date),
            )
            if company.currency_id.is_zero(amount):
                continue
            if partner not in tax_receipt_annual_dict:
                tax_receipt_annual_dict[partner] = {
                    'amount': amount,
                    'extra_vals': {'donation_ids': [(6, 0, [donation.id])]},
                }
            else:
                tax_receipt_annual_dict[partner]['amount'] += amount
                tax_receipt_annual_dict[partner]['extra_vals']['donation_ids'][0][2].append(donation.id)
        return res

    def _get_statement_template(self):
        return self.env.ref(
            'donation_management.mail_template_year_end_statement', raise_if_not_found=False,
        )

    def action_send_tax_receipt(self):
        action = super().action_send_tax_receipt()
        template = self._get_statement_template()
        if self.type == 'annual' and template:
            action['context'].update({
                'default_template_id': template.id,
                'default_use_template': True,
            })
        return action

    def action_email_statements(self):
        """Email each selected year-end statement to its donor, with the PDF attached."""
        template = self._get_statement_template()
        if not template:
            raise UserError(_('The year-end statement email template is missing.'))
        statements = self.filtered(lambda r: r.type == 'annual')
        sent = statements.filtered(lambda r: r.partner_id.email)
        for statement in sent:
            template.send_mail(statement.id, email_layout_xmlid='mail.mail_notification_light')
        skipped = statements - sent
        message = _('%(count)s statement(s) queued for email.', count=len(sent))
        if skipped:
            message += ' ' + _(
                'No email address for: %(names)s. Print these instead.',
                names=', '.join(skipped.mapped('partner_id.display_name')),
            )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Year-End Statements'),
                'message': message,
                'type': 'warning' if skipped or not sent else 'success',
                'sticky': bool(skipped),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
