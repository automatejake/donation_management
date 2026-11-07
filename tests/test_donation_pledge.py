# -*- coding: utf-8 -*-
import re
from datetime import date, datetime

from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger


class PledgeCommon:

    @classmethod
    def _setup_pledges(cls):
        cls.company = cls.env.company
        income = cls.env['account.account'].create({
            'name': 'Pledge test income', 'code': 'PLG400', 'account_type': 'income',
        })
        cls.general = cls.env['donation.category'].create({
            'name': 'General Fund (pledge test)', 'account_id': income.id,
        })
        cls.building = cls.env['donation.category'].create({
            'name': 'Building Fund (pledge test)', 'account_id': income.id,
        })
        cls.campaign = cls.env['donation.campaign'].create({
            'name': '2026 Stewardship',
            'start_date': date(2026, 1, 1),
            'end_date': date(2026, 12, 31),
        })
        cls.maria = cls.env['res.partner'].create({'name': 'Maria Popescu', 'email': 'maria@example.com'})
        cls.george = cls.env['res.partner'].create({'name': 'George Haddad'})

    def _gift(self, partner, amount, when, category=None, state='confirmed'):
        donation = self.env['donation.donation'].with_context(from_website=True).create({
            'partner_id': partner.id,
            'amount': amount,
            'currency_id': self.company.currency_id.id,
            'date': when,
            'category_id': (category or self.general).id,
        })
        donation.with_context(skip_donation_complete=True).write({'state': state})
        return donation

    def _pledge(self, partner, amount, frequency='monthly', category=None):
        return self.env['donation.pledge'].create({
            'campaign_id': self.campaign.id,
            'partner_id': partner.id,
            'amount': amount,
            'frequency': frequency,
            'category_id': category.id if category else False,
        })


@tagged('post_install', '-at_install')
class TestDonationPledge(PledgeCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_pledges()

    def test_gifts_in_the_campaign_dates_count(self):
        pledge = self._pledge(self.maria, 1200.0)
        self._gift(self.maria, 100.0, datetime(2026, 1, 4, 12))
        self._gift(self.maria, 150.0, datetime(2026, 6, 7, 12))
        self._gift(self.maria, 500.0, datetime(2025, 12, 28, 12))  # last year
        self._gift(self.maria, 80.0, datetime(2026, 3, 1, 12), state='cancelled')
        self._gift(self.george, 999.0, datetime(2026, 3, 1, 12))  # someone else
        pledge.invalidate_recordset()
        self.assertEqual(pledge.given, 250.0)
        self.assertEqual(pledge.remaining, 950.0)
        self.assertAlmostEqual(pledge.progress, 250.0 / 12.0, places=2)

    def test_fund_limits_what_counts(self):
        pledge = self._pledge(self.maria, 1000.0, category=self.building)
        self._gift(self.maria, 100.0, datetime(2026, 2, 1, 12), category=self.general)
        self._gift(self.maria, 300.0, datetime(2026, 2, 1, 12), category=self.building)
        pledge.invalidate_recordset()
        self.assertEqual(pledge.given, 300.0)

    def test_expected_follows_the_calendar(self):
        pledge = self._pledge(self.maria, 1200.0)
        expected = pledge._expected_by(date(2026, 6, 30))
        self.assertAlmostEqual(expected, 1200.0 * 181 / 365, delta=0.01)
        self.assertEqual(pledge._expected_by(date(2025, 12, 1)), 0.0)
        self.assertEqual(pledge._expected_by(date(2027, 1, 5)), 1200.0)
        yearly = self._pledge(self.george, 600.0, frequency='yearly')
        self.assertEqual(yearly._expected_by(date(2026, 11, 15)), 0.0)
        self.assertEqual(yearly._expected_by(date(2026, 12, 31)), 600.0)

    def test_fulfilled_and_capped_at_100(self):
        pledge = self._pledge(self.maria, 600.0, frequency='yearly')
        self._gift(self.maria, 650.0, datetime(2026, 4, 1, 12))
        pledge.invalidate_recordset()
        self.assertEqual(pledge.status, 'done')
        self.assertEqual(pledge.progress, 100.0)
        self.assertEqual(pledge.remaining, 0.0)

    def test_batch_envelope_gifts_count(self):
        """Envelope gifts posted from a Sunday batch reach the pledge like online gifts."""
        gift = self._gift(self.maria, 75.0, datetime(2026, 11, 15, 12))
        gift.payment_kind = 'check'
        pledge = self._pledge(self.maria, 900.0, frequency='weekly')
        self.assertEqual(pledge.given, 75.0)

    def test_one_pledge_per_family_and_campaign(self):
        self._pledge(self.maria, 1200.0)
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError), self.cr.savepoint():
            self._pledge(self.maria, 500.0)

    def test_campaign_needs_dates(self):
        undated = self.env['donation.campaign'].create({'name': 'Undated'})
        with self.assertRaises(ValidationError):
            self.env['donation.pledge'].create({
                'campaign_id': undated.id, 'partner_id': self.maria.id, 'amount': 100.0,
            })

    def test_campaign_totals(self):
        self._pledge(self.maria, 1200.0)
        self._pledge(self.george, 800.0)
        self._gift(self.george, 200.0, datetime(2026, 5, 1, 12))
        self.campaign.invalidate_recordset()
        self.assertEqual(self.campaign.pledge_count, 2)
        self.assertEqual(self.campaign.pledged_total, 2000.0)
        self.assertEqual(self.campaign.pledge_given_total, 200.0)

    def test_setting_switches_the_feature(self):
        Pledge = self.env['donation.pledge']
        settings = self.env['res.config.settings'].create({'group_donation_pledges': False})
        settings.execute()
        self.assertFalse(Pledge._pledges_enabled())
        settings = self.env['res.config.settings'].create({'group_donation_pledges': True})
        settings.execute()
        self.assertTrue(Pledge._pledges_enabled())


@tagged('post_install', '-at_install')
class TestDonationPledgePortal(PledgeCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_pledges()
        # A campaign running today, so the card shows whatever day the tests run.
        today = date.today()
        cls.campaign.write({'start_date': today.replace(month=1, day=1), 'end_date': today.replace(month=12, day=31)})
        cls.user = cls.env['res.users'].create({
            'name': 'Maria Popescu',
            'login': 'maria_pledge',
            'password': 'maria_pledge_pw1',
            'partner_id': cls.maria.id,
            'groups_id': [(6, 0, [cls.env.ref('base.group_portal').id])],
        })

    def _page(self):
        self.authenticate('maria_pledge', 'maria_pledge_pw1')
        response = self.url_open('/my/donations')
        self.assertEqual(response.status_code, 200)
        return response.text

    def _enable(self, on):
        self.env['res.config.settings'].create({'group_donation_pledges': on}).execute()

    def test_card_shows_own_pledge_when_switched_on(self):
        self._enable(True)
        self._pledge(self.maria, 1200.0)
        self._pledge(self.george, 4321.0)
        page = self._page()
        self.assertIn('o_portal_donation_pledge', page)
        self.assertIn('2026 Stewardship', page)
        self.assertNotIn('4,321', page)

    def test_no_card_when_switched_off(self):
        self._enable(True)
        self._pledge(self.maria, 1200.0)
        self._enable(False)
        self.assertNotIn('o_portal_donation_pledge', self._page())

    def test_no_card_without_a_pledge(self):
        self._enable(True)
        self.assertNotIn('o_portal_donation_pledge', self._page())

    def _post_change(self, pledge, amount, frequency='weekly'):
        self.authenticate('maria_pledge', 'maria_pledge_pw1')
        # Any website page carries the session's CSRF token; a valid token
        # makes sure a refused change is refused by the route, not by CSRF.
        page = self.url_open('/my/donations').text
        token = re.search(r'csrf_token["\']?\s*[:=]\s*["\']([0-9a-fo]+)', page).group(1)
        return self.url_open(f'/my/pledges/{pledge.id}/edit', data={
            'csrf_token': token, 'amount': amount, 'frequency': frequency,
        })

    def test_donor_changes_own_pledge(self):
        self._enable(True)
        pledge = self._pledge(self.maria, 1200.0)
        self.assertIn(f'/my/pledges/{pledge.id}/edit', self._page())
        response = self._post_change(pledge, '1500', 'weekly')
        self.assertIn('pledge_updated=1', response.url)
        pledge.invalidate_recordset()
        self.assertEqual(pledge.amount, 1500.0)
        self.assertEqual(pledge.frequency, 'weekly')
        self.assertTrue(pledge.message_ids.filtered(lambda m: 'Changed online' in (m.body or '')))

    def test_zero_amount_is_refused(self):
        self._enable(True)
        pledge = self._pledge(self.maria, 1200.0)
        response = self._post_change(pledge, '0')
        self.assertIn('error=amount', response.url)
        pledge.invalidate_recordset()
        self.assertEqual(pledge.amount, 1200.0)

    def test_cannot_change_someone_elses_pledge(self):
        self._enable(True)
        other = self._pledge(self.george, 800.0)
        self._post_change(other, '1')
        other.invalidate_recordset()
        self.assertEqual(other.amount, 800.0)

    def test_setting_off_hides_and_blocks_changes(self):
        self._enable(True)
        self.company.donation_pledge_portal_edit = False
        pledge = self._pledge(self.maria, 1200.0)
        page = self._page()
        self.assertIn('o_portal_donation_pledge', page)
        self.assertNotIn(f'/my/pledges/{pledge.id}/edit', page)
        self._post_change(pledge, '50')
        pledge.invalidate_recordset()
        self.assertEqual(pledge.amount, 1200.0)
