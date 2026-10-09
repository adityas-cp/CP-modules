# -*- coding: utf-8 -*-

from odoo import models

PROCUREMENT_TOOL_XMLIDS = (
    'cp_ai_procurement_advisor.ai_tool_procurement_save_context',
    'cp_ai_procurement_advisor.ai_tool_procurement_set_items',
    'cp_ai_procurement_advisor.ai_tool_procurement_set_requirements',
    'cp_ai_procurement_advisor.ai_tool_procurement_check_completeness',
    'cp_ai_procurement_advisor.ai_tool_procurement_mark_ready',
)


class AIAgent(models.Model):
    _inherit = 'ai.agent'

    def _get_default_tools(self):
        """The Procurement Advisor always has its tools: the whole interview relies on them,
        so they are not hidden behind a skill the model would first have to load."""
        tools = super()._get_default_tools()
        if self == self.env.ref('cp_ai_procurement_advisor.ai_agent_procurement_advisor', raise_if_not_found=False):
            for xmlid in PROCUREMENT_TOOL_XMLIDS:
                if tool := self.env.ref(xmlid, raise_if_not_found=False):
                    tools |= tool
        return tools
