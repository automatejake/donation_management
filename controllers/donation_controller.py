# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import http, fields, _
from odoo.http import request
from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.controllers.portal import PaymentPortal
from odoo.addons.payment.controllers.post_processing import PaymentPostProcessing
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager
from odoo.exceptions import AccessError, MissingError


class DonationWebsiteController(http.Controller):

    def _payment_method_for_provider(self, provider):
        """Return one payment.method linked to the provider (required on payment.transaction)."""
        provider_sudo = provider.sudo()
        pms = provider_sudo.with_context(active_test=False).payment_method_ids.filtered('active')
        if not pms:
            pms = provider_sudo.with_context(active_test=False).payment_method_ids[:1]
        return pms[:1]

    @http.route('/donate', type='http', auth='public', website=True, sitemap=True)
    def donation_form(self, **kwargs):
        """Display donation form"""
        
        # Get active campaigns
        campaigns = request.env['donation.campaign'].sudo().search([
            ('active', '=', True),
            '|', ('end_date', '=', False), ('end_date', '>=', fields.Date.today())
        ])
        categories = request.env['donation.category'].sudo().search([('active', '=', True)])
        
        # Get available payment providers
        providers = request.env['payment.provider'].sudo()._get_compatible_providers(
            request.env.company.id,
            request.env.user.partner_id.id,
            currency_id=request.env.company.currency_id.id,
            amount=100
        )
        
        values = {
            'campaigns': campaigns,
            'categories': categories,
            'providers': providers,
            'user': request.env.user,
            'error': kwargs.get('error'),
            'success': kwargs.get('success'),
        }
        
        return request.render('donation_management.donation_form_page', values)

    @http.route('/donate/submit', type='http', auth='public', website=True, methods=['POST'], csrf=True)
    def donation_submit(self, **post):
        """Process donation form submission"""
        
        # Validate input
        try:
            amount = float(post.get('amount', 0))
            if amount <= 0:
                raise ValueError("Invalid amount")
        except ValueError:
            return request.redirect('/donate?error=invalid_amount')
        
        # Get or create partner
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
        
        # Create donation
        donation_vals = {
            'partner_id': partner.id,
            'amount': amount,
            'currency_id': request.env.company.currency_id.id,
            'campaign_id': int(post.get('campaign_id')) if post.get('campaign_id') else False,
            'category_id': category_id,
            'purpose': post.get('purpose', ''),
            'is_anonymous': post.get('is_anonymous') == 'on',
            'is_recurring': post.get('is_recurring') == 'on',
            'frequency': post.get('frequency', 'monthly') if post.get('is_recurring') == 'on' else False,
            'state': 'draft',
        }
        
        donation = request.env['donation.donation'].sudo().create(donation_vals)
        
        # Redirect to payment
        return self._redirect_to_payment(donation, post)

    def _get_or_create_partner(self, post):
        """Get existing partner or create new one"""
        
        # If user is logged in, use their partner
        if not request.env.user._is_public():
            return request.env.user.partner_id
        
        # Check if partner exists with this email
        email = post.get('email', '').strip()
        if email:
            partner = request.env['res.partner'].sudo().search([
                ('email', '=', email)
            ], limit=1)
            
            if partner:
                return partner
        
        # Create new partner
        partner_vals = {
            'name': post.get('name', 'Anonymous Donor'),
            'email': email,
            'phone': post.get('phone', ''),
        }
        
        return request.env['res.partner'].sudo().create(partner_vals)

    def _redirect_to_payment(self, donation, post):
        """Redirect to payment processing"""
        
        # Get selected payment provider
        provider_id = int(post.get('provider_id'))
        provider = request.env['payment.provider'].sudo().browse(provider_id)
        
        if not provider.exists():
            return request.redirect('/donate?error=invalid_provider')

        payment_method = self._payment_method_for_provider(provider)
        if not payment_method:
            return request.redirect('/donate?error=no_payment_method')

        redirect_form_view = provider._get_redirect_form_view(is_validation=False)
        operation = 'online_redirect' if redirect_form_view else 'online_direct'

        access_token = payment_utils.generate_access_token(
            donation.partner_id.id,
            donation.amount,
            donation.currency_id.id,
        )

        tx_values = {
            'provider_id': provider.id,
            'payment_method_id': payment_method.id,
            'reference': donation.name,
            'amount': donation.amount,
            'currency_id': donation.currency_id.id,
            'partner_id': donation.partner_id.id,
            'operation': operation,
            'landing_route': '/payment/confirmation',
            'tokenize': post.get('save_token') == 'on' or donation.is_recurring,
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

        demo_direct = operation == 'online_direct' and provider.code == 'demo'

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
        """Complete a demo payment for a donation (demo provider has no redirect HTML form)."""
        try:
            tx_id_int = int(post.get('tx_id', 0))
        except (TypeError, ValueError):
            return request.redirect('/donate?error=invalid_payment')

        tx = request.env['payment.transaction'].sudo().browse(tx_id_int)
        if not tx.exists() or tx.provider_code != 'demo':
            return request.redirect('/donate?error=invalid_payment')

        donation = request.env['donation.donation'].sudo().search([
            ('payment_transaction_id', '=', tx.id),
            ('state', '=', 'pending'),
        ], limit=1)
        if not donation:
            return request.redirect('/donate?error=invalid_payment')

        simulated_state = post.get('simulated_state') or 'done'
        if simulated_state not in ('done', 'pending', 'cancel', 'error'):
            simulated_state = 'done'

        customer_input = (post.get('customer_input') or '').strip()

        tx._handle_notification_data('demo', {
            'reference': tx.reference,
            'payment_details': customer_input,
            'simulated_state': simulated_state,
        })
        PaymentPostProcessing.monitor_transaction(tx)
        return request.redirect('/payment/status')

    @http.route('/donate/confirmation/<int:donation_id>', type='http', auth='public', website=True)
    def donation_confirmation(self, donation_id, access_token=None, **kwargs):
        """Display donation confirmation page"""
        
        try:
            donation = self._document_check_access('donation.donation', donation_id, access_token)
        except (AccessError, MissingError):
            return request.redirect('/donate?error=access_denied')
        
        return request.render('donation_management.donation_confirmation_page', {
            'donation': donation,
        })

    def _document_check_access(self, model_name, document_id, access_token=None):
        """Check access to document"""
        document = request.env[model_name].browse(document_id)
        
        if not document.exists():
            raise MissingError(_("This document does not exist."))
        
        # Check access token
        if access_token:
            if not document.access_token or document.access_token != access_token:
                raise AccessError(_("Invalid access token."))
        # Check if user owns the document
        elif not request.env.user._is_public():
            if document.partner_id != request.env.user.partner_id:
                raise AccessError(_("You do not have access to this document."))
        else:
            raise AccessError(_("You must be logged in to access this document."))
        
        return document


class DonationPortal(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        """Add donation count to portal home"""
        values = super()._prepare_home_portal_values(counters)
        
        if 'donation_count' in counters:
            partner = request.env.user.partner_id
            donation_count = request.env['donation.donation'].search_count([
                ('partner_id', '=', partner.id),
                ('state', '=', 'confirmed'),
            ])
            values['donation_count'] = donation_count
        
        return values

    @http.route(['/my/donations', '/my/donations/page/<int:page>'], type='http', auth='user', website=True)
    def portal_my_donations(self, page=1, date_begin=None, date_end=None, sortby=None, **kw):
        """Display user's donations in portal"""
        
        values = self._prepare_portal_layout_values()
        partner = request.env.user.partner_id
        DonationSudo = request.env['donation.donation'].sudo()
        
        domain = [
            ('partner_id', '=', partner.id),
            ('state', '=', 'confirmed'),
        ]
        
        # Date filtering
        if date_begin and date_end:
            domain += [('date', '>=', date_begin), ('date', '<=', date_end)]
        
        # Sorting
        searchbar_sortings = {
            'date': {'label': _('Date'), 'order': 'date desc'},
            'amount': {'label': _('Amount'), 'order': 'amount desc'},
            'name': {'label': _('Reference'), 'order': 'name'},
        }
        
        if not sortby:
            sortby = 'date'
        
        order = searchbar_sortings[sortby]['order']
        
        # Count for pager
        donation_count = DonationSudo.search_count(domain)
        
        # Pager
        pager = portal_pager(
            url="/my/donations",
            url_args={'date_begin': date_begin, 'date_end': date_end, 'sortby': sortby},
            total=donation_count,
            page=page,
            step=self._items_per_page
        )
        
        # Content
        donations = DonationSudo.search(domain, order=order, limit=self._items_per_page, offset=pager['offset'])
        
        # Get recurring rules
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
        """Display single donation detail"""
        
        try:
            donation_sudo = self._document_check_access('donation.donation', donation_id, access_token=access_token)
        except (AccessError, MissingError):
            return request.redirect('/my')
        
        values = {
            'donation': donation_sudo,
            'page_name': 'donation',
        }
        
        return request.render('donation_management.portal_donation_detail', values)

    @http.route(['/my/recurring_donations/<int:rule_id>/cancel'], type='http', auth='user', website=True, methods=['POST'], csrf=True)
    def portal_cancel_recurring(self, rule_id, **kw):
        """Cancel a recurring donation from portal"""
        
        rule = request.env['donation.recurring.rule'].sudo().search([
            ('id', '=', rule_id),
            ('partner_id', '=', request.env.user.partner_id.id),
        ], limit=1)
        
        if rule:
            rule.action_deactivate()
        
        return request.redirect('/my/donations')

    @http.route(['/my/recurring_donations/<int:rule_id>/pause'], type='http', auth='user', website=True, methods=['POST'], csrf=True)
    def portal_pause_recurring(self, rule_id, **kw):
        """Pause a recurring donation from portal"""
        
        rule = request.env['donation.recurring.rule'].sudo().search([
            ('id', '=', rule_id),
            ('partner_id', '=', request.env.user.partner_id.id),
        ], limit=1)
        
        if rule:
            rule.action_pause()
        
        return request.redirect('/my/donations')

    @http.route(['/my/recurring_donations/<int:rule_id>/resume'], type='http', auth='user', website=True, methods=['POST'], csrf=True)
    def portal_resume_recurring(self, rule_id, **kw):
        """Resume a recurring donation from portal"""
        
        rule = request.env['donation.recurring.rule'].sudo().search([
            ('id', '=', rule_id),
            ('partner_id', '=', request.env.user.partner_id.id),
        ], limit=1)
        
        if rule:
            rule.action_resume()
        
        return request.redirect('/my/donations')