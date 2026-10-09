# -*- coding: utf-8 -*-

from odoo import api, fields, models
from odoo.exceptions import UserError


class ProcurementSendWizard(models.TransientModel):
    _name = 'procurement.send.wizard'
    _description = "Send a Procurement Request to Vendors"

    request_id = fields.Many2one('procurement.request', required=True, ondelete='cascade')
    vendor_ids = fields.Many2many('res.partner', string="Vendors", required=True)
    invited_vendor_ids = fields.Many2many(
        'res.partner', string="Already Invited", compute='_compute_invited_vendor_ids')
    send_email = fields.Boolean(
        "Email the Vendors", default=True,
        help="Send each vendor the specification PDF and the link to the quote form.")
    budget_warning = fields.Char(related='request_id.budget_warning')

    @api.depends('request_id')
    def _compute_invited_vendor_ids(self):
        for wizard in self:
            wizard.invited_vendor_ids = wizard.request_id.rfq_ids.filtered(lambda o: o.state != 'cancel').partner_id

    def action_send(self):
        self.ensure_one()
        request = self.request_id
        if request.state not in ('approved', 'sent'):
            raise UserError(self.env._("Approve the specification before sending it to vendors."))
        vendors = self.vendor_ids - self.invited_vendor_ids
        if not vendors:
            raise UserError(self.env._("All the selected vendors already received this request."))
        if self.send_email and (no_email := vendors.filtered(lambda p: not p.email)):
            raise UserError(self.env._(
                "These vendors have no email address: %s", ", ".join(no_email.mapped('display_name'))))

        rfqs = request._create_rfqs(vendors)
        if self.send_email:
            for rfq in rfqs:
                rfq.with_context(mark_rfq_as_sent=True).message_post_with_source(
                    'cp_ai_procurement_advisor.mail_template_procurement_rfq',
                    subtype_xmlid='mail.mt_comment',
                )
        request.write({
            'state': 'sent',
            'vendor_ids': [fields.Command.link(vendor.id) for vendor in vendors],
        })
        return request.action_view_rfqs()
