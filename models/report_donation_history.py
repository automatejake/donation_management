# -*- coding: utf-8 -*-
from odoo import fields, models


class ReportDonationHistory(models.AbstractModel):
    _name = 'report.donation_management.report_donation_history_document'
    _description = 'Donation History Report'

    def _get_report_values(self, docids, data=None):
        donations = self.env['donation.donation'].browse(docids).sorted('date', reverse=True)
        data = data or {}
        return {
            'doc_ids': docids,
            'doc_model': 'donation.donation',
            'docs': donations,
            'report_period_label': data.get('report_period_label', ''),
            'report_generated_date': fields.Date.to_string(fields.Date.today()),
        }
