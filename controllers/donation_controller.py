# -*- coding: utf-8 -*-
import csv
import io
import logging
import re
from calendar import monthrange
from datetime import datetime

from markupsafe import Markup
from werkzeug.urls import url_encode, url_quote

from odoo import http, fields, _
from odoo.http import request
from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.controllers.portal import PaymentPortal
from odoo.addons.payment.controllers.post_processing import PaymentPostProcessing
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager
from odoo.exceptions import AccessError, MissingError, ValidationError

_logger = logging.getLogger(__name__)

VALID_FREQUENCIES = ('one_time', 'weekly', 'biweekly', 'monthly')


class DonationWebsiteController(http.Controller):

    def _payment_portal(self):
        """Payment portal helpers (_create_transaction needs a controller instance)."""
        return PaymentPortal()

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

    def _get_donation_payment_options(self, partner, amount=None):
        """Payment methods to show on the donate form (Card, ACH, etc.), each with its provider."""
        amount = amount if amount is not None else 100.0
        partner = partner or request.env.user.partner_id
        providers = self._get_compatible_providers(partner, amount)
        payment_methods = request.env['payment.method'].sudo()._get_compatible_payment_methods(
            providers.ids,
            partner.id,
            currency_id=request.env.company.currency_id.id,
            amount=amount,
        )
        options = []
        for pm in payment_methods:
            provider = (pm.provider_ids & providers)[:1]
            if provider:
                options.append({'payment_method': pm, 'provider': provider})
        return options

    def _resolve_payment_selection(self, post, partner=None, amount=None):
        """Return (provider, payment_method) from form data, or (None, None)."""
        method_type, method_id = self._parse_payment_method(post)
        if not method_type:
            return None, None
        if method_type == 'token':
            token = request.env['payment.token'].sudo().browse(method_id)
            if not token.exists():
                return None, None
            return token.provider_id, token.payment_method_id

        partner = partner or request.env.user.partner_id
        try:
            amount = amount if amount is not None else float(post.get('amount') or 0)
        except (TypeError, ValueError):
            amount = 100.0
        providers = self._get_compatible_providers(partner, amount or 100.0)

        if method_type == 'method':
            pm = request.env['payment.method'].sudo().browse(method_id)
            provider = (pm.provider_ids & providers)[:1]
            if not pm.exists() or not provider:
                return None, None
            return provider, pm

        if method_type == 'provider':
            provider = request.env['payment.provider'].sudo().browse(method_id)
            if not provider.exists() or provider.id not in providers.ids:
                return None, None
            return provider, self._payment_method_for_provider(provider)

        return None, None

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
            token = request.env['payment.token'].sudo().browse(method_id)
            if token.exists() and token.payment_method_id:
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

        partner = request.env.user.partner_id if not request.env.user._is_public() else None
        providers = self._get_compatible_providers(partner)
        payment_tokens = self._get_payment_tokens(partner, providers)
        payment_options = self._get_donation_payment_options(partner)
        default_fee_percent = self._get_default_fee_coverage_percent()
        helcim_provider = self._get_compatible_providers(partner).filtered(
            lambda p: p.code == 'helcim',
        )[:1]

        return request.render('donation_management.donation_form_page', {
            'campaigns': campaigns,
            'categories': categories,
            'payment_options': payment_options,
            'payment_tokens': payment_tokens,
            'user': request.env.user,
            'error': kwargs.get('error'),
            'success': kwargs.get('success'),
            'default_fee_percent': default_fee_percent,
            'helcim_checkout_mode': (
                helcim_provider.helcim_checkout_mode if helcim_provider else False
            ),
            'helcim_js_config_token': (
                helcim_provider.helcim_js_config_token if helcim_provider else False
            ),
            'helcim_provider_state': (
                helcim_provider.state if helcim_provider else False
            ),
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
            'fee_percent': token.payment_method_id.get_donation_fee_coverage_percent(),
        } for token in tokens]

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

    def _create_donation_payment_transaction(self, donation, provider, payment_method=None):
        """Create a payment transaction for a website donation.

        :return: (tx, payment_method, operation)
        """
        if not payment_method:
            payment_method = self._payment_method_for_provider(provider)
        if not payment_method:
            raise ValidationError(_('No payment method configured for this provider.'))

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
            operation = provider._donation_payment_operation(is_validation=False)

        access_token = payment_utils.generate_access_token(
            donation.partner_id.id,
            tx_amount,
            tx_currency.id,
        )

        tx = request.env['payment.transaction'].sudo().create({
            'provider_id': provider.id,
            'payment_method_id': payment_method.id,
            'reference': donation.name,
            'amount': tx_amount,
            'currency_id': tx_currency.id,
            'partner_id': donation.partner_id.id,
            'operation': operation,
            'landing_route': '/payment/confirmation',
            'tokenize': donation.is_recurring,
        })
        donation.sudo().write({
            'payment_transaction_id': tx.id,
            'state': 'pending',
        })
        PaymentPortal._update_landing_route(tx, access_token)
        tx._log_sent_message()
        PaymentPostProcessing.monitor_transaction(tx)
        return tx, payment_method, operation

    @http.route('/donate/submit', type='http', auth='public', website=True, methods=['POST'], csrf=True)
    def donation_submit(self, **post):
        try:
            donation = self._create_donation_from_post(post)
        except ValueError as exc:
            return request.redirect('/donate?error=%s' % exc.args[0])

        method_type, method_id = self._parse_payment_method(post)
        if not method_type:
            donation.unlink()
            return request.redirect('/donate?error=invalid_payment_method')

        if method_type == 'token':
            return self._pay_with_saved_token(donation, method_id)

        provider, payment_method = self._resolve_payment_selection(
            post, donation.partner_id, donation.amount,
        )
        if not provider or not payment_method:
            donation.unlink()
            return request.redirect('/donate?error=invalid_payment_method')

        if provider.code == 'helcim':
            if (
                provider.helcim_checkout_mode == 'helcim_js'
                and payment_method.code == 'card'
            ):
                donation.unlink()
                return request.redirect('/donate?error=helcim_js_required')
            if provider.helcim_checkout_mode == 'helcimpay_js':
                return self._redirect_to_payment(donation, provider, payment_method, post)
            return self._pay_with_helcim_api(donation, provider, payment_method, post)

        return self._redirect_to_payment(donation, provider, payment_method, post)

    @http.route('/donate/prepare_helcim_js', type='json', auth='public')
    def donation_prepare_helcim_js(self, **post):
        """Create donation + transaction; return data for Helcim.js checkout."""
        try:
            donation = self._create_donation_from_post(post)
        except ValueError as exc:
            return {'error': str(exc.args[0])}

        provider, payment_method = self._resolve_payment_selection(
            post, donation.partner_id, donation.amount,
        )
        if (
            not provider
            or not payment_method
            or provider.code != 'helcim'
            or provider.helcim_checkout_mode != 'helcim_js'
            or payment_method.code != 'card'
        ):
            donation.unlink()
            return {'error': 'invalid_provider'}
        if not provider.helcim_js_config_token:
            donation.unlink()
            return {
                'error': 'helcim_js_not_configured',
                'message': _(
                    'Helcim.js is not configured. Add a Helcim.js Configuration '
                    'Token on the Helcim payment provider.'
                ),
            }

        try:
            tx, payment_method, operation = self._create_donation_payment_transaction(
                donation, provider, payment_method,
            )
        except ValidationError as exc:
            donation.unlink()
            return {'error': 'no_payment_method', 'message': exc.args[0]}

        customer_vals = provider._helcim_get_customer_code(donation.partner_id)
        if customer_vals.get('errorMessage'):
            donation.unlink()
            return {'error': 'invalid_provider', 'message': customer_vals['errorMessage']}

        return {
            'donation_id': donation.id,
            'amount': tx.amount,
            'customer_code': customer_vals.get('customerCode'),
            'tokenize': bool(tx.tokenize),
            'operation': operation,
        }

    @http.route('/donate/prepare_helcim_pay', type='json', auth='public')
    def donation_prepare_helcim_pay(self, **post):
        """Create donation + transaction; return data for HelcimPay.js checkout."""
        try:
            donation = self._create_donation_from_post(post)
        except ValueError as exc:
            return {'error': str(exc.args[0])}

        provider, payment_method = self._resolve_payment_selection(
            post, donation.partner_id, donation.amount,
        )
        if (
            not provider
            or not payment_method
            or provider.code != 'helcim'
            or provider.helcim_checkout_mode != 'helcimpay_js'
        ):
            donation.unlink()
            return {'error': 'invalid_provider'}

        try:
            tx, payment_method, operation = self._create_donation_payment_transaction(
                donation, provider, payment_method,
            )
        except ValidationError as exc:
            donation.unlink()
            return {'error': 'no_payment_method', 'message': exc.args[0]}

        donation_sudo = donation.sudo()
        donation_sudo._portal_ensure_token()
        return {
            'donation_id': donation_sudo.id,
            'reference': tx.reference,
            'access_token': donation_sudo.access_token,
            'payment_method_code': payment_method.code,
            'tokenize': bool(tx.tokenize),
            'operation': operation,
        }

    @http.route(
        '/donate/helcim_js_complete',
        type='http',
        auth='public',
        website=True,
        methods=['POST'],
        csrf=True,
    )
    def donation_helcim_js_complete(self, **post):
        """Complete a donation after Helcim.js processed the card in the browser."""
        try:
            donation_id = int(post.get('donation_id') or 0)
        except (TypeError, ValueError):
            return request.redirect('/donate?error=invalid_payment')

        donation = request.env['donation.donation'].sudo().browse(donation_id).exists()
        if not donation or donation.state not in ('draft', 'pending'):
            return request.redirect('/donate?error=invalid_payment')

        tx = donation.payment_transaction_id
        if not tx or tx.provider_code != 'helcim':
            donation.unlink()
            return request.redirect('/donate?error=invalid_payment')

        tokenize = (
            post.get('helcim_save_payment_method') == 'on'
            or donation.is_recurring
        )
        try:
            tx._helcim_process_js_post(post, tokenize=tokenize)
        except ValidationError as exc:
            donation.unlink()
            return request.redirect(
                '/donate?error=%s' % url_quote(str(exc.args[0])),
            )
        except Exception:
            _logger.exception('Helcim.js payment failed for %s', tx.reference)
            donation.unlink()
            return request.redirect('/donate?error=invalid_payment')

        if tx.state == 'done':
            tx._post_process()
            _logger.info(
                'Donation %s paid via Helcim.js — search transaction ID %s in Helcim '
                '(Payments → Test Transactions if the provider is in test mode).',
                donation.name,
                tx.provider_reference or '?',
            )
            return self._redirect_to_confirmation(donation)
        if tx.state == 'pending':
            donation.write({'state': 'pending'})
            return request.redirect('/payment/status')
        if tx.state == 'error':
            msg = tx.state_message or _('Payment was declined.')
            donation.unlink()
            return request.redirect('/donate?error=%s' % url_quote(msg))

        donation.unlink()
        return request.redirect('/donate?error=invalid_payment')

    def _extract_helcim_payment_data(self, post, payment_method, donation):
        """Build payment payload for Helcim direct API from the donation form."""
        partner = donation.partner_id
        save = post.get('helcim_save_payment_method') == 'on' or donation.is_recurring
        if payment_method.code == 'card':
            return {
                'type': 'card',
                'card_number': post.get('helcim_card_number', ''),
                'card_expiry': post.get('helcim_card_expiry', ''),
                'card_cvv': post.get('helcim_card_cvv', ''),
                'card_holder_name': post.get('helcim_card_holder_name') or partner.name,
                'save_payment_method': save,
            }
        if payment_method.code == 'ach_direct_debit':
            return {
                'type': 'ach',
                'bank_account_number': post.get('helcim_bank_account_number', ''),
                'bank_routing': post.get('helcim_bank_routing', ''),
                'bank_financial': post.get('helcim_bank_financial', ''),
                'bank_transit': post.get('helcim_bank_transit', ''),
                'bank_account_type': post.get('helcim_bank_account_type', '1'),
                'bank_holder_type': post.get('helcim_bank_holder_type', '1'),
                'bank_account_holder': post.get('helcim_bank_account_holder') or partner.name,
                'bank_street': post.get('helcim_bank_street') or partner.street,
                'bank_city': post.get('helcim_bank_city') or partner.city,
                'bank_zip': post.get('helcim_bank_zip') or partner.zip,
                'bank_state': post.get('helcim_bank_state') or (
                    partner.state_id.code if partner.state_id else ''
                ),
                'bank_country': partner.country_id.code if partner.country_id else 'US',
                'save_payment_method': save,
            }
        raise ValidationError(_('Unsupported Helcim payment method.'))

    def _pay_with_helcim_api(self, donation, provider, payment_method, post):
        """Process donation payment through the Helcim Payment API (native form)."""
        try:
            payment_data = self._extract_helcim_payment_data(post, payment_method, donation)
        except ValidationError as exc:
            donation.unlink()
            return request.redirect(
                '/donate?error=%s' % url_quote(str(exc.args[0])),
            )

        try:
            tx, payment_method, operation = self._create_donation_payment_transaction(
                donation, provider, payment_method,
            )
        except ValidationError:
            donation.unlink()
            return request.redirect('/donate?error=no_payment_method')

        try:
            tx._helcim_process_direct_payment(payment_data)
        except ValidationError as exc:
            donation.unlink()
            return request.redirect(
                '/donate?error=%s' % url_quote(str(exc.args[0])),
            )
        except Exception:
            _logger.exception('Helcim direct API payment failed for %s', tx.reference)
            donation.unlink()
            return request.redirect('/donate?error=invalid_payment')

        if tx.state == 'done':
            tx._post_process()
            return self._redirect_to_confirmation(donation)
        if tx.state == 'pending':
            donation.write({'state': 'pending'})
            return request.redirect('/payment/status')
        if tx.state == 'error':
            msg = tx.state_message or _('Payment was declined.')
            donation.unlink()
            return request.redirect('/donate?error=%s' % url_quote(msg))

        donation.unlink()
        return request.redirect('/donate?error=invalid_payment')

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
            tx = self._payment_portal()._create_transaction(
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

    def _redirect_to_payment(self, donation, provider, payment_method, post):
        if not provider.exists():
            donation.unlink()
            return request.redirect('/donate?error=invalid_provider')

        try:
            tx, payment_method, operation = self._create_donation_payment_transaction(
                donation, provider, payment_method,
            )
        except ValidationError:
            donation.unlink()
            return request.redirect('/donate?error=no_payment_method')

        redirect_html = ''
        if operation == 'online_redirect':
            processing_values = tx._get_processing_values()
            redirect_html = processing_values.get('redirect_form_html') or ''
            if redirect_html and not isinstance(redirect_html, Markup):
                redirect_html = Markup(redirect_html)

        demo_direct = (
            provider.code == 'demo'
            and not redirect_html
            and operation in ('online_direct', 'validation')
        )
        helcim_pay_autostart = (
            provider.code == 'helcim'
            and provider.helcim_checkout_mode == 'helcimpay_js'
            and operation in ('online_direct', 'validation')
        )
        if helcim_pay_autostart:
            donation.sudo()._portal_ensure_token()

        return request.render('donation_management.donation_payment_redirect', {
            'tx': tx,
            'donation': donation,
            'redirect_form_html': redirect_html,
            'demo_direct': demo_direct,
            'helcim_pay_autostart': helcim_pay_autostart,
            'payment_method': payment_method,
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
            pdf_content, _report_format = report.sudo()._render_qweb_pdf(
                report.report_name,
                donations.ids,
                data={'report_period_label': history['period_label']},
            )
            pdf_headers = [
                ('Content-Type', 'application/pdf'),
                ('Content-Length', len(pdf_content)),
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
