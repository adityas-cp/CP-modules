# -*- coding: utf-8 -*-

from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    procurement_request_id = fields.Many2one(
        'procurement.request', string="Procurement Request", index='btree_not_null', copy=False, readonly=True)
    procurement_compliance_ids = fields.One2many(
        'procurement.quote.compliance', 'order_id', string="Specification Answers")
    delivery_cost = fields.Monetary("Delivery Cost", copy=False)
    installation_cost = fields.Monetary("Installation Cost", copy=False)
    warranty_months = fields.Integer("Warranty (months)", copy=False)
    vendor_lead_days = fields.Integer("Lead Time (days)", copy=False, help="Days needed by the vendor to deliver.")
    vendor_quote_notes = fields.Text("Vendor Notes", copy=False)
    quote_submitted = fields.Boolean("Quote Received", copy=False, readonly=True)
    quote_date = fields.Datetime("Quote Date", copy=False, readonly=True)
    procurement_total_cc = fields.Monetary(
        "Total Cost", compute='_compute_procurement_total_cc', store=True, currency_field='company_currency_id',
        help="Quote total including delivery and installation, in company currency.")
    procurement_delivery_date = fields.Date(
        "Expected Delivery", compute='_compute_procurement_delivery_date', store=True)

    # Written by procurement.request._recompute_quote_scores, see scoring.py
    score_price = fields.Float("Price Score", readonly=True, copy=False, aggregator=None)
    score_delivery = fields.Float("Delivery Score", readonly=True, copy=False, aggregator=None)
    score_spec = fields.Float("Spec Match Score", readonly=True, copy=False, aggregator=None)
    score_performance = fields.Float("Past Performance Score", readonly=True, copy=False, aggregator=None)
    score_warranty = fields.Float("Warranty Score", readonly=True, copy=False, aggregator=None)
    procurement_score = fields.Float("Score", readonly=True, copy=False, aggregator=None)
    procurement_rank = fields.Integer("Rank", readonly=True, copy=False, aggregator=None)

    @api.depends('amount_total_cc', 'delivery_cost', 'installation_cost', 'currency_id', 'company_id', 'date_order')
    def _compute_procurement_total_cc(self):
        for order in self:
            extra = order.delivery_cost + order.installation_cost
            if extra and order.currency_id != order.company_currency_id:
                extra = order.currency_id._convert(
                    extra, order.company_currency_id, order.company_id, order.date_order or fields.Date.today())
            order.procurement_total_cc = order.amount_total_cc + extra

    @api.depends('quote_submitted', 'quote_date', 'vendor_lead_days')
    def _compute_procurement_delivery_date(self):
        for order in self:
            if order.quote_submitted and order.quote_date:
                order.procurement_delivery_date = order.quote_date.date() + timedelta(days=max(0, order.vendor_lead_days))
            else:
                order.procurement_delivery_date = False

    def _get_procurement_coverage(self):
        """Ratio of the request items this quote prices."""
        self.ensure_one()
        request_lines = self.procurement_request_id.line_ids
        if not request_lines:
            return 0.0
        quoted = self.order_line.filtered(lambda l: l.procurement_line_id and l.price_unit > 0).procurement_line_id
        return len(quoted & request_lines) / len(request_lines)

    def _procurement_quote_is_editable(self):
        self.ensure_one()
        return self.state in ('draft', 'sent') and self.procurement_request_id.state == 'sent'

    def get_procurement_quote_url(self):
        """Public URL of the vendor quote form (used in the RFQ email)."""
        self.ensure_one()
        return f"{self.get_base_url()}/procurement/quote/{self.id}?access_token={self._portal_ensure_token()}"

    def action_open_procurement_quote_form(self):
        """Open the vendor quote form, e.g. to check it or to send the link by another channel."""
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': self.get_procurement_quote_url(), 'target': 'new'}

    def _procurement_submit_quote(self, line_prices, compliance_answers, vals, attachments=()):
        """Store a quote submitted by the vendor.

        :param dict line_prices: purchase.order.line id -> unit price
        :param dict compliance_answers: procurement.quote.compliance id -> (status, comment)
        :param dict vals: purchase.order values (costs, lead time, warranty, notes)
        :param attachments: iterable of (filename, raw content, mimetype)
        """
        self.ensure_one()
        if not self._procurement_quote_is_editable():
            raise UserError(self.env._("This request for quotation is closed."))
        for line in self.order_line.filtered(lambda l: l.id in line_prices):
            line.price_unit = line_prices[line.id]
        for compliance in self.procurement_compliance_ids.filtered(lambda c: c.id in compliance_answers):
            status, comment = compliance_answers[compliance.id]
            compliance.write({'status': status or False, 'vendor_comment': comment or False})
        self.write({**vals, 'quote_submitted': True, 'quote_date': fields.Datetime.now()})

        attachment_ids = []
        for filename, content, mimetype in attachments:
            attachment_ids.append(self.env['ir.attachment'].create({
                'name': filename,
                'raw': content,
                'mimetype': mimetype,
                'res_model': self._name,
                'res_id': self.id,
            }).id)
        self.message_post(
            body=self.env._("Quote submitted by the vendor from the portal."),
            author_id=self.partner_id.id,
            attachment_ids=attachment_ids,
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        self.procurement_request_id.message_post(
            body=self.env._("%(vendor)s submitted a quote (%(order)s).",
                            vendor=self.partner_id.display_name, order=self.name),
        )
        self.procurement_request_id._recompute_quote_scores()

    def action_mark_quote_received(self):
        """For quotes received by email and typed in by the buyer."""
        orders = self.filtered(lambda o: o.procurement_request_id and o.state in ('draft', 'sent'))
        orders.write({'quote_submitted': True, 'quote_date': fields.Datetime.now()})
        orders.procurement_request_id._recompute_quote_scores()

    def action_procurement_choose(self):
        self.ensure_one()
        if not self.quote_submitted:
            raise UserError(self.env._("This vendor has not quoted yet."))
        return self.button_confirm()

    def button_approve(self, force=False):
        res = super().button_approve(force=force)
        for order in self.filtered(lambda o: o.procurement_request_id and o.state == 'purchase'):
            request = order.procurement_request_id
            if request.state != 'done':
                request.state = 'done'
                request.message_post(body=self.env._(
                    "Ordered from %(vendor)s with %(order)s.",
                    vendor=order.partner_id.display_name, order=order._get_html_link()))
        return res


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    procurement_line_id = fields.Many2one(
        'procurement.request.line', string="Procurement Item", index='btree_not_null', copy=False, readonly=True)
    procurement_compliance_ids = fields.One2many('procurement.quote.compliance', 'order_line_id')
