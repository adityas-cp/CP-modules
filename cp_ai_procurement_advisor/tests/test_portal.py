# -*- coding: utf-8 -*-

import re

from odoo import Command
from odoo.tests import HttpCase, tagged

from .common import ProcurementCommon


@tagged('post_install', '-at_install')
class TestProcurementPortal(HttpCase, ProcurementCommon):

    def setUp(self):
        super().setUp()
        self.request = self._create_request(budget=31234.0)
        self.request._ai_tool_set_items(self._laptop_items())
        self.request.line_ids[0].target_unit_price = 987.65
        self.request.action_approve()
        self.env['procurement.send.wizard'].create({
            'request_id': self.request.id,
            'vendor_ids': [Command.set(self.vendor_a.ids)],
            'send_email': False,
        }).action_send()
        self.rfq = self.request.rfq_ids
        self.url = self.rfq.get_procurement_quote_url().replace(self.rfq.get_base_url(), '')

    def test_quote_form_access(self):
        response = self.url_open(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('RAM', response.text)
        # hidden budget: neither the budget nor the target prices reach the vendor
        self.assertNotIn('31,234', response.text)
        self.assertNotIn('31234', response.text)
        self.assertNotIn('987', response.text)

        self.assertEqual(self.url_open(f'/procurement/quote/{self.rfq.id}').status_code, 404)
        self.assertEqual(self.url_open(f'/procurement/quote/{self.rfq.id}?access_token=wrong').status_code, 404)

    def test_submit_quote(self):
        page = self.url_open(self.url).text
        csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        laptop_line, dock_line = self.rfq.order_line
        ram = self.rfq.procurement_compliance_ids.filtered(lambda c: c.attribute_id.name == 'RAM')
        data = {
            'csrf_token': csrf_token,
            f'price_{laptop_line.id}': '949,90',
            f'price_{dock_line.id}': '139.5',
            f'compliance_{ram.id}': 'alternative',
            f'compliance_comment_{ram.id}': '32 GB at the same price',
            'delivery_cost': '150',
            'installation_cost': '',
            'vendor_lead_days': '12',
            'warranty_months': '24',
            'vendor_quote_notes': 'Valid 30 days',
        }
        response = self.url_open(
            self.url.replace('/procurement/quote/%s' % self.rfq.id, '/procurement/quote/%s/submit' % self.rfq.id),
            data=data, files={'quote_file': ('offer.pdf', b'%PDF-1.4 test', 'application/pdf')})
        self.assertEqual(response.status_code, 200)
        self.assertIn('your quote was received', response.text)

        self.assertTrue(self.rfq.quote_submitted)
        self.assertEqual(laptop_line.price_unit, 949.90)
        self.assertEqual(dock_line.price_unit, 139.5)
        self.assertEqual(ram.status, 'alternative')
        self.assertEqual(ram.vendor_comment, '32 GB at the same price')
        self.assertEqual(self.rfq.vendor_lead_days, 12)
        self.assertEqual(self.rfq.warranty_months, 24)
        self.assertEqual(self.rfq.delivery_cost, 150)
        self.assertTrue(self.rfq.message_ids.attachment_ids.filtered(lambda a: a.name == 'offer.pdf'))
        self.assertEqual(self.rfq.procurement_rank, 1)

    def test_submit_requires_lead_time(self):
        page = self.url_open(self.url).text
        csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        submit_url = self.url.replace('/procurement/quote/%s' % self.rfq.id, '/procurement/quote/%s/submit' % self.rfq.id)
        response = self.url_open(submit_url, data={'csrf_token': csrf_token, 'vendor_lead_days': ''})
        self.assertIn('error=lead_time', response.url)
        self.assertFalse(self.rfq.quote_submitted)
        response = self.url_open(submit_url, data={'csrf_token': csrf_token, 'vendor_lead_days': '5', 'delivery_cost': 'abc'})
        self.assertIn('error=invalid', response.url)
        self.assertFalse(self.rfq.quote_submitted)

    def test_open_quote_form_action(self):
        action = self.rfq.action_open_procurement_quote_form()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertIn(f'/procurement/quote/{self.rfq.id}?access_token={self.rfq.access_token}', action['url'])
