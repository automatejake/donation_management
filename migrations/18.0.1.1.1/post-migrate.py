# -*- coding: utf-8 -*-

def migrate(cr, version):
    if not version:
        return
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    menu = env.ref('donation_management.menu_donation_root', raise_if_not_found=False)
    if menu:
        menu.write({'web_icon': 'donation_management,static/description/icon.png'})
