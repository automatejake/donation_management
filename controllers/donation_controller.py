# -*- coding: utf-8 -*-
import csv
import io
import json
import logging
import re
from calendar import monthrange
from datetime import datetime

from werkzeug.urls import url_encode

from odoo import http, fields, _
from odoo.http import content_disposition, request
from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.controllers.portal import PaymentPortal
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError

_logger = logging.getLogger(__name__)

VALID_FREQUENCIES = ('one_time', 'weekly', 'biweekly', 'monthly')


class DonationWebsiteController(http.Controller):

    def _payment_portal(self):
        """Payment portal helpers (_create_transaction needs a controller instance)."""
        return PaymentPortal()

    def _get_default_fee_coverage_percent(self):
        return float(request.env['ir.config_parameter'].sudo().get_param(
            'donation_management.fee_coverage_percent', '3.0',
        ))

    def _get_compatible_providers(self, partner, amount=None):
        amount = amount if amount is not None else 100.0
        return request.env['payment.provider'].sudo()._get_compatible_providers(
            request.env.company.id,
            partner.id if partner else request.env.user.partner_id.id,
            currency_id=request.env.company.currency_id.id,
            amount=amount,
        )

    def _own_payment_token(self, token_id):
        """The saved payment method ``token_id`` if it belongs to the logged-in user, else empty.

        Saved cards and bank accounts are only offered to, and usable by, the
        signed-in donor who owns them: an email address typed on the form is
        not proof of who is paying.
        """
        Token = request.env['payment.token'].sudo()
        user = request.env.user
        if user._is_public() or not token_id:
            return Token
        token = Token.browse(token_id).exists()
        if not token or token.partner_id.commercial_partner_id != user.partner_id.commercial_partner_id:
            return Token
        return token

    def _get_payment_tokens(self, partner, providers):
        if not partner or partner.is_public:
            return request.env['payment.token']
        return request.env['payment.token'].sudo()._get_available_tokens(
            providers.ids, partner.id,
        )

    def _parse_payment_method(self, post):
        """Return ('token', id), ('provider', id), or (None, None)."""
        payment_method = (post.get('payment_method') or '').strip()
        if payment_method.startswith('token_'):
            try:
                return 'token', int(payment_method[6:])
            except (TypeError, ValueError):
                return None, None
        if payment_method.startswith('method_'):
            try:
                return 'method', int(payment_method[7:])
            except (TypeError, ValueError):
                return None, None
        if payment_method.startswith('provider_'):
            try:
                return 'provider', int(payment_method[9:])
            except (TypeError, ValueError):
                return None, None
        if post.get('provider_id'):
            try:
                return 'provider', int(post.get('provider_id'))
            except (TypeError, ValueError):
                return None, None
        return None, None

    def _get_fee_percent_for_post(self, post):
        try:
            fee_pct = float(post.get('fee_percent', ''))
        except (TypeError, ValueError):
            fee_pct = 0.0
        if fee_pct > 0:
            return fee_pct

        method_type, method_id = self._parse_payment_method(post)
        if method_type == 'method' and method_id:
            pm = request.env['payment.method'].sudo().browse(method_id)
            if pm.exists():
                return pm.get_donation_fee_coverage_percent()
        if method_type == 'provider' and method_id:
            provider = request.env['payment.provider'].sudo().browse(method_id)
            if provider.exists():
                return provider.get_donation_fee_coverage_percent()
        if method_type == 'token' and method_id:
            token = self._own_payment_token(method_id)
            if token and token.payment_method_id:
                return token.payment_method_id.get_donation_fee_coverage_percent()
        return self._get_default_fee_coverage_percent()

    def _parse_amounts(self, post):
        """Return (total_amount, base_amount, fee_amount, cover_fees)."""
        try:
            base_amount = float(post.get('amount', 0))
        except (TypeError, ValueError):
            raise ValueError('invalid_amount')
        if base_amount <= 0:
            raise ValueError('invalid_amount')

        cover_fees = post.get('cover_fees') == 'on'
        fee_amount = 0.0
        total_amount = base_amount
        if cover_fees:
            fee_pct = self._get_fee_percent_for_post(post)
            fee_amount = round(base_amount * fee_pct / 100, 2)
            total_amount = round(base_amount + fee_amount, 2)
        return total_amount, base_amount, fee_amount, cover_fees

    @http.route('/donate', type='http', auth='public', website=True, sitemap=True)
    def donation_form(self, **kwargs):
        campaigns = request.env['donation.campaign'].sudo().search([
            ('active', '=', True),
            '|', ('end_date', '=', False), ('end_date', '>=', fields.Date.today()),
        ])
        categories = request.env['donation.category'].sudo().search([('active', '=', True)])

        values = {
            'campaigns': campaigns,
            'categories': categories,
            'user': request.env.user,
            'error': kwargs.get('error'),
            'success': kwargs.get('success'),
            'default_fee_percent': self._get_default_fee_coverage_percent(),
        }
        values.update(self._donation_payment_form_values())
        return request.render('donation_management.donation_form_page', values)

    def _donation_payment_form_values(self):
        """Values for Odoo's standard payment form (payment.form) on /donate.

        Every provider shows its own fields there, the same way as on an
        invoice or in the shop, so giving works with any provider.
        """
        user = request.env.user
        partner_sudo = user.partner_id.sudo()
        currency = request.env.company.currency_id
        providers_sudo = self._get_compatible_providers(partner_sudo)
        payment_methods_sudo = request.env['payment.method'].sudo()._get_compatible_payment_methods(
            providers_sudo.ids, partner_sudo.id, currency_id=currency.id,
        )
        tokens_sudo = self._get_payment_tokens(
            None if user._is_public() else partner_sudo, providers_sudo,
        )
        if user._is_public():
            # A guest's gift is filed under the email they type, which proves
            # nothing: they get no "save my details" box (recurring gifts save
            # the card anyway, to charge it on schedule).
            show_tokenize = {provider.id: False for provider in providers_sudo}
        else:
            show_tokenize = PaymentPortal._compute_show_tokenize_input_mapping(providers_sudo)
        fee_percents = {
            'payment_method_%s' % pm.id: round(pm.get_donation_fee_coverage_percent(), 2)
            for pm in payment_methods_sudo
        }
        fee_percents.update({
            'token_%s' % token.id: round(
                token.payment_method_id.get_donation_fee_coverage_percent(), 2,
            )
            for token in tokens_sudo
        })
        return {
            # payment.form context
            'mode': 'payment',
            'reference_prefix': 'DON',
            'amount': 0.0,
            'currency': currency,
            'partner_id': partner_sudo.id,
            'providers_sudo': providers_sudo,
            'payment_methods_sudo': payment_methods_sudo,
            'tokens_sudo': tokens_sudo,
            'transaction_route': '/donate/transaction',
            'landing_route': '/payment/confirmation',
            'access_token': payment_utils.generate_access_token(partner_sudo.id, 0.0, currency.id),
            'show_tokenize_input_mapping': show_tokenize,
            'submit_button_label': _('Donate Now'),
            'availability_report': {},
            # /donate
            'donation_fee_percents': json.dumps(fee_percents),
        }

    @http.route('/donate/tokens', type='json', auth='public')
    def donation_tokens(self, email=None):
        """Saved payment methods are no longer looked up by email: guests get none.

        Kept so pages cached before this change still load; signed-in donors
        get their own saved methods on the form itself.
        """
        return []

    def _validate_donation_post(self, post):
        """Validate donation form POST/JSON data.

        :return: dict of validated values.
        :raise ValueError: with error code as message for invalid input.
        """
        try:
            total_amount, base_amount, fee_amount, cover_fees = self._parse_amounts(post)
        except ValueError as exc:
            raise ValueError(str(exc) or 'invalid_amount') from exc

        frequency = post.get('frequency', 'one_time')
        if frequency not in VALID_FREQUENCIES:
            raise ValueError('invalid_frequency')

        recurring_start_date = False
        if frequency != 'one_time':
            start_raw = post.get('recurring_start_date', '').strip()
            if not start_raw:
                raise ValueError('missing_start_date')
            try:
                recurring_start_date = fields.Date.to_date(start_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError('invalid_start_date') from exc
            if recurring_start_date < fields.Date.today():
                raise ValueError('invalid_start_date')

        category_id = False
        if post.get('category_id'):
            try:
                raw_cat = int(post.get('category_id'))
            except (TypeError, ValueError) as exc:
                raise ValueError('invalid_category') from exc
            category = request.env['donation.category'].sudo().search([
                ('id', '=', raw_cat),
                ('active', '=', True),
            ], limit=1)
            if not category:
                raise ValueError('invalid_category')
            category_id = category.id

        campaign_id = False
        if post.get('campaign_id'):
            try:
                campaign_id = int(post.get('campaign_id'))
            except (TypeError, ValueError) as exc:
                raise ValueError('invalid_campaign') from exc

        return {
            'total_amount': total_amount,
            'base_amount': base_amount,
            'fee_amount': fee_amount,
            'cover_fees': cover_fees,
            'frequency': frequency,
            'recurring_start_date': recurring_start_date,
            'category_id': category_id,
            'campaign_id': campaign_id,
            'purpose': post.get('purpose', ''),
            'partner': self._get_or_create_partner(post),
        }

    def _create_donation_from_post(self, post):
        """Create a draft donation from validated form data."""
        validated = self._validate_donation_post(post)
        return request.env['donation.donation'].sudo().with_context(
            from_website=True,
        ).create({
            'partner_id': validated['partner'].id,
            'amount': validated['total_amount'],
            'base_amount': validated['base_amount'],
            'fee_amount': validated['fee_amount'],
            'cover_fees': validated['cover_fees'],
            'currency_id': request.env.company.currency_id.id,
            'campaign_id': validated['campaign_id'],
            'category_id': validated['category_id'],
            'purpose': validated['purpose'],
            'frequency': validated['frequency'],
            'recurring_start_date': validated['recurring_start_date'],
            'state': 'draft',
        })

    # Error codes from _validate_donation_post, as the donor reads them.
    def _donation_error_message(self, code):
        messages = {
            'invalid_amount': _('Please enter a valid donation amount.'),
            'invalid_category': _('Please select a valid donation category.'),
            'invalid_campaign': _('Please select a valid campaign.'),
            'invalid_frequency': _('Please select a valid donation schedule.'),
            'missing_start_date': _('Please choose a start date for your recurring donation.'),
            'invalid_start_date': _('Please enter a valid start date.'),
            'missing_name': _('Please enter your name.'),
            'missing_email': _('Please enter your email.'),
        }
        return messages.get(code, _('Please check the donation details and try again.'))

    @http.route('/donate/transaction', type='json', auth='public')
    def donation_transaction(
        self, donation_values=None, provider_id=None, payment_method_id=None,
        token_id=None, flow=None, tokenization_requested=False, **kwargs,
    ):
        """Create the gift and its payment, for Odoo's payment form on /donate.

        The amount comes from the gift (with fees when covered), never from
        the browser's payment form.
        """
        post = dict(donation_values) if isinstance(donation_values, dict) else {}
        if request.env.user._is_public():
            if not (post.get('name') or '').strip():
                raise ValidationError(self._donation_error_message('missing_name'))
            if not (post.get('email') or '').strip():
                raise ValidationError(self._donation_error_message('missing_email'))
        if flow == 'token':
            post['payment_method'] = 'token_%s' % (token_id or '')
        elif payment_method_id:
            post['payment_method'] = 'method_%s' % payment_method_id
        try:
            donation = self._create_donation_from_post(post)
        except ValueError as exc:
            raise ValidationError(self._donation_error_message(exc.args[0] if exc.args else ''))

        try:
            return self._donation_start_payment(
                donation, provider_id, payment_method_id, token_id, flow,
                tokenization_requested,
            )
        except (AccessError, ValidationError, UserError):
            donation.unlink()
            raise

    def _donation_start_payment(
        self, donation, provider_id, payment_method_id, token_id, flow, tokenization_requested,
    ):
        """Create the transaction for ``donation``; return its processing values."""
        partner = donation.partner_id
        starts_later = donation.is_recurring and donation._recurring_starts_later()
        token = request.env['payment.token']
        if flow == 'token':
            token = self._own_payment_token(token_id)
            if not token or token.provider_id not in self._get_compatible_providers(
                partner, donation.amount,
            ):
                raise ValidationError(_(
                    'The saved payment method could not be used. Please choose another one.'
                ))
            donation.write({'payment_token_id': token.id, 'state': 'pending'})
            if starts_later:
                # Nothing to charge today: the saved method is charged on the date.
                donation._finalize_recurring_setup()
                donation._portal_ensure_token()
                return {
                    'donation_redirect': '/donate/confirmation/%s?access_token=%s' % (
                        donation.id, donation.access_token,
                    ),
                }
            provider = token.provider_id
            payment_method = token.payment_method_id
        elif flow in ('direct', 'redirect'):
            providers = self._get_compatible_providers(partner, donation.amount)
            provider = providers.filtered(lambda p: p.id == provider_id)
            payment_method = request.env['payment.method'].sudo().browse(
                payment_method_id or 0,
            ).exists()
            if not provider or not payment_method or provider not in payment_method.provider_ids:
                raise ValidationError(_('Please select how you would like to pay.'))
            if donation.is_recurring and not (
                provider.allow_tokenization and payment_method.support_tokenization
            ):
                raise ValidationError(_(
                    'This payment method cannot be saved for a recurring gift. '
                    'Please choose another one, or give once.'
                ))
        else:
            raise ValidationError(_('Please select how you would like to pay.'))

        # Guests only save a card for a recurring gift (see the payment form values).
        tokenize = donation.is_recurring or (
            bool(tokenization_requested) and not request.env.user._is_public()
        )
        request.update_context(delay_payment_request=True)
        tx = self._payment_portal()._create_transaction(
            provider_id=provider.id,
            payment_method_id=payment_method.id,
            token_id=token.id or None,
            amount=donation.amount,
            currency_id=donation.currency_id.id,
            partner_id=partner.id,
            flow=flow,
            tokenization_requested=tokenize,
            landing_route='/payment/confirmation',
            reference_prefix=donation.name,
            is_validation=bool(starts_later and flow != 'token'),
            custom_create_values={'reference': donation.name},
        )
        donation.write({'payment_transaction_id': tx.id, 'state': 'pending'})
        PaymentPortal._update_landing_route(
            tx, payment_utils.generate_access_token(tx.partner_id.id, tx.amount, tx.currency_id.id),
        )
        if flow == 'token':
            tx._send_payment_request()
        return tx._get_processing_values()

    def _get_or_create_partner(self, post):
        if not request.env.user._is_public():
            return request.env.user.partner_id

        email = post.get('email', '').strip()
        if email:
            partner = request.env['res.partner'].sudo().search([
                ('email', '=', email),
            ], limit=1)
            if partner:
                return partner

        return request.env['res.partner'].sudo().create({
            'name': post.get('name', 'Donor'),
            'email': email,
        })

    def _redirect_to_confirmation(self, donation):
        donation_sudo = donation.sudo()
        donation_sudo._portal_ensure_token()
        return request.redirect(
            '/donate/confirmation/%s?access_token=%s' % (donation_sudo.id, donation_sudo.access_token)
        )

    @http.route('/donate/confirmation/<int:donation_id>', type='http', auth='public', website=True)
    def donation_confirmation(self, donation_id, access_token=None, **kwargs):
        try:
            donation = self._document_check_access('donation.donation', donation_id, access_token)
        except (AccessError, MissingError):
            return request.redirect('/donate?error=access_denied')

        return request.render('donation_management.donation_confirmation_page', {
            'donation': donation,
        })

    def _document_check_access(self, model_name, document_id, access_token=None):
        document = request.env[model_name].browse(document_id)
        if not document.exists():
            raise MissingError(_("This document does not exist."))
        if access_token:
            if not document.access_token or document.access_token != access_token:
                raise AccessError(_("Invalid access token."))
        elif not request.env.user._is_public():
            if document.partner_id != request.env.user.partner_id:
                raise AccessError(_("You do not have access to this document."))
        else:
            raise AccessError(_("You must be logged in to access this document."))
        return document


class DonationPortal(CustomerPortal):

    _DONATION_MONTHS = (
        (1, 'January'), (2, 'February'), (3, 'March'), (4, 'April'),
        (5, 'May'), (6, 'June'), (7, 'July'), (8, 'August'),
        (9, 'September'), (10, 'October'), (11, 'November'), (12, 'December'),
    )

    def _donation_portal_base_domain(self, partner):
        return [
            ('partner_id', '=', partner.id),
            ('state', '=', 'confirmed'),
        ]

    def _donation_portal_date_domain(self, filter_year=None, filter_month=None):
        if not filter_year:
            return []
        try:
            year = int(filter_year)
        except (TypeError, ValueError):
            return []
        if filter_month:
            try:
                month = int(filter_month)
            except (TypeError, ValueError):
                month = None
        else:
            month = None
        if month and 1 <= month <= 12:
            last_day = monthrange(year, month)[1]
            date_begin = datetime(year, month, 1, 0, 0, 0)
            date_end = datetime(year, month, last_day, 23, 59, 59)
        else:
            date_begin = datetime(year, 1, 1, 0, 0, 0)
            date_end = datetime(year, 12, 31, 23, 59, 59)
        return [('date', '>=', date_begin), ('date', '<=', date_end)]

    def _get_available_donation_years(self, partner):
        Donation = request.env['donation.donation'].sudo()
        dates = Donation.search(self._donation_portal_base_domain(partner)).mapped('date')
        years = sorted(
            {fields.Datetime.to_datetime(d).year for d in dates if d},
            reverse=True,
        )
        if not years:
            years = [fields.Date.today().year]
        return years

    def _donation_period_label(self, filter_year=None, filter_month=None):
        if not filter_year:
            return _('All time')
        try:
            year = int(filter_year)
        except (TypeError, ValueError):
            return _('All time')
        if filter_month:
            try:
                month = int(filter_month)
                if 1 <= month <= 12:
                    month_name = dict(self._DONATION_MONTHS).get(month, str(month))
                    return f'{month_name} {year}'
            except (TypeError, ValueError):
                pass
        return str(year)

    def _donation_portal_filter_args(self, sortby, filter_year, filter_month):
        return {
            'sortby': sortby,
            'filter_year': filter_year or '',
            'filter_month': filter_month or '',
        }

    def _prepare_donation_history(self, partner, sortby=None, filter_year=None, filter_month=None):
        Donation = request.env['donation.donation'].sudo()
        domain = self._donation_portal_base_domain(partner) + self._donation_portal_date_domain(
            filter_year, filter_month,
        )
        searchbar_sortings = {
            'date': {'label': _('Date'), 'order': 'date desc'},
            'amount': {'label': _('Amount'), 'order': 'amount desc'},
            'name': {'label': _('Reference'), 'order': 'name'},
        }
        if not sortby or sortby not in searchbar_sortings:
            sortby = 'date'
        return {
            'domain': domain,
            'order': searchbar_sortings[sortby]['order'],
            'searchbar_sortings': searchbar_sortings,
            'sortby': sortby,
            'filter_year': filter_year,
            'filter_month': filter_month,
            'available_years': self._get_available_donation_years(partner),
            'donation_months': self._DONATION_MONTHS,
            'period_label': self._donation_period_label(filter_year, filter_month),
            'Donation': Donation,
        }

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'donation_count' in counters:
            partner = request.env.user.partner_id
            confirmed_count = request.env['donation.donation'].search_count([
                ('partner_id', '=', partner.id),
                ('state', '=', 'confirmed'),
            ])
            recurring_count = request.env['donation.recurring.rule'].search_count([
                ('partner_id', '=', partner.id),
                ('state', 'in', ('active', 'paused')),
            ])
            values['donation_count'] = confirmed_count + recurring_count
        return values

    @http.route(['/my/donations', '/my/donations/page/<int:page>'], type='http', auth='user', website=True)
    def portal_my_donations(
        self, page=1, sortby=None, filter_year=None, filter_month=None, **kw,
    ):
        values = self._prepare_portal_layout_values()
        partner = request.env.user.partner_id
        history = self._prepare_donation_history(
            partner, sortby=sortby, filter_year=filter_year, filter_month=filter_month,
        )
        Donation = history['Donation']
        domain = history['domain']
        url_args = self._donation_portal_filter_args(
            history['sortby'], filter_year, filter_month,
        )

        donation_count = Donation.search_count(domain)
        pager = portal_pager(
            url="/my/donations",
            url_args=url_args,
            total=donation_count,
            page=page,
            step=self._items_per_page,
        )
        donations = Donation.search(
            domain,
            order=history['order'],
            limit=self._items_per_page,
            offset=pager['offset'],
        )
        filtered_donations = Donation.search(domain, order=history['order'])
        recurring_rules = request.env['donation.recurring.rule'].sudo().search([
            ('partner_id', '=', partner.id),
            ('state', 'in', ('active', 'paused')),
        ], order='state asc, next_donation_date asc, id desc')

        values.update({
            'donations': donations,
            'has_confirmed_donations': bool(Donation.search_count(
                self._donation_portal_base_domain(partner),
            )),
            'history_total_amount': sum(filtered_donations.mapped('amount')),
            'history_currency': filtered_donations[:1].currency_id or partner.currency_id,
            'history_count': int(donation_count),
            'recurring_rules': recurring_rules,
            'page_name': 'donation',
            'pager': pager,
            'default_url': '/my/donations',
            'searchbar_sortings': history['searchbar_sortings'],
            'sortby': history['sortby'],
            'filter_year': filter_year,
            'filter_month': filter_month,
            'available_years': history['available_years'],
            'donation_months': history['donation_months'],
            'period_label': history['period_label'],
        })
        return request.render('donation_management.portal_my_donations', values)

    @http.route(['/my/donations/export'], type='http', auth='user', website=True)
    def portal_my_donations_export(
        self, sortby=None, filter_year=None, filter_month=None, export_format='csv', **kw,
    ):
        partner = request.env.user.partner_id
        history = self._prepare_donation_history(
            partner, sortby=sortby, filter_year=filter_year, filter_month=filter_month,
        )
        donations = history['Donation'].search(domain=history['domain'], order=history['order'])
        period_slug = re.sub(r'[^\w\-]+', '_', history['period_label'].lower())
        partner_slug = re.sub(r'[^\w\-]+', '_', partner.name)
        filename_base = f'donation_history_{partner_slug}_{period_slug}'

        if export_format == 'pdf':
            if not donations:
                return request.redirect(
                    '/my/donations?' + url_encode(
                        self._donation_portal_filter_args(sortby, filter_year, filter_month),
                    ),
                )
            report = request.env.ref(
                'donation_management.action_report_donation_history',
                raise_if_not_found=False,
            )
            if not report:
                return request.redirect('/my/donations')
            # Donors can't read report records themselves; the gifts were already
            # limited to their own above.
            report = report.sudo()
            pdf_content, _report_format = report._render_qweb_pdf(
                report.report_name,
                donations.ids,
                data={'report_period_label': history['period_label']},
            )
            pdf_headers = [
                ('Content-Type', 'application/pdf'),
                ('Content-Length', len(pdf_content)),
                ('Content-Disposition', content_disposition(filename_base + '.pdf')),
            ]
            return request.make_response(pdf_content, headers=pdf_headers)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'Date', 'Reference', 'Campaign', 'Category', 'Amount', 'Currency',
            'Type', 'Tax Receipt', 'Fee Covered', 'Fee Amount',
        ])
        for donation in donations:
            writer.writerow([
                fields.Datetime.to_datetime(donation.date).strftime('%Y-%m-%d %H:%M:%S'),
                donation.name,
                donation.campaign_id.name or '',
                donation.category_id.name or '',
                donation.amount,
                donation.currency_id.name,
                'Recurring' if donation.is_recurring else 'One-time',
                donation.tax_receipt_number or '',
                'Yes' if donation.cover_fees else 'No',
                donation.fee_amount,
            ])
        csv_content = output.getvalue().encode('utf-8-sig')
        csv_headers = [
            ('Content-Type', 'text/csv; charset=utf-8'),
            ('Content-Disposition', content_disposition(filename_base + '.csv')),
        ]
        return request.make_response(csv_content, headers=csv_headers)

    @http.route(['/my/donations/<int:donation_id>'], type='http', auth='user', website=True)
    def portal_donation_detail(self, donation_id, access_token=None, **kw):
        try:
            donation_sudo = self._document_check_access(
                'donation.donation', donation_id, access_token=access_token,
            )
        except (AccessError, MissingError):
            return request.redirect('/my')
        return request.render('donation_management.portal_donation_detail', {
            'donation': donation_sudo,
            'page_name': 'donation',
        })

    @http.route(['/my/recurring_donations/<int:rule_id>/cancel'], type='http', auth='user', website=True, methods=['POST'], csrf=True)
    def portal_cancel_recurring(self, rule_id, **kw):
        rule = request.env['donation.recurring.rule'].sudo().search([
            ('id', '=', rule_id),
            ('partner_id', '=', request.env.user.partner_id.id),
        ], limit=1)
        if rule:
            rule.action_deactivate()
        return request.redirect('/my/donations')

    @http.route(['/my/recurring_donations/<int:rule_id>/pause'], type='http', auth='user', website=True, methods=['POST'], csrf=True)
    def portal_pause_recurring(self, rule_id, **kw):
        rule = request.env['donation.recurring.rule'].sudo().search([
            ('id', '=', rule_id),
            ('partner_id', '=', request.env.user.partner_id.id),
            ('state', '=', 'active'),
        ], limit=1)
        if rule:
            rule.action_pause()
        return request.redirect('/my/donations')

    @http.route(['/my/recurring_donations/<int:rule_id>/resume'], type='http', auth='user', website=True, methods=['POST'], csrf=True)
    def portal_resume_recurring(self, rule_id, **kw):
        rule = request.env['donation.recurring.rule'].sudo().search([
            ('id', '=', rule_id),
            ('partner_id', '=', request.env.user.partner_id.id),
            ('state', '=', 'paused'),
        ], limit=1)
        if rule:
            rule.action_resume()
        return request.redirect('/my/donations')
