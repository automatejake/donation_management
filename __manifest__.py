# -*- coding: utf-8 -*-
{
    'name': 'Donation Management',
    'version': '18.0.1.1.0',
    'category': 'Website/Website',
    'summary': 'Manage online donations with recurring payments',
    'description': """
        Donation Management Module
        ===========================
        * Accept one-time and recurring donations via website
        * Integrate with Odoo payment providers
        * Donor portal for viewing donation history
        * Automatic recurring donation processing
        * Payment token support for saved cards
    """,
    'author': 'Your Organization',
    'website': 'https://www.yourorganization.com',
    'depends': [
        'base',
        'website',
        'payment',
        'portal',
        'account',
    ],
    'data': [
        'security/donation_security.xml',
        'security/ir.model.access.csv',
        'data/donation_data.xml',
        'data/donation_cron.xml',
        'views/payment_provider_views.xml',
        'views/payment_method_views.xml',
        'views/donation_views.xml',
        'views/donation_portal_templates.xml',
        'views/donation_website_templates.xml',
        'report/donation_receipt_template.xml',
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
}