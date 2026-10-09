# -*- coding: utf-8 -*-

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import Form, tagged

from .common import ProcurementCommon


@tagged('post_install', '-at_install')
class TestProcurementFlow(ProcurementCommon):

    def _approved_request(self):
        request = self._create_request()
        request._ai_tool_set_items(self._laptop_items())
        request._ai_tool_set_requirements([{'name': 'Installation on site', 'mandatory': True}])
        request.action_approve()
        return request

    def _send(self, request, vendors, send_email=True):
        wizard = self.env['procurement.send.wizard'].with_user(self.buyer).create({
            'request_id': request.id,
            'vendor_ids': [Command.set(vendors.ids)],
            'send_email': send_email,
        })
        wizard.action_send()
        return request.rfq_ids.filtered(lambda o: o.partner_id in vendors)

    def _answer_all(self, rfq, status='met'):
        return {compliance.id: (status, '') for compliance in rfq.procurement_compliance_ids}

    def _quote(self, rfq, unit_prices, lead_days, warranty=12, status='met', **vals):
        lines = rfq.order_line.filtered('procurement_line_id')
        rfq.sudo()._procurement_submit_quote(
            dict(zip(lines.ids, unit_prices)),
            self._answer_all(rfq, status),
            {'vendor_lead_days': lead_days, 'warranty_months': warranty, **vals},
        )

    def test_approval_requires_complete_spec(self):
        request = self._create_request(deadline=False)
        with self.assertRaises(UserError):
            request.action_approve()
        request._ai_tool_set_items(self._laptop_items())
        with self.assertRaises(UserError):
            request.action_approve()  # still no deadline
        request.deadline = self.deadline
        request.action_approve()
        self.assertEqual(request.state, 'approved')

    def test_add_item_from_form(self):
        """Adding a line in the UI starts with no specification rows."""
        empty_line = self.env['procurement.request.line'].new({'name': 'Office chair'})
        self.assertEqual(empty_line.spec_summary, '')
        request = self._create_request()
        with Form(request) as request_form:
            with request_form.line_ids.new() as line_form:
                line_form.name = 'Office chair'
                line_form.quantity = 10
                with line_form.attribute_ids.new() as attribute_form:
                    attribute_form.name = 'Seat height'
                    attribute_form.value = '45-48'
                    attribute_form.unit = 'cm'
        self.assertEqual(request.line_ids.spec_summary, 'Seat height: 45-48 cm')

    def test_budget_check(self):
        request = self._create_request(budget=10000.0)
        self.assertEqual(request.budget_status, 'no_estimate')
        request._ai_tool_set_items(self._laptop_items())  # 28,750 estimated
        self.assertEqual(request.budget_status, 'over')
        self.assertTrue(request.budget_warning)
        request.budget = 30000.0
        self.assertEqual(request.budget_status, 'tight')
        request.budget = 50000.0
        self.assertEqual(request.budget_status, 'ok')
        self.assertFalse(request.budget_warning)

    def test_send_creates_grouped_rfqs_without_prices(self):
        request = self._approved_request()
        rfqs = self._send(request, self.vendor_a | self.vendor_b)
        self.assertEqual(request.state, 'sent')
        self.assertEqual(len(rfqs), 2)
        self.assertEqual(rfqs.purchase_group_id.order_ids, rfqs, "RFQs must be alternatives of each other")
        self.assertTrue(all(rfq.state == 'sent' for rfq in rfqs), "RFQs are marked as sent by the email")
        for rfq in rfqs:
            self.assertEqual(rfq.order_line.mapped('price_unit'), [0.0, 0.0])
            self.assertEqual(rfq.order_line.product_id, self.env.ref('cp_ai_procurement_advisor.product_procurement_item'))
            self.assertIn('RAM: min. 16 GB', rfq.order_line[0].name)
            # 3 item attributes + 1 general requirement
            self.assertEqual(len(rfq.procurement_compliance_ids), 4)
            message = rfq.message_ids.filtered(lambda m: m.body and '/procurement/quote/' in m.body)
            self.assertEqual(len(message), 1, "The RFQ email contains the link to the quote form")
            self.assertIn(rfq.access_token, message.body)
            # in tests reports are not rendered as PDF, only check the specification is attached
            self.assertTrue(message.attachment_ids.name.startswith(f'Specification - {rfq.name}.'))

        # invite one more vendor later: joins the same alternative group, no duplicates
        rfq_c = self._send(request, self.vendor_c, send_email=False)
        self.assertEqual(rfq_c.purchase_group_id, rfqs.purchase_group_id)
        with self.assertRaises(UserError):
            self._send(request, self.vendor_a)

    def test_ranking_and_order(self):
        request = self._approved_request()
        rfq_a = self._send(request, self.vendor_a, send_email=False)
        rfq_b = self._send(request, self.vendor_b, send_email=False)
        rfq_c = self._send(request, self.vendor_c, send_email=False)

        # A: good price, on time, meets the spec. B: much pricier, longer warranty.
        # C: cheapest but cannot meet the mandatory characteristics.
        self._quote(rfq_a, [900, 140], lead_days=10, warranty=12)
        self._quote(rfq_b, [1400, 200], lead_days=10, warranty=36, delivery_cost=200)
        self._quote(rfq_c, [850, 120], lead_days=10, warranty=12, status='not_met')

        self.assertEqual(rfq_c.score_price, 100.0)
        self.assertLess(rfq_a.score_price, 100.0)
        self.assertEqual(rfq_a.score_spec, 100.0)
        self.assertEqual(rfq_a.score_delivery, 100.0)
        self.assertEqual(rfq_c.score_spec, 0.0, "A mandatory characteristic not met gives 0 on that item")
        self.assertEqual(rfq_b.score_warranty, 100.0)
        self.assertEqual(rfq_a.procurement_rank, 1)
        self.assertEqual(request.best_rfq_id, rfq_a)
        self.assertEqual(request.quote_count, 3)

        # make delivery matter most and A late: ranking changes
        rfq_a.sudo()._procurement_submit_quote({}, {}, {'vendor_lead_days': 90})
        request.write({'scoring_preset': 'urgent', 'strict_deadline': True})
        self.assertEqual(rfq_a.score_delivery, 0.0)
        self.assertNotEqual(rfq_a.procurement_rank, 1)

        action = request.action_view_ranking()
        self.assertEqual(action['res_model'], 'purchase.order')

        rfq_b.with_context(skip_alternative_check=True).action_procurement_choose()
        self.assertEqual(rfq_b.state, 'purchase')
        self.assertEqual(request.state, 'done')

    def test_quote_coverage(self):
        request = self._approved_request()
        rfq_a = self._send(request, self.vendor_a, send_email=False)
        rfq_b = self._send(request, self.vendor_b, send_email=False)
        self._quote(rfq_a, [1000, 150], lead_days=5)
        self._quote(rfq_b, [700, 0], lead_days=5)  # docking stations not quoted
        self.assertEqual(rfq_b._get_procurement_coverage(), 0.5)
        self.assertGreater(rfq_a.score_price, rfq_b.score_price)

    def test_closed_rfq_cannot_be_quoted(self):
        request = self._approved_request()
        rfq = self._send(request, self.vendor_a, send_email=False)
        request.action_cancel()
        self.assertEqual(rfq.state, 'cancel')
        with self.assertRaises(UserError):
            self._quote(rfq, [1000, 100], lead_days=5)

    def test_reset_to_draft_blocked_once_sent(self):
        request = self._approved_request()
        self._send(request, self.vendor_a, send_email=False)
        with self.assertRaises(UserError):
            request.action_reset_to_draft()

    def test_create_products(self):
        self.buyer.group_ids |= self.env.ref('product.group_product_manager')
        request = self._create_request()
        request._ai_tool_set_items(self._laptop_items())
        request.action_create_products()
        self.assertTrue(all(request.line_ids.mapped('product_id')))
        self.assertEqual(request.line_ids[0].product_id.name, '14-inch business laptop')

    def test_spec_report_hides_budget(self):
        request = self._approved_request()
        request.line_ids[0].target_unit_price = 987.65
        rfq = self._send(request, self.vendor_a, send_email=False)
        for report_ref, record in (
            ('cp_ai_procurement_advisor.action_report_procurement_spec', request),
            ('cp_ai_procurement_advisor.action_report_procurement_rfq_spec', rfq),
        ):
            html = self.env['ir.actions.report']._render_qweb_html(report_ref, record.ids)[0].decode()
            self.assertIn('RAM', html)
            self.assertNotIn('987', html)
            self.assertNotIn('30,000', html)
            self.assertNotIn('30000', html)
