# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import http, fields, _
from odoo.http import request
from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.controllers.portal import PaymentPortal
from odoo.addons.payment.controllers.post_processing import PaymentPostProcessing
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager
from odoo.exceptions import AccessError, MissingError, ValidationError

VALID_FREQUENCIES = ('one_time', 'weekly', 'biweekly', 'monthly')


class DonationWebsiteController(http.Controller):

    def _payment_method_for_provider(self, provider):
        provider_sudo = provider.sudo()
        pms = provider_sudo.with_context(active_test=False).payment_method_ids.filtered('active')
        if not pms:
            pms = provider_sudo.with_context(active_test=False).payment_method_ids[:1]
        return pms[:1]

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
        if method_type == 'provider' and method_id:
            provider = request.env['payment.provider'].sudo().browse(method_id)
            if provider.exists():
                return provider.get_donation_fee_coverage_percent()
        if method_type == 'token' and method_id:
            token = request.env['payment.token'].sudo().browse(method_id)
            if token.exists():
                return token.provider_id.get_donation_fee_coverage_percent()
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

        partner = request.env.user.partner_id if not request.env.user._is_public() else None
        providers = self._get_compatible_providers(partner)
        payment_tokens = self._get_payment_tokens(partner, providers)
        default_fee_percent = self._get_default_fee_coverage_percent()

        return request.render('donation_management.donation_form_page', {
            'campaigns': campaigns,
            'categories': categories,
            'providers': providers,
            'payment_tokens': payment_tokens,
            'user': request.env.user,
            'error': kwargs.get('error'),
            'success': kwargs.get('success'),
            'default_fee_percent': default_fee_percent,
        })

    @http.route('/donate/tokens', type='json', auth='public')
    def donation_tokens(self, email=None):
        """Return saved payment methods for a partner matched by email (guest donors)."""
        email = (email or '').strip()
        if not email:
            return []
        partner = request.env['res.partner'].sudo().search([('email', '=', email)], limit=1)
        if not partner:
            return []
        providers = self._get_compatible_providers(partner)
        tokens = self._get_payment_tokens(partner, providers)
        return [{
            'id': token.id,
            'display_name': token.display_name,
            'payment_method': token.payment_method_id.name,
            'provider_name': token.provider_id.name,
            'fee_percent': token.provider_id.get_donation_fee_coverage_percent(),
        } for token in tokens]

    @http.route('/donate/submit', type='http', auth='public', website=True, methods=['POST'], csrf=True)
    def donation_submit(self, **post):
        try:
            total_amount, base_amount, fee_amount, cover_fees = self._parse_amounts(post)
        except ValueError:
            return request.redirect('/donate?error=invalid_amount')

        frequency = post.get('frequency', 'one_time')
        if frequency not in VALID_FREQUENCIES:
            return request.redirect('/donate?error=invalid_frequency')

        recurring_start_date = False
        if frequency != 'one_time':
            start_raw = post.get('recurring_start_date', '').strip()
            if not start_raw:
                return request.redirect('/donate?error=missing_start_date')
            try:
                recurring_start_date = fields.Date.to_date(start_raw)
            except (TypeError, ValueError):
                return request.redirect('/donate?error=invalid_start_date')
            if recurring_start_date < fields.Date.today():
                return request.redirect('/donate?error=invalid_start_date')

        partner = self._get_or_create_partner(post)

        category_id = False
        if post.get('category_id'):
            try:
                raw_cat = int(post.get('category_id'))
            except (TypeError, ValueError):
                return request.redirect('/donate?error=invalid_category')
            category = request.env['donation.category'].sudo().search([
                ('id', '=', raw_cat),
                ('active', '=', True),
            ], limit=1)
            if not category:
                return request.redirect('/donate?error=invalid_category')
            category_id = category.id

        donation_vals = {
            'partner_id': partner.id,
            'amount': total_amount,
            'base_amount': base_amount,
            'fee_amount': fee_amount,
            'cover_fees': cover_fees,
            'currency_id': request.env.company.currency_id.id,
            'campaign_id': int(post.get('campaign_id')) if post.get('campaign_id') else False,
            'category_id': category_id,
            'purpose': post.get('purpose', ''),
            'frequency': frequency,
            'recurring_start_date': recurring_start_date,
            'state': 'draft',
        }

        donation = request.env['donation.donation'].sudo().with_context(
            from_website=True,
        ).create(donation_vals)

        method_type, method_id = self._parse_payment_method(post)
        if not method_type:
            donation.unlink()
            return request.redirect('/donate?error=invalid_payment_method')

        if method_type == 'token':
            return self._pay_with_saved_token(donation, method_id)
        return self._redirect_to_payment(donation, method_id, post)

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
            'phone': post.get('phone', ''),
        })

    def _redirect_to_confirmation(self, donation):
        donation_sudo = donation.sudo()
        donation_sudo._portal_ensure_token()
        return request.redirect(
            '/donate/confirmation/%s?access_token=%s' % (donation_sudo.id, donation_sudo.access_token)
        )

    def _pay_with_saved_token(self, donation, token_id):
        token = request.env['payment.token'].sudo().browse(token_id)
        if not token.exists():
            donation.unlink()
            return request.redirect('/donate?error=invalid_token')

        if token.partner_id.commercial_partner_id != donation.partner_id.commercial_partner_id:
            donation.unlink()
            return request.redirect('/donate?error=invalid_token')

        providers = self._get_compatible_providers(donation.partner_id, donation.amount)
        if token.provider_id.id not in providers.ids:
            donation.unlink()
            return request.redirect('/donate?error=invalid_token')

        donation.write({
            'payment_token_id': token.id,
            'state': 'pending',
        })

        recurring_starts_later = (
            donation.is_recurring
            and donation.recurring_start_date
            and donation.recurring_start_date > fields.Date.today()
        )
        if recurring_starts_later:
            try:
                donation._finalize_recurring_setup()
            except Exception:
                donation.unlink()
                return request.redirect('/donate?error=invalid_payment')
            return self._redirect_to_confirmation(donation)

        access_token = payment_utils.generate_access_token(
            donation.partner_id.id,
            donation.amount,
            donation.currency_id.id,
        )
        try:
            tx = PaymentPortal._create_transaction(
                provider_id=token.provider_id.id,
                payment_method_id=token.payment_method_id.id,
                token_id=token.id,
                amount=donation.amount,
                currency_id=donation.currency_id.id,
                partner_id=donation.partner_id.id,
                flow='token',
                tokenization_requested=False,
                landing_route='/payment/confirmation',
                reference_prefix=donation.name,
                custom_create_values={'reference': donation.name},
            )
        except (AccessError, ValidationError):
            donation.unlink()
            return request.redirect('/donate?error=invalid_token')

        donation.write({'payment_transaction_id': tx.id})
        PaymentPortal._update_landing_route(tx, access_token)
        return request.redirect('/payment/status')

    def _redirect_to_payment(self, donation, provider_id, post):
        provider = request.env['payment.provider'].sudo().browse(provider_id)
        if not provider.exists():
            donation.unlink()
            return request.redirect('/donate?error=invalid_provider')

        payment_method = self._payment_method_for_provider(provider)
        if not payment_method:
            donation.unlink()
            return request.redirect('/donate?error=no_payment_method')

        recurring_starts_later = (
            donation.is_recurring
            and donation.recurring_start_date
            and donation.recurring_start_date > fields.Date.today()
        )

        if recurring_starts_later:
            validation_currency = provider.with_context(
                validation_pm=payment_method,
            )._get_validation_currency()
            tx_currency = validation_currency[:1] or donation.currency_id
            tx_amount = provider._get_validation_amount()
            operation = 'validation'
        else:
            tx_currency = donation.currency_id
            tx_amount = donation.amount
            redirect_form_view = provider._get_redirect_form_view(is_validation=False)
            operation = 'online_redirect' if redirect_form_view else 'online_direct'

        access_token = payment_utils.generate_access_token(
            donation.partner_id.id,
            tx_amount,
            tx_currency.id,
        )

        tx_values = {
            'provider_id': provider.id,
            'payment_method_id': payment_method.id,
            'reference': donation.name,
            'amount': tx_amount,
            'currency_id': tx_currency.id,
            'partner_id': donation.partner_id.id,
            'operation': operation,
            'landing_route': '/payment/confirmation',
            'tokenize': donation.is_recurring,
        }

        tx = request.env['payment.transaction'].sudo().create(tx_values)
        donation.sudo().write({
            'payment_transaction_id': tx.id,
            'state': 'pending',
        })

        PaymentPortal._update_landing_route(tx, access_token)
        tx._log_sent_message()
        PaymentPostProcessing.monitor_transaction(tx)

        processing_values = tx._get_processing_values()
        redirect_html = processing_values.get('redirect_form_html') or ''
        if redirect_html and not isinstance(redirect_html, Markup):
            redirect_html = Markup(redirect_html)

        demo_direct = (
            provider.code == 'demo'
            and not redirect_html
            and operation in ('online_direct', 'validation')
        )

        return request.render('donation_management.donation_payment_redirect', {
            'tx': tx,
            'donation': donation,
            'redirect_form_html': redirect_html,
            'demo_direct': demo_direct,
        })

    @http.route(
        '/donate/demo/pay',
        type='http',
        auth='public',
        website=True,
        methods=['POST'],
        csrf=True,
    )
    def donation_demo_pay(self, **post):
        try:
            tx_id_int = int(post.get('tx_id', 0))
        except (TypeError, ValueError):
            return request.redirect('/donate?error=invalid_payment')

        tx = request.env['payment.transaction'].sudo().browse(tx_id_int)
        if not tx.exists() or tx.provider_code != 'demo':
            return request.redirect('/donate?error=invalid_payment')

        donation = request.env['donation.donation'].sudo().search([
            ('payment_transaction_id', '=', tx.id),
        ], limit=1)
        if not donation:
            return request.redirect('/donate?error=invalid_payment')

        simulated_state = post.get('simulated_state') or 'done'
        if simulated_state not in ('done', 'pending', 'cancel', 'error'):
            simulated_state = 'done'

        tx._handle_notification_data('demo', {
            'reference': tx.reference,
            'payment_details': (post.get('customer_input') or '').strip(),
            'simulated_state': simulated_state,
        })
        PaymentPostProcessing.monitor_transaction(tx)
        return request.redirect('/payment/status')

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

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'donation_count' in counters:
            partner = request.env.user.partner_id
            values['donation_count'] = request.env['donation.donation'].search_count([
                ('partner_id', '=', partner.id),
                ('state', '=', 'confirmed'),
            ])
        return values

    @http.route(['/my/donations', '/my/donations/page/<int:page>'], type='http', auth='user', website=True)
    def portal_my_donations(self, page=1, date_begin=None, date_end=None, sortby=None, **kw):
        values = self._prepare_portal_layout_values()
        partner = request.env.user.partner_id
        DonationSudo = request.env['donation.donation'].sudo()

        domain = [
            ('partner_id', '=', partner.id),
            ('state', '=', 'confirmed'),
        ]
        if date_begin and date_end:
            domain += [('date', '>=', date_begin), ('date', '<=', date_end)]

        searchbar_sortings = {
            'date': {'label': _('Date'), 'order': 'date desc'},
            'amount': {'label': _('Amount'), 'order': 'amount desc'},
            'name': {'label': _('Reference'), 'order': 'name'},
        }
        if not sortby:
            sortby = 'date'
        order = searchbar_sortings[sortby]['order']

        donation_count = DonationSudo.search_count(domain)
        pager = portal_pager(
            url="/my/donations",
            url_args={'date_begin': date_begin, 'date_end': date_end, 'sortby': sortby},
            total=donation_count,
            page=page,
            step=self._items_per_page,
        )
        donations = DonationSudo.search(
            domain, order=order, limit=self._items_per_page, offset=pager['offset'],
        )
        recurring_rules = request.env['donation.recurring.rule'].sudo().search([
            ('partner_id', '=', partner.id),
            ('state', '=', 'active'),
        ])

        values.update({
            'date_begin': date_begin,
            'date_end': date_end,
            'donations': donations,
            'recurring_rules': recurring_rules,
            'page_name': 'donation',
            'pager': pager,
            'default_url': '/my/donations',
            'searchbar_sortings': searchbar_sortings,
            'sortby': sortby,
        })
        return request.render('donation_management.portal_my_donations', values)

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
        ], limit=1)
        if rule:
            rule.action_pause()
        return request.redirect('/my/donations')

    @http.route(['/my/recurring_donations/<int:rule_id>/resume'], type='http', auth='user', website=True, methods=['POST'], csrf=True)
    def portal_resume_recurring(self, rule_id, **kw):
        rule = request.env['donation.recurring.rule'].sudo().search([
            ('id', '=', rule_id),
            ('partner_id', '=', request.env.user.partner_id.id),
        ], limit=1)
        if rule:
            rule.action_resume()
        return request.redirect('/my/donations')
