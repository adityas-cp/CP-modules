# -*- coding: utf-8 -*-

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.cp_ai_procurement_advisor.models.ai_agent import PROCUREMENT_TOOL_XMLIDS

from .common import ProcurementCommon


@tagged('post_install', '-at_install')
class TestProcurementAITools(ProcurementCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent = cls.env.ref('cp_ai_procurement_advisor.ai_agent_procurement_advisor')

    def _run_tool(self, request, xmlid, **args):
        """Run a tool the way ai.session does: sudo tool, record and env of the user."""
        tool = self.env(user=self.buyer)['ir.actions.server'].sudo().browse(self.env.ref(xmlid).id)
        return tool._ai_tool_run(request, args, {})

    def test_agent_has_procurement_tools(self):
        tools = self.agent._get_default_tools()
        for xmlid in PROCUREMENT_TOOL_XMLIDS:
            self.assertIn(self.env.ref(xmlid), tools)
        default_agent_tools = self.env.ref('ai.ai_default_agent')._get_default_tools()
        self.assertFalse(default_agent_tools & self.env.ref(PROCUREMENT_TOOL_XMLIDS[0]))

    def test_composer_routes_request_chat_to_agent(self):
        request = self._create_request()
        composer = self.env['ai.composer']._get_composer_from_key_and_model('chatter_ai_button', 'procurement.request')
        self.assertEqual(composer, self.env.ref('cp_ai_procurement_advisor.ai_composer_procurement_request'))
        self.assertEqual(composer._get_agent('procurement.request', request.id), self.agent)
        context_text = "\n".join(
            part['text'] for part in composer._get_initial_context('procurement.request', request.id))
        self.assertIn(request.name, context_text)
        self.assertIn('Laptops for 25 new hires', context_text)

    def test_interview_through_tools(self):
        request = self._create_request(title='New hires equipment', budget=0, deadline=False)
        self._run_tool(
            request, 'cp_ai_procurement_advisor.ai_tool_procurement_save_context',
            title='Laptops for 25 new hires', need_summary='Office laptops for the sales team.',
            budget=30000, deadline=str(self.deadline), product_category='Goods')
        self.assertEqual(request.title, 'Laptops for 25 new hires')
        self.assertEqual(request.budget, 30000)
        self.assertEqual(request.deadline, self.deadline)
        self.assertEqual(request.product_category_id, self.env.ref('product.product_category_goods'))

        result = self._run_tool(
            request, 'cp_ai_procurement_advisor.ai_tool_procurement_set_items', items=self._laptop_items())
        self.assertEqual(len(request.line_ids), 2)
        laptop = request.line_ids[0]
        self.assertEqual(laptop.quantity, 25)
        self.assertEqual(laptop.attribute_ids.mapped('name'), ['RAM', 'Keyboard layout'])
        self.assertEqual(laptop.attribute_ids.mapped('mandatory'), [True, False])
        # mandatory defaults to True when the AI does not say
        self.assertTrue(request.line_ids[1].attribute_ids.mandatory)
        self.assertEqual(request.estimated_total, 25 * 1000 + 25 * 150)
        self.assertIn('Estimated cost', result)

        self._run_tool(
            request, 'cp_ai_procurement_advisor.ai_tool_procurement_set_requirements',
            requirements=[{'name': 'Delivered configured with our OS image'}])
        self.assertEqual(request.requirement_ids.name, 'Delivered configured with our OS image')

        # set_items replaces the list instead of appending
        self._run_tool(
            request, 'cp_ai_procurement_advisor.ai_tool_procurement_set_items', items=self._laptop_items()[:1])
        self.assertEqual(len(request.line_ids), 1)

        status = self._run_tool(request, 'cp_ai_procurement_advisor.ai_tool_procurement_check_completeness')
        self.assertNotIn('Blocking gaps', status)

        self._run_tool(request, 'cp_ai_procurement_advisor.ai_tool_procurement_mark_ready')
        self.assertEqual(request.state, 'review')

    def test_mark_ready_refused_with_gaps(self):
        request = self._create_request(deadline=False)
        result = self._run_tool(request, 'cp_ai_procurement_advisor.ai_tool_procurement_mark_ready')
        self.assertIn('Not ready', result)
        self.assertIn('No items', result)
        self.assertIn('No deadline', result)
        self.assertEqual(request.state, 'draft')

    def test_unknown_unit_kept_in_description(self):
        request = self._create_request()
        self._run_tool(request, 'cp_ai_procurement_advisor.ai_tool_procurement_set_items', items=[{
            'name': 'Polo shirt with embroidered logo',
            'quantity': 40,
            'unit': 'shirts-per-box',
            'attributes': [{'name': 'Fabric', 'value': 'Pique cotton 220 g/m2'}],
        }])
        line = request.line_ids
        self.assertEqual(line.uom_id, self.env.ref('uom.product_uom_unit'))
        self.assertIn('shirts-per-box', line.description)

    def test_tools_refuse_after_approval(self):
        request = self._create_request()
        self._run_tool(request, 'cp_ai_procurement_advisor.ai_tool_procurement_set_items', items=self._laptop_items())
        request.action_approve()
        with self.assertRaises(UserError):
            self._run_tool(request, 'cp_ai_procurement_advisor.ai_tool_procurement_set_items', items=self._laptop_items())

    def test_tool_needs_a_request(self):
        with self.assertRaises(UserError):
            self._run_tool(
                self.env['procurement.request'], 'cp_ai_procurement_advisor.ai_tool_procurement_check_completeness')

    def test_tool_runs_on_session_record(self):
        """A chat launched from the request runs the tools on that request."""
        request = self._create_request()
        res = self.agent.with_user(self.buyer).action_launch_ai_chat(
            'chatter_ai_button', 'procurement.request', request.id)
        session = self.env['ai.session'].sudo().search([('channel_id', '=', res['ai_channel_id'])])
        self.assertEqual(session.agent_id, self.agent)
        session = session.with_user(self.buyer)
        record = session._get_related_record()
        self.assertEqual(record, request)

        state = {}
        session._initialize_tool_state(state)
        tools = self.env['ir.actions.server'].sudo().browse(state['available_tools'])
        tools_by_name = {tool.ai_tool_name: tool for tool in tools}
        self.assertIn('procurement_set_items', tools_by_name)

        tool_call = {'name': 'procurement_set_items', 'args': {'items': self._laptop_items()}, 'call_id': 'call_1'}
        items = list(session._handle_tool_calls(
            [tool_call], tools_by_name, session._build_tools_context(state), record))
        tool_results = items[-1]['tool_results']
        self.assertTrue(tool_results[0]['success'], tool_results[0]['result'])
        self.assertEqual(len(request.line_ids), 2)

    def test_action_start_ai_chat(self):
        request = self._create_request()
        action = request.action_start_ai_chat()
        self.assertEqual(action['tag'], 'cp_procurement_open_ai_chat')
        self.assertEqual(action['params']['res_id'], request.id)
        self.assertIn(request.title, action['params']['user_message'])
