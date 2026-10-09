# -*- coding: utf-8 -*-

from odoo import api, fields, models


class ProcurementRequestLine(models.Model):
    _name = 'procurement.request.line'
    _description = "Procurement Request Item"
    _order = 'request_id, sequence, id'

    request_id = fields.Many2one('procurement.request', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='request_id.company_id', store=True)
    currency_id = fields.Many2one(related='request_id.currency_id')
    sequence = fields.Integer(default=10)
    name = fields.Char("Item", required=True)
    description = fields.Text("Description")
    product_id = fields.Many2one(
        'product.product', string="Product", domain=[('purchase_ok', '=', True)], check_company=True,
        help="Optional: leave empty to order with a generic procurement item.")
    quantity = fields.Float("Quantity", digits='Product Unit', default=1.0, required=True)
    uom_id = fields.Many2one('uom.uom', string="Unit", default=lambda self: self.env.ref('uom.product_uom_unit'))
    target_unit_price = fields.Monetary(
        "Target Unit Price", help="Internal estimate used for the budget check, never shown to vendors.")
    target_subtotal = fields.Monetary("Target Subtotal", compute='_compute_target_subtotal', store=True)
    attribute_ids = fields.One2many(
        'procurement.request.line.attribute', 'line_id', string="Specification", copy=True)
    spec_summary = fields.Char("Specification Summary", compute='_compute_spec_summary')
    quantity_label = fields.Char("Quantity and Unit", compute='_compute_quantity_label',
                                 help="Quantity without useless decimals, e.g. '50 Units', '2.5 kg'.")

    _check_quantity_positive = models.Constraint('CHECK(quantity >= 0)', "The quantity cannot be negative.")

    @api.depends('quantity', 'target_unit_price')
    def _compute_target_subtotal(self):
        for line in self:
            line.target_subtotal = line.quantity * line.target_unit_price

    @api.depends('attribute_ids.name', 'attribute_ids.value', 'attribute_ids.unit')
    def _compute_spec_summary(self):
        for line in self:
            line.spec_summary = "; ".join(attribute._get_display_text() for attribute in line.attribute_ids)

    @api.depends('quantity', 'uom_id')
    def _compute_quantity_label(self):
        for line in self:
            line.quantity_label = f"{line.quantity:g} {line.uom_id.name or ''}".strip()

    def _get_vendor_description(self):
        """Line description sent to vendors (RFQ line, spec PDF)."""
        self.ensure_one()
        parts = [self.name]
        if self.description:
            parts.append(self.description)
        parts += [f"- {attribute._get_display_text()}" for attribute in self.attribute_ids]
        return "\n".join(parts)


class ProcurementRequestLineAttribute(models.Model):
    _name = 'procurement.request.line.attribute'
    _description = "Procurement Item Specification"
    _order = 'line_id, sequence, id'

    line_id = fields.Many2one('procurement.request.line', required=True, index=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char("Attribute", required=True, help="e.g. RAM, Fabric, Seat height, Certification")
    value = fields.Char("Required Value", required=True, help="e.g. 16, Cotton 200 g/m², 45, ISO 9001")
    unit = fields.Char("Unit", help="e.g. GB, cm, months")
    mandatory = fields.Boolean(
        "Mandatory", default=True,
        help="A quote that cannot meet a mandatory attribute scores 0 on spec match for this item.")

    def _get_display_text(self):
        self.ensure_one()
        return f"{self.name}: {self.value}{f' {self.unit}' if self.unit else ''}"


class ProcurementRequestRequirement(models.Model):
    _name = 'procurement.request.requirement'
    _description = "Procurement General Requirement"
    _order = 'request_id, sequence, id'

    request_id = fields.Many2one('procurement.request', required=True, index=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char("Requirement", required=True, help="e.g. Installation included, CE marking, Net 30 payment")
    description = fields.Text("Details")
    mandatory = fields.Boolean("Mandatory", default=True)
