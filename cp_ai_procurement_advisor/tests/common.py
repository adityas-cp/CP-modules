# -*- coding: utf-8 -*-

from datetime import timedelta

from odoo import Command, fields
from odoo.tests import TransactionCase


class ProcurementCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.buyer = cls.env['res.users'].create({
            'name': 'Procurement Buyer',
            'login': 'procurement_buyer',
            'email': 'buyer@example.com',
            'group_ids': [Command.set([cls.env.ref('purchase.group_purchase_user').id])],
        })
        cls.vendor_a, cls.vendor_b, cls.vendor_c = cls.env['res.partner'].create([
            {'name': 'Vendor A', 'email': 'a@vendor.example.com'},
            {'name': 'Vendor B', 'email': 'b@vendor.example.com'},
            {'name': 'Vendor C', 'email': 'c@vendor.example.com'},
        ])
        cls.deadline = fields.Date.today() + timedelta(days=30)

    def _create_request(self, **vals):
        return self.env['procurement.request'].with_user(self.buyer).create({
            'title': 'Laptops for 25 new hires',
            'budget': 30000.0,
            'deadline': self.deadline,
            'delivery_location': 'HQ, 3rd floor',
            **vals,
        })

    def _laptop_items(self):
        return [{
            'name': '14-inch business laptop',
            'quantity': 25,
            'unit': 'Units',
            'target_unit_price': 1000.0,
            'description': 'For office work and video calls.',
            'attributes': [
                {'name': 'RAM', 'value': 'min. 16', 'unit': 'GB', 'mandatory': True},
                {'name': 'Keyboard layout', 'value': 'AZERTY', 'mandatory': False},
            ],
        }, {
            'name': 'USB-C docking station',
            'quantity': 25,
            'target_unit_price': 150.0,
            'attributes': [{'name': 'Display outputs', 'value': '2 x HDMI'}],
        }]
