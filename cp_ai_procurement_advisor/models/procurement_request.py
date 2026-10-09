# -*- coding: utf-8 -*-

from odoo import Command, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_amount

from .scoring import FACTORS, WEIGHT_PRESETS, score_compliance, score_quotes

EDITABLE_STATES = ('draft', 'review')
SCORING_FIELDS = {
    'deadline', 'strict_deadline', 'scoring_preset',
    'weight_price', 'weight_delivery', 'weight_spec', 'weight_performance', 'weight_warranty',
}
BUDGET_TIGHT_RATIO = 0.9


class ProcurementRequest(models.Model):
    _name = 'procurement.request'
    _description = "Procurement Request"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(
        "Reference", required=True, copy=False, readonly=True, index='trigram',
        default=lambda self: self.env._("New"))
    title = fields.Char("What do you need?", required=True, tracking=True)
    need_summary = fields.Text("Need Summary", help="Context of the purchase, as understood from the interview.")
    state = fields.Selection([
        ('draft', "Defining Needs"),
        ('review', "Items to Review"),
        ('approved', "Specification Approved"),
        ('sent', "Sent to Vendors"),
        ('done', "Ordered"),
        ('cancel', "Cancelled"),
    ], string="Status", default='draft', required=True, copy=False, tracking=True, index=True)
    user_id = fields.Many2one(
        'res.users', string="Buyer", default=lambda self: self.env.user, tracking=True,
        domain=[('share', '=', False)])
    company_id = fields.Many2one('res.company', required=True, index=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    product_category_id = fields.Many2one('product.category', string="Product Category")
    budget = fields.Monetary(
        "Budget", tracking=True,
        help="Internal only: the budget is never shown to vendors so they do not quote up to it.")
    deadline = fields.Date("Needed By", tracking=True)
    strict_deadline = fields.Boolean(
        "Strict Deadline", help="Quotes delivering after the deadline get 0 for delivery.")
    delivery_location = fields.Char("Delivery Location")
    approval_date = fields.Date("Approved On", readonly=True, copy=False)

    line_ids = fields.One2many('procurement.request.line', 'request_id', string="Items", copy=True)
    requirement_ids = fields.One2many(
        'procurement.request.requirement', 'request_id', string="General Requirements", copy=True)
    vendor_ids = fields.Many2many('res.partner', string="Invited Vendors", copy=False)
    rfq_ids = fields.One2many('purchase.order', 'procurement_request_id', string="Quotations")
    rfq_count = fields.Integer(compute='_compute_rfq_stats')
    quote_count = fields.Integer("Quotes Received", compute='_compute_rfq_stats')
    best_rfq_id = fields.Many2one('purchase.order', string="Best Quote", compute='_compute_rfq_stats')

    estimated_total = fields.Monetary("Estimated Cost", compute='_compute_budget_status')
    budget_status = fields.Selection([
        ('no_budget', "No Budget"),
        ('no_estimate', "No Estimate"),
        ('ok', "Within Budget"),
        ('tight', "Tight"),
        ('over', "Over Budget"),
    ], compute='_compute_budget_status')
    budget_warning = fields.Char(compute='_compute_budget_status')
    completeness_issues = fields.Text(compute='_compute_completeness_issues')

    scoring_preset = fields.Selection([
        ('balanced', "Balanced"),
        ('cheapest', "Cheapest"),
        ('urgent', "Urgent"),
        ('quality', "Quality First"),
        ('custom', "Custom"),
    ], string="Priority", default='balanced', required=True)
    weight_price = fields.Integer("Price Weight", compute='_compute_weights', store=True, readonly=False)
    weight_delivery = fields.Integer("Delivery Weight", compute='_compute_weights', store=True, readonly=False)
    weight_spec = fields.Integer("Spec Match Weight", compute='_compute_weights', store=True, readonly=False)
    weight_performance = fields.Integer("Past Performance Weight", compute='_compute_weights', store=True, readonly=False)
    weight_warranty = fields.Integer("Warranty Weight", compute='_compute_weights', store=True, readonly=False)

    _check_budget_positive = models.Constraint('CHECK(budget >= 0)', "The budget cannot be negative.")

    # ------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', self.env._("New")) == self.env._("New"):
                vals['name'] = self.env['ir.sequence'].next_by_code('procurement.request') or self.env._("New")
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        if SCORING_FIELDS & vals.keys():
            self.filtered('rfq_ids')._recompute_quote_scores()
        return res

    # ------------------------------------------------------------
    # COMPUTE
    # ------------------------------------------------------------

    @api.depends('scoring_preset')
    def _compute_weights(self):
        for request in self:
            preset = WEIGHT_PRESETS.get(request.scoring_preset)
            if not preset:
                # custom: keep whatever the user typed
                continue
            for factor in FACTORS:
                request[f'weight_{factor}'] = preset[factor]

    @api.depends('rfq_ids.state', 'rfq_ids.quote_submitted', 'rfq_ids.procurement_score')
    def _compute_rfq_stats(self):
        for request in self:
            rfqs = request.rfq_ids.filtered(lambda o: o.state != 'cancel')
            quotes = rfqs.filtered('quote_submitted')
            request.rfq_count = len(rfqs)
            request.quote_count = len(quotes)
            request.best_rfq_id = quotes.sorted('procurement_score', reverse=True)[:1]

    @api.depends('budget', 'line_ids.target_subtotal', 'currency_id')
    def _compute_budget_status(self):
        for request in self:
            estimate = sum(request.line_ids.mapped('target_subtotal'))
            request.estimated_total = estimate
            request.budget_warning = False
            if not request.budget:
                request.budget_status = 'no_budget'
            elif not estimate:
                request.budget_status = 'no_estimate'
            elif estimate > request.budget:
                request.budget_status = 'over'
                request.budget_warning = self.env._(
                    "The estimated cost (%(estimate)s) exceeds the budget (%(budget)s) by %(percent)s%%. "
                    "Vendors are unlikely to quote within budget: reduce quantities, relax the specification or raise the budget.",
                    estimate=format_amount(self.env, estimate, request.currency_id),
                    budget=format_amount(self.env, request.budget, request.currency_id),
                    percent=round((estimate / request.budget - 1) * 100),
                )
            elif estimate > request.budget * BUDGET_TIGHT_RATIO:
                request.budget_status = 'tight'
                request.budget_warning = self.env._(
                    "The estimated cost (%(estimate)s) uses more than 90%% of the budget (%(budget)s): "
                    "delivery, installation or price variations may push it over.",
                    estimate=format_amount(self.env, estimate, request.currency_id),
                    budget=format_amount(self.env, request.budget, request.currency_id),
                )
            else:
                request.budget_status = 'ok'

    @api.depends(
        'title', 'deadline', 'state', 'line_ids.name', 'line_ids.quantity',
        'line_ids.description', 'line_ids.attribute_ids')
    def _compute_completeness_issues(self):
        for request in self:
            issues = request._get_completeness_issues() if request.state in EDITABLE_STATES else []
            request.completeness_issues = "\n".join(issues) or False

    # ------------------------------------------------------------
    # BUSINESS
    # ------------------------------------------------------------

    def _get_completeness_issues(self):
        """Blocking problems: the specification cannot be approved while any remains."""
        self.ensure_one()
        _ = self.env._
        issues = []
        if not self.line_ids:
            issues.append(_("No items: list what has to be bought."))
        if not self.deadline:
            issues.append(_("No deadline: say when the goods or services are needed."))
        elif self.deadline < fields.Date.context_today(self):
            issues.append(_("The deadline is in the past."))
        for line in self.line_ids:
            if line.quantity <= 0:
                issues.append(_("Item “%s” has no quantity.", line.name))
            if not line.attribute_ids and not line.description:
                issues.append(_("Item “%s” has no specification: vendors cannot quote it reliably.", line.name))
        return issues

    def _get_completeness_warnings(self):
        """Non-blocking gaps the buyer should be aware of."""
        self.ensure_one()
        _ = self.env._
        warnings = []
        if not self.budget:
            warnings.append(_("No budget set: the budget check cannot run."))
        if not self.delivery_location:
            warnings.append(_("No delivery location."))
        if self.line_ids and not any(self.line_ids.mapped('target_unit_price')):
            warnings.append(_("No target prices: the cost cannot be estimated."))
        if self.budget_warning:
            warnings.append(self.budget_warning)
        return warnings

    def _check_editable(self):
        for request in self:
            if request.state not in EDITABLE_STATES:
                raise UserError(self.env._(
                    "Request %(name)s is %(state)s: its items can no longer be changed.",
                    name=request.name,
                    state=dict(request._fields['state']._description_selection(self.env))[request.state],
                ))

    def _get_rfq_line_vals(self, line):
        generic_product = self.env.ref('cp_ai_procurement_advisor.product_procurement_item')
        product = line.product_id or generic_product
        uom = line.uom_id if line.product_id and line.uom_id in product._get_available_uoms() else product.uom_id
        date_planned = fields.Datetime.to_datetime(self.deadline) if self.deadline else fields.Datetime.now()
        return {
            'product_id': product.id,
            'name': line._get_vendor_description(),
            'product_qty': line.quantity,
            'uom_id': uom.id,
            'price_unit': 0.0,
            'date_planned': date_planned,
            'procurement_line_id': line.id,
        }

    def _create_rfqs(self, vendors):
        """Create one RFQ per vendor, grouped as alternatives of each other."""
        self.ensure_one()
        existing_rfqs = self.rfq_ids.filtered(lambda o: o.state != 'cancel')
        vals_list = []
        for vendor in vendors:
            vals_list.append({
                'partner_id': vendor.id,
                'procurement_request_id': self.id,
                'origin': self.name,
                'company_id': self.company_id.id,
                'user_id': self.user_id.id,
                'currency_id': (vendor.property_purchase_currency_id or self.company_id.currency_id).id,
                'payment_term_id': vendor.property_supplier_payment_term_id.id,
                'order_line': [Command.create(self._get_rfq_line_vals(line)) for line in self.line_ids],
            })
        rfqs = self.env['purchase.order'].create(vals_list)

        compliance_vals = []
        for rfq in rfqs:
            for po_line in rfq.order_line.filtered('procurement_line_id'):
                compliance_vals += [{
                    'order_id': rfq.id,
                    'order_line_id': po_line.id,
                    'attribute_id': attribute.id,
                } for attribute in po_line.procurement_line_id.attribute_ids]
            compliance_vals += [{
                'order_id': rfq.id,
                'requirement_id': requirement.id,
            } for requirement in self.requirement_ids]
        self.env['procurement.quote.compliance'].create(compliance_vals)

        all_rfqs = existing_rfqs | rfqs
        if len(all_rfqs) > 1:
            group = existing_rfqs.purchase_group_id[:1]
            if group:
                group.order_ids |= rfqs
            else:
                self.env['purchase.order.group'].create({'order_ids': [Command.set(all_rfqs.ids)]})
        return rfqs

    def _get_quote_spec_score(self, order):
        """Average compliance score of the quote over all items (and general requirements)."""
        self.ensure_one()
        compliances = order.procurement_compliance_ids
        scores = []
        for line in self.line_ids:
            po_lines = order.order_line.filtered(lambda pol: pol.procurement_line_id == line)
            if not po_lines or not any(po_lines.mapped('price_unit')):
                scores.append(0.0)
                continue
            answers = [
                (c.status, c.attribute_id.mandatory)
                for c in compliances if c.order_line_id in po_lines and c.attribute_id
            ]
            scores.append(score_compliance(answers))
        if self.requirement_ids:
            scores.append(score_compliance([
                (c.status, c.requirement_id.mandatory) for c in compliances if c.requirement_id
            ]))
        return sum(scores) / len(scores) if scores else 0.0

    def _recompute_quote_scores(self):
        for request in self:
            quotes = request.rfq_ids.filtered(lambda o: o.quote_submitted and o.state != 'cancel')
            (request.rfq_ids - quotes).write({
                'score_price': 0, 'score_delivery': 0, 'score_spec': 0, 'score_performance': 0,
                'score_warranty': 0, 'procurement_score': 0, 'procurement_rank': 0,
            })
            if not quotes:
                continue
            weights = {factor: request[f'weight_{factor}'] for factor in FACTORS}
            results = score_quotes(
                [{
                    'key': quote.id,
                    'total': quote.procurement_total_cc,
                    'coverage': quote._get_procurement_coverage(),
                    'delivery_date': quote.procurement_delivery_date,
                    'spec': request._get_quote_spec_score(quote),
                    'on_time_rate': quote.partner_id.on_time_rate,
                    'warranty_months': quote.warranty_months,
                } for quote in quotes],
                weights,
                deadline=request.deadline,
                strict_deadline=request.strict_deadline,
            )
            for quote in quotes:
                result = results[quote.id]
                quote.write({
                    'score_price': result['price'],
                    'score_delivery': result['delivery'],
                    'score_spec': result['spec'],
                    'score_performance': result['performance'],
                    'score_warranty': result['warranty'],
                    'procurement_score': result['total'],
                    'procurement_rank': result['rank'],
                })

    # ------------------------------------------------------------
    # ACTIONS
    # ------------------------------------------------------------

    def action_start_ai_chat(self):
        self.ensure_one()
        self._check_editable()
        if self.line_ids:
            user_message = self.env._("Let's continue refining the specification of this request.")
        else:
            user_message = self.env._("Help me write the specification for: %s", self.title)
        return {
            'type': 'ir.actions.client',
            'tag': 'cp_procurement_open_ai_chat',
            'params': {
                'res_model': self._name,
                'res_id': self.id,
                'channel_title': f"{self.name} - {self.title}",
                'user_message': user_message,
            },
        }

    def action_submit_review(self):
        self._check_editable()
        self.write({'state': 'review'})

    def action_approve(self):
        for request in self:
            request._check_editable()
            if issues := request._get_completeness_issues():
                raise UserError(self.env._(
                    "The specification of %(name)s is not complete:\n- %(issues)s",
                    name=request.name, issues="\n- ".join(issues)))
        self.write({'state': 'approved', 'approval_date': fields.Date.context_today(self)})

    def action_reset_to_draft(self):
        for request in self:
            if request.rfq_ids.filtered(lambda o: o.state != 'cancel'):
                raise UserError(self.env._(
                    "Request %s was already sent to vendors: cancel their quotations first.", request.name))
        self.write({'state': 'draft', 'approval_date': False})

    def action_cancel(self):
        self.rfq_ids.filtered(lambda o: o.state in ('draft', 'sent', 'to approve')).button_cancel()
        self.write({'state': 'cancel'})

    def action_print_spec(self):
        return self.env.ref('cp_ai_procurement_advisor.action_report_procurement_spec').report_action(self)

    def action_open_send_wizard(self):
        self.ensure_one()
        if self.state not in ('approved', 'sent'):
            raise UserError(self.env._("Approve the specification before sending it to vendors."))
        return {
            'name': self.env._("Send to Vendors"),
            'type': 'ir.actions.act_window',
            'res_model': 'procurement.send.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_request_id': self.id},
        }

    def action_view_rfqs(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('purchase.purchase_rfq')
        action.update({
            'domain': [('procurement_request_id', '=', self.id)],
            'context': {'default_procurement_request_id': self.id, 'create': False},
        })
        return action

    def action_view_ranking(self):
        self.ensure_one()
        self._recompute_quote_scores()
        return {
            'name': self.env._("Quote Ranking - %s", self.title),
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'views': [
                (self.env.ref('cp_ai_procurement_advisor.purchase_order_view_procurement_ranking').id, 'list'),
                (False, 'form'),
            ],
            'domain': [('procurement_request_id', '=', self.id), ('state', '!=', 'cancel')],
            'context': {'create': False},
        }

    def action_create_products(self):
        """Turn items without a product into real products, for buyers who want them in the catalog."""
        for line in self.line_ids.filtered(lambda l: not l.product_id):
            line.product_id = self.env['product.product'].create({
                'name': line.name,
                'type': 'consu',
                'purchase_ok': True,
                'uom_id': line.uom_id.id or self.env.ref('uom.product_uom_unit').id,
                'categ_id': (self.product_category_id or self.env.ref('product.product_category_goods')).id,
                'description_purchase': line._get_vendor_description(),
            })

    # ------------------------------------------------------------
    # AI
    # ------------------------------------------------------------

    def _ai_serialize_request(self):
        """Plain-text snapshot of the request given to the AI agent."""
        self.ensure_one()
        lines = [
            f"# Procurement request {self.name}",
            f"- Need: {self.title}",
            f"- Status: {dict(self._fields['state']._description_selection(self.env))[self.state]}",
            f"- Summary: {self.need_summary or 'not written yet'}",
            f"- Product category: {self.product_category_id.complete_name or 'not set'}",
            f"- Budget (internal, never shown to vendors): "
            f"{format_amount(self.env, self.budget, self.currency_id) if self.budget else 'not set'}",
            f"- Needed by: {self.deadline or 'not set'} (today is {fields.Date.context_today(self)})",
            f"- Delivery location: {self.delivery_location or 'not set'}",
            "",
            f"## Items ({len(self.line_ids)})",
        ]
        for index, line in enumerate(self.line_ids, start=1):
            price = format_amount(self.env, line.target_unit_price, self.currency_id) if line.target_unit_price else "no target price"
            lines.append(f"{index}. {line.name} - {line.quantity:g} {line.uom_id.name or ''} - {price}")
            if line.description:
                lines.append(f"   {line.description}")
            for attribute in line.attribute_ids:
                lines.append(f"   - {attribute._get_display_text()} ({'mandatory' if attribute.mandatory else 'optional'})")
        if not self.line_ids:
            lines.append("No items yet.")
        lines += ["", "## General requirements"]
        lines += [
            f"- {req.name}: {req.description or ''} ({'mandatory' if req.mandatory else 'optional'})"
            for req in self.requirement_ids
        ] or ["None yet."]
        lines += [
            "",
            f"## Estimated cost: {format_amount(self.env, self.estimated_total, self.currency_id)}",
        ]
        if issues := self._get_completeness_issues():
            lines += ["", "## Blocking gaps"] + [f"- {issue}" for issue in issues]
        if warnings := self._get_completeness_warnings():
            lines += ["", "## Warnings"] + [f"- {warning}" for warning in warnings]
        return "\n".join(lines)

    def _ai_check_record(self):
        if not self:
            raise UserError(self.env._(
                "This tool only works in a chat opened from a procurement request "
                "(Purchase > AI Procurement > Requests > Start with AI)."))
        self.ensure_one()
        self._check_editable()

    def _ai_tool_save_context(self, title=None, need_summary=None, product_category=None,
                              budget=None, deadline=None, delivery_location=None):
        self._ai_check_record()
        vals = {}
        if title:
            vals['title'] = title
        if need_summary:
            vals['need_summary'] = need_summary
        if delivery_location:
            vals['delivery_location'] = delivery_location
        if budget is not None:
            if budget < 0:
                raise UserError(self.env._("The budget cannot be negative."))
            vals['budget'] = budget
        if deadline:
            try:
                vals['deadline'] = fields.Date.to_date(deadline)
            except ValueError:
                raise UserError(self.env._("Invalid deadline %s: use the YYYY-MM-DD format.", deadline))
        if product_category:
            category = self.env['product.category'].search([('name', '=ilike', product_category)], limit=1) \
                or self.env['product.category'].search([('complete_name', 'ilike', product_category)], limit=1)
            if category:
                vals['product_category_id'] = category.id
        self.write(vals)
        return self.env._("Saved.\n\n%s", self._ai_serialize_request())

    def _ai_tool_set_items(self, items):
        self._ai_check_record()
        if not items:
            raise UserError(self.env._("Give at least one item."))
        Uom = self.env['uom.uom']
        default_uom = self.env.ref('uom.product_uom_unit')
        line_vals = []
        for sequence, item in enumerate(items, start=1):
            description = item.get('description') or ''
            uom = default_uom
            if unit := (item.get('unit') or '').strip():
                uom = Uom.search([('name', '=ilike', unit)], limit=1)
                if not uom:
                    uom = default_uom
                    description = f"{description}\n{self.env._('Unit: %s', unit)}".strip()
            product = self.env['product.product'].search(
                [('name', '=ilike', item['name']), ('purchase_ok', '=', True)], limit=1)
            line_vals.append({
                'request_id': self.id,
                'sequence': sequence * 10,
                'name': item['name'],
                'description': description or False,
                'quantity': item.get('quantity') or 1.0,
                'uom_id': uom.id,
                'product_id': product.id if product and uom in product._get_available_uoms() else False,
                'target_unit_price': max(0.0, item.get('target_unit_price') or 0.0),
                'attribute_ids': [Command.create({
                    'sequence': attr_sequence * 10,
                    'name': attribute['name'],
                    'value': attribute['value'],
                    'unit': attribute.get('unit') or False,
                    'mandatory': attribute.get('mandatory', True) is not False,
                }) for attr_sequence, attribute in enumerate(item.get('attributes') or [], start=1)],
            })
        self.line_ids.unlink()
        self.env['procurement.request.line'].create(line_vals)
        return self.env._("Items saved.\n\n%s", self._ai_serialize_request())

    def _ai_tool_set_requirements(self, requirements):
        self._ai_check_record()
        self.requirement_ids.unlink()
        self.env['procurement.request.requirement'].create([{
            'request_id': self.id,
            'sequence': sequence * 10,
            'name': requirement['name'],
            'description': requirement.get('description') or False,
            'mandatory': requirement.get('mandatory', True) is not False,
        } for sequence, requirement in enumerate(requirements or [], start=1)])
        return self.env._("Requirements saved.\n\n%s", self._ai_serialize_request())

    def _ai_tool_check_completeness(self):
        self._ai_check_record()
        return self._ai_serialize_request()

    def _ai_tool_mark_ready(self):
        self._ai_check_record()
        if issues := self._get_completeness_issues():
            return self.env._(
                "Not ready, ask the user about these gaps first:\n- %s", "\n- ".join(issues))
        self.state = 'review'
        self.message_post(body=self.env._("The AI assistant prepared the item list: it is ready for review."))
        return self.env._(
            "The request is now in review. Tell the user to check the items in the form, "
            "adjust anything needed, then click “Approve Specification”.")
