# -*- coding: utf-8 -*-

from odoo import fields, models


class ProcurementQuoteCompliance(models.Model):
    _name = 'procurement.quote.compliance'
    _description = "Vendor Answer to a Specification Point"
    _order = 'order_id, order_line_id, id'

    order_id = fields.Many2one('purchase.order', required=True, index=True, ondelete='cascade')
    order_line_id = fields.Many2one('purchase.order.line', index='btree_not_null', ondelete='cascade')
    attribute_id = fields.Many2one('procurement.request.line.attribute', index='btree_not_null', ondelete='cascade')
    requirement_id = fields.Many2one('procurement.request.requirement', index='btree_not_null', ondelete='cascade')
    name = fields.Char("Specification", compute='_compute_spec_fields')
    required_value = fields.Char("Required", compute='_compute_spec_fields')
    mandatory = fields.Boolean(compute='_compute_spec_fields')
    status = fields.Selection([
        ('met', "Meets the specification"),
        ('alternative', "Alternative offered"),
        ('not_met', "Cannot meet"),
    ], string="Vendor Answer")
    vendor_comment = fields.Char("Vendor Comment")

    _check_target = models.Constraint(
        'CHECK((attribute_id IS NULL) != (requirement_id IS NULL))',
        "A vendor answer refers either to an item specification or to a general requirement.",
    )

    def _compute_spec_fields(self):
        for compliance in self:
            if compliance.attribute_id:
                attribute = compliance.attribute_id
                compliance.name = f"{attribute.line_id.name} - {attribute.name}"
                compliance.required_value = f"{attribute.value}{f' {attribute.unit}' if attribute.unit else ''}"
                compliance.mandatory = attribute.mandatory
            else:
                requirement = compliance.requirement_id
                compliance.name = requirement.name
                compliance.required_value = requirement.description or ""
                compliance.mandatory = requirement.mandatory
