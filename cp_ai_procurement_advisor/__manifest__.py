# -*- coding: utf-8 -*-
#
#  ┌────────────────────────────────────────────────────────────────────┐
#  │   Developed by: CHEF PIXEL                                         │
#  │   Website: https://chef-pixel.fr                                   │
#  │   Support: hello@chef-pixel.fr                                     │
#  │   Description: AI-guided specifications and vendor quote ranking   │
#  └────────────────────────────────────────────────────────────────────┘
#
#  🤖 From a vague purchase need to a ranked list of vendor quotes!

{
    "name": "AI Procurement Advisor",
    "version": "20.0.1.0.0",
    "summary": "AI interviews you about any purchase, writes the specification, collects and ranks vendor quotes",
    "description": """
        AI Procurement Advisor
        ======================

        Buy anything without knowing the exact specifications up front.

        Key Features
        ------------
        - AI chat that asks the questions relevant to the product being bought
          (laptops, uniforms, furniture, services...) and fills the request for you
        - Generic item specification: any attribute, any product category
        - Specification PDF sent to vendors, budget and target prices kept hidden
        - Vendor portal page to quote prices, lead time, warranty and spec compliance
        - Transparent, repeatable scoring of quotes (price, delivery, spec match,
          past performance, warranty) with configurable weights
        - Quotes are standard RFQs grouped as alternatives, confirm the winner as usual
    """,
    "author": "CHEF PIXEL",
    "maintainer": "CHEF PIXEL",
    "company": "CHEF PIXEL",
    "website": "https://chef-pixel.fr",
    "support": "hello@chef-pixel.fr",
    "category": "Supply Chain/Purchase",
    "license": "OPL-1",
    "depends": ["purchase_stock", "purchase_alternative", "portal", "ai"],
    "data": [
        "security/ir.access.csv",
        "data/ir_sequence_data.xml",
        "data/product_data.xml",
        "report/procurement_spec_report.xml",
        "data/mail_template_data.xml",
        "data/ai_tool_data.xml",
        "data/ai_agent_data.xml",
        "views/procurement_request_views.xml",
        "views/purchase_order_views.xml",
        "views/portal_templates.xml",
        "views/res_config_settings_views.xml",
        "wizard/procurement_send_wizard_views.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "cp_ai_procurement_advisor/static/src/js/**/*",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}
