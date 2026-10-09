# -*- coding: utf-8 -*-

from odoo import models


class AIComposer(models.Model):
    _inherit = 'ai.composer'

    def _get_initial_context(self, res_model=None, res_id=None, text_selection=None,
                             text_of_editable=None, reference_image_path=None):
        context_parts = super()._get_initial_context(
            res_model, res_id, text_selection, text_of_editable, reference_image_path)
        if self.interface_key == 'chatter_ai_button' and res_model == 'procurement.request' and res_id:
            request = self.env['procurement.request'].browse(res_id).exists()
            if request:
                context_parts.append({
                    'type': 'text',
                    'text': f"<current_procurement_request>\n{request._ai_serialize_request()}\n</current_procurement_request>",
                })
        return context_parts
