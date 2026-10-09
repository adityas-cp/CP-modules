# -*- coding: utf-8 -*-

from odoo import fields, models

from .ai_session import PARAM_API_KEY, PARAM_BASE_URL, PARAM_MODEL
from .llm_provider import DEFAULT_BASE_URL, DEFAULT_MODEL


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    cp_procurement_llm_api_key = fields.Char(
        "AI Provider API Key", config_parameter=PARAM_API_KEY,
        help="When set, the Procurement Advisor chat uses this provider instead of the Odoo AI service.")
    cp_procurement_llm_base_url = fields.Char(
        "AI Provider URL", config_parameter=PARAM_BASE_URL, default=DEFAULT_BASE_URL,
        help="Base URL of an OpenAI-compatible API (the part before /chat/completions).")
    cp_procurement_llm_model = fields.Char(
        "AI Model", config_parameter=PARAM_MODEL, default=DEFAULT_MODEL)
