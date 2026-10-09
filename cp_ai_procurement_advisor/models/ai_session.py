# -*- coding: utf-8 -*-

import logging
import threading
import uuid

from odoo import api, models
from odoo.modules.registry import Registry

from .llm_provider import DEFAULT_BASE_URL, DEFAULT_MODEL, LLMProviderError, chat_completion

_logger = logging.getLogger(__name__)

PARAM_API_KEY = 'cp_ai_procurement_advisor.llm_api_key'
PARAM_BASE_URL = 'cp_ai_procurement_advisor.llm_base_url'
PARAM_MODEL = 'cp_ai_procurement_advisor.llm_model'


def _run_custom_request(dbname, uid, request_uuid, config):
    """Background job: call the provider outside any transaction, then continue
    the conversation like the Odoo AI webhook (/ai/completion_result_ready) does."""
    try:
        registry = Registry(dbname)
        with registry.cursor() as cr:
            session = api.Environment(cr, uid, {})['ai.session']._cp_get_waiting_session(request_uuid)
            if not session:
                return
            payload = session.request_payload
        message, error = None, None
        try:
            message = chat_completion(
                config['api_key'], config['base_url'], config['model'],
                payload.get('instructions'), payload.get('messages'), payload.get('tools'),
            )
        except LLMProviderError as e:
            _logger.warning("AI provider request %s failed: %s", request_uuid, e)
            error = str(e)
        with registry.cursor() as cr:
            api.Environment(cr, uid, {})['ai.session']._cp_deliver_custom_result(request_uuid, message, error)
    except Exception:
        _logger.exception("AI provider request %s crashed", request_uuid)


class AiSession(models.Model):
    _inherit = 'ai.session'

    def _cp_get_custom_llm_config(self):
        """Provider settings when this conversation must bypass the Odoo AI service."""
        self.ensure_one()
        agent = self.env.ref('cp_ai_procurement_advisor.ai_agent_procurement_advisor', raise_if_not_found=False)
        if not agent or self._root_session().agent_id != agent:
            return None
        params = self.env['ir.config_parameter'].sudo()
        api_key = params.get_str(PARAM_API_KEY)
        if not api_key:
            return None
        return {
            'api_key': api_key,
            'base_url': params.get_str(PARAM_BASE_URL) or DEFAULT_BASE_URL,
            'model': params.get_str(PARAM_MODEL) or DEFAULT_MODEL,
        }

    def _save_and_submit_request(self, payload, *, callback_type, request_round, request_round_limit, state):
        config = self._cp_get_custom_llm_config()
        if not config:
            return super()._save_and_submit_request(
                payload, callback_type=callback_type, request_round=request_round,
                request_round_limit=request_round_limit, state=state)

        if callback_type == 'channel_name':
            # the chat is already named after the request: spare the provider quota
            self.unlink()
            return

        # same state as the Odoo AI flow, so the chat UI and resumptions work unchanged
        guest = self.env.context.get('guest')
        request_uuid = str(uuid.uuid4())
        state['callback_type'] = callback_type
        self.write({
            'loop_state': 'waiting_model',
            'request_uuid': request_uuid,
            'request_webhook_secret': uuid.uuid4().hex,
            'request_round': request_round,
            'request_round_limit': request_round_limit,
            'request_payload': payload,
            'request_user_id': self.env.uid,
            'request_guest_id': guest.id if guest else False,
            'request_context': self._get_request_context_snapshot(),
            'resume_token': False,
            'pending_tool_call': False,
            'state': state,
        })
        self._publish_response_state()

        dbname, uid = self.env.cr.dbname, self.env.uid

        @self.env.cr.postcommit.add
        def submit():
            threading.Thread(
                target=_run_custom_request, args=(dbname, uid, request_uuid, config),
                name=f'cp_ai_procurement_llm_{request_uuid}', daemon=True,
            ).start()

    @api.model
    def _cp_get_waiting_session(self, request_uuid):
        return self.sudo().search([('request_uuid', '=', request_uuid), ('loop_state', '=', 'waiting_model')], limit=1)

    @api.model
    def _cp_deliver_custom_result(self, request_uuid, message, error=None):
        session = self._cp_get_waiting_session(request_uuid)
        if not session:
            return
        context = dict(session.request_context or {})
        context['discuss_channel'] = self.env['discuss.channel'].browse(session.channel_id.id)
        env = self.env(user=session.request_user_id.id, context=context, su=False)
        session = session.with_env(env).sudo()
        if error:
            session._finish_exchange('failed', content=env._("The AI provider returned an error: %s", error))
            return
        session._continue_agent_loop({'kind': 'success', 'message': message})
