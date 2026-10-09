# -*- coding: utf-8 -*-

from odoo import http
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal

MAX_QUOTE_FILE_SIZE = 10 * 1024 * 1024
COMPLIANCE_STATUSES = {'met', 'alternative', 'not_met'}


def _parse_amount(value):
    value = (value or '').strip().replace(' ', '').replace(',', '.')
    if not value:
        return 0.0
    amount = float(value)
    if amount < 0:
        raise ValueError(value)
    return amount


def _parse_int(value):
    value = (value or '').strip()
    if not value:
        return 0
    number = int(value)
    if number < 0:
        raise ValueError(value)
    return number


class ProcurementQuotePortal(CustomerPortal):

    def _procurement_get_order(self, order_id, access_token):
        try:
            order_sudo = self._document_check_access('purchase.order', order_id, access_token=access_token)
        except (AccessError, MissingError):
            return None
        return order_sudo if order_sudo.procurement_request_id else None

    def _procurement_quote_url(self, order_sudo, access_token, **params):
        query = "&".join(f"{key}={value}" for key, value in params.items())
        return f"/procurement/quote/{order_sudo.id}?access_token={access_token}{'&' + query if query else ''}"

    @http.route('/procurement/quote/<int:order_id>', type='http', auth='public', website=True, methods=['GET'])
    def procurement_quote_form(self, order_id, access_token=None, success=None, error=None, **kw):
        order_sudo = self._procurement_get_order(order_id, access_token)
        if not order_sudo:
            raise request.not_found()
        return request.render('cp_ai_procurement_advisor.portal_procurement_quote', {
            'order': order_sudo,
            'procurement_request': order_sudo.procurement_request_id,
            'access_token': access_token,
            'can_edit': order_sudo._procurement_quote_is_editable(),
            'success': success,
            'error': error,
        })

    @http.route('/procurement/quote/<int:order_id>/submit', type='http', auth='public', website=True, methods=['POST'])
    def procurement_quote_submit(self, order_id, access_token=None, **post):
        order_sudo = self._procurement_get_order(order_id, access_token)
        if not order_sudo:
            raise request.not_found()
        if not order_sudo._procurement_quote_is_editable():
            return request.redirect(self._procurement_quote_url(order_sudo, access_token, error='closed'))

        try:
            line_prices = {
                line.id: _parse_amount(post.get(f'price_{line.id}'))
                for line in order_sudo.order_line.filtered('procurement_line_id')
            }
            vals = {
                'delivery_cost': _parse_amount(post.get('delivery_cost')),
                'installation_cost': _parse_amount(post.get('installation_cost')),
                'vendor_lead_days': _parse_int(post.get('vendor_lead_days')),
                'warranty_months': _parse_int(post.get('warranty_months')),
                'vendor_quote_notes': (post.get('vendor_quote_notes') or '').strip() or False,
                'partner_ref': (post.get('partner_ref') or '').strip() or order_sudo.partner_ref,
            }
        except ValueError:
            return request.redirect(self._procurement_quote_url(order_sudo, access_token, error='invalid'))
        if not (post.get('vendor_lead_days') or '').strip():
            return request.redirect(self._procurement_quote_url(order_sudo, access_token, error='lead_time'))

        compliance_answers = {}
        for compliance in order_sudo.procurement_compliance_ids:
            status = post.get(f'compliance_{compliance.id}')
            compliance_answers[compliance.id] = (
                status if status in COMPLIANCE_STATUSES else False,
                (post.get(f'compliance_comment_{compliance.id}') or '').strip()[:500],
            )

        attachments = []
        for file in request.httprequest.files.getlist('quote_file'):
            if not file.filename:
                continue
            content = file.read(MAX_QUOTE_FILE_SIZE + 1)
            if len(content) > MAX_QUOTE_FILE_SIZE:
                return request.redirect(self._procurement_quote_url(order_sudo, access_token, error='file_size'))
            attachments.append((file.filename, content, file.mimetype))

        try:
            order_sudo._procurement_submit_quote(line_prices, compliance_answers, vals, attachments)
        except UserError:
            return request.redirect(self._procurement_quote_url(order_sudo, access_token, error='closed'))
        return request.redirect(self._procurement_quote_url(order_sudo, access_token, success=1))
