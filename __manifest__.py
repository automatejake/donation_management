# -*- coding: utf-8 -*-
{
    'name': 'Donation Management',
    'version': '18.0.1.5.0',
    'category': 'Website/Website',
    'summary': 'Manage online donations with recurring payments',
    'description': """
        Donation Management Module
        ===========================
        * Accept one-time and recurring donations via website
        * Works with any Odoo payment provider (Helcim through the donation_management_helcim bridge)
        * Donor portal for viewing donation history
        * Automatic recurring donation processing
        * Payment token support for saved cards
        * Offering batches: enter a Sunday's envelopes, checks and cash on one screen
        * Stewardship pledges (switch on in Settings): progress per family, shown on My Account
        * Year-end giving statements (OCA donation_base annual receipts)
    """,
    'author': 'Jacob Neubaum',
    'website': 'https://www.automatejake.com',
    'depends': [
        'base',
        'website',
        'payment',
        'portal',
        'account',
        'donation_base',
    ],
    'data': [
        'security/donation_security.xml',
        'security/donation_pledge_security.xml',
        'security/ir.model.access.csv',
        'data/donation_data.xml',
        'data/donation_cron.xml',
        'views/payment_provider_views.xml',
        'views/payment_method_views.xml',
        'views/donation_views.xml',
        'views/donation_portal_templates.xml',
        'views/donation_website_templates.xml',
        'report/donation_receipt_template.xml',
        'report/donation_portal_report.xml',
        'report/year_end_statement_template.xml',
        'data/year_end_statement_mail.xml',
        'views/year_end_statement_views.xml',
        'data/donation_batch_data.xml',
        'data/donation_batch_partner.xml',
        'views/donation_batch_views.xml',
        'views/donation_pledge_views.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'donation_management/static/src/js/donation_form.js',
            'donation_management/static/src/css/donation.css',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
    # 'post_init_hook': 'post_init_hook',
}