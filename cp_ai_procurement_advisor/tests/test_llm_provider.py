# -*- coding: utf-8 -*-

import json
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.cp_ai_procurement_advisor.models import llm_provider
from odoo.addons.cp_ai_procurement_advisor.models.ai_session import PARAM_API_KEY

from .common import ProcurementCommon


class FakeResponse:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code
        self.text = json.dumps(data)

    def json(self):
        return self.data


def openai_tool_call(name, args, call_id='call_1'):
    return FakeResponse({'choices': [{'message': {
        'role': 'assistant',
        'content': None,
        'tool_calls': [{'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}],
    }}]})


def openai_text(text):
    return FakeResponse({'choices': [{'message': {'role': 'assistant', 'content': text}}]})


@tagged('post_install', '-at_install')
class TestLLMProviderFormat(TransactionCase):

    def test_schema_sanitized(self):
        schema = {
            'type': 'object',
            'properties': {
                'deadline': {'type': 'string', 'maxLength': 10, 'pattern': '^x$', 'description': 'd'},
                'items': {'type': 'array', 'items': {'type': 'object', 'properties': {
                    'name': {'type': ['string', 'null'], 'maxLength': 5}}, 'required': ['name']}},
            },
            'required': [],
        }
        clean = llm_provider.sanitize_schema(schema)
        self.assertEqual(clean['properties']['deadline'], {'type': 'string', 'description': 'd'})
        self.assertEqual(clean['properties']['items']['items']['properties']['name'], {'type': 'string'})

    def test_tools_without_parameters(self):
        tools = llm_provider.to_openai_tools([
            {'name': 'check', 'instructions': 'Check it', 'schema': {'type': 'object', 'properties': {}, 'required': []}},
        ])
        self.assertEqual(tools, [{'type': 'function', 'function': {'name': 'check', 'description': 'Check it'}}])

    def test_conversation_roundtrip(self):
        assistant = llm_provider.from_openai_response({'choices': [{'message': {
            'content': 'Saving',
            'tool_calls': [{
                'id': 'abc', 'type': 'function',
                'function': {'name': 'save', 'arguments': '{"title": "Laptops"}'},
                'extra_content': {'google': {'thought_signature': 'sig'}},
            }],
        }}]})
        self.assertEqual(assistant['content'][1]['args'], {'title': 'Laptops'})
        messages = llm_provider.to_openai_messages('Be nice', [
            {'role': 'user', 'content': [{'type': 'text', 'text': 'I need laptops'}]},
            assistant,
            {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_name': 'save', 'tool_call_id': 'abc',
                 'result': [{'type': 'text', 'text': 'Saved.'}], 'success': True},
                {'type': 'text', 'text': '<context/>'},
            ]},
        ])
        self.assertEqual([m['role'] for m in messages], ['system', 'user', 'assistant', 'tool', 'user'])
        self.assertEqual(messages[2]['tool_calls'][0]['function']['arguments'], '{"title": "Laptops"}')
        self.assertEqual(messages[2]['tool_calls'][0]['extra_content'], {'google': {'thought_signature': 'sig'}})
        self.assertEqual(messages[3], {'role': 'tool', 'tool_call_id': 'abc', 'content': 'Saved.'})

    def test_provider_error(self):
        with patch.object(llm_provider.requests, 'post', return_value=FakeResponse(
                [{'error': {'code': 400, 'message': 'API key not valid'}}], status_code=400)):
            with self.assertRaisesRegex(llm_provider.LLMProviderError, 'API key not valid'):
                llm_provider.chat_completion('bad', None, 'm', 'i', [])


@tagged('post_install', '-at_install')
class TestLLMProviderSession(ProcurementCommon):

    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_str(PARAM_API_KEY, 'test-key')
        self.agent = self.env.ref('cp_ai_procurement_advisor.ai_agent_procurement_advisor')
        self.request = self._create_request(title='New hires equipment', budget=0)
        res = self.agent.with_user(self.buyer).action_launch_ai_chat(
            'chatter_ai_button', 'procurement.request', self.request.id)
        self.session = self.env['ai.session'].sudo().search([('channel_id', '=', res['ai_channel_id'])])

    def _run_pending_request(self, response):
        """What the background thread does, in the test transaction."""
        session = self.env['ai.session']._cp_get_waiting_session(self.session.request_uuid)
        self.assertTrue(session, "A request must be waiting for the provider")
        payload = session.request_payload
        with patch.object(llm_provider.requests, 'post', return_value=response) as post:
            message = llm_provider.chat_completion(
                'test-key', None, 'test-model', payload['instructions'], payload['messages'], payload['tools'])
        self.env['ai.session']._cp_deliver_custom_result(session.request_uuid, message)
        return post.call_args.kwargs['json']

    def test_chat_runs_on_own_provider(self):
        self.session.with_user(self.buyer).sudo()._submit_agent_request(
            [{'type': 'text', 'text': 'I need 25 laptops for new hires, budget 30000'}])
        self.assertEqual(self.session.loop_state, 'waiting_model')

        sent = self._run_pending_request(openai_tool_call(
            'procurement_save_context', {'title': 'Laptops for 25 new hires', 'budget': 30000}))
        self.assertIn('procurement_set_items', [tool['function']['name'] for tool in sent['tools']])
        self.assertEqual(sent['model'], 'test-model')
        self.assertEqual(self.request.title, 'Laptops for 25 new hires')
        self.assertEqual(self.request.budget, 30000)
        # the tool result went back to the model in a new request
        self.assertEqual(self.session.loop_state, 'waiting_model')

        sent = self._run_pending_request(openai_text('Which processor class do you need?'))
        self.assertEqual(sent['messages'][-1]['role'], 'tool')
        self.assertEqual(sent['messages'][-2]['tool_calls'][0]['id'], sent['messages'][-1]['tool_call_id'])
        self.assertEqual(self.session.loop_state, 'ready')
        self.assertIn('Which processor class', self.session.channel_id.message_ids[0].body)

    def test_provider_error_shown_in_chat(self):
        self.session.with_user(self.buyer).sudo()._submit_agent_request([{'type': 'text', 'text': 'Hello'}])
        self.env['ai.session']._cp_deliver_custom_result(self.session.request_uuid, None, '429 - quota exceeded')
        self.assertEqual(self.session.loop_state, 'ready')
        self.assertIn('quota exceeded', self.session.channel_id.message_ids[0].body)

    def test_odoo_ai_used_without_key(self):
        self.env['ir.config_parameter'].sudo().set_str(PARAM_API_KEY, '')
        self.assertIsNone(self.session._cp_get_custom_llm_config())
        default_session = self.env['ai.session'].sudo().create({
            'agent_id': self.env.ref('ai.ai_default_agent').id,
            'channel_id': self.env.ref('ai.ai_default_agent')._create_ai_chat_channel().id,
        })
        self.env['ir.config_parameter'].sudo().set_str(PARAM_API_KEY, 'test-key')
        self.assertIsNone(default_session._cp_get_custom_llm_config(), "Other agents keep using Odoo AI")
