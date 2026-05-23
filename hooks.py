# -*- coding: utf-8 -*-

def _refresh_donation_menu_icon(env):
    menu = env.ref('donation_management.menu_donation_root', raise_if_not_found=False)
    if menu:
        menu.write({'web_icon': 'donation_management,static/description/icon.png'})


def post_init_hook(env):
    _refresh_donation_menu_icon(env)
