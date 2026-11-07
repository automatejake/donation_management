# Helcim moved out of Donation Management into donation_management_helcim.
# Sites that already take gifts through Helcim get that module installed
# here, so their giving page keeps its Helcim card and bank fields.
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    modules = env['ir.module.module']
    if not modules.search([('name', '=', 'payment_helcim'), ('state', '=', 'installed')]):
        return
    bridge = modules.search([
        ('name', '=', 'donation_management_helcim'),
        ('state', '=', 'uninstalled'),
    ])
    if bridge:
        bridge.button_install()
