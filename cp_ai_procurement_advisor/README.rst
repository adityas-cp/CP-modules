======================
AI Procurement Advisor
======================

Buy anything without knowing the exact specification up front.

Workflow
========

1. **Purchase > AI Procurement > Requests**: create a request with a one-line need
   (e.g. "Laptops for 25 new hires") and click **Start with AI**.
2. The *Procurement Advisor* agent interviews you. It asks the universal questions
   (quantity, budget, deadline, delivery, installation) and the questions that matter
   for that kind of product, then writes the items, their specification attributes
   and the general requirements on the request.
3. Review the items, then **Approve Specification**. Approval is refused while items,
   quantities, specifications or the deadline are missing.
4. **Send to Vendors**: one RFQ per vendor, grouped as alternatives, emailed with the
   specification PDF and a link to the vendor quote form.
5. Vendors enter unit prices, delivery and installation costs, lead time, warranty
   and, for each characteristic, whether they meet it or offer an alternative.
6. **Compare Quotes** ranks the quotes; **Choose** confirms the winning RFQ (the
   standard alternative RFQ dialog lets you cancel or keep the others).

Scoring
=======

Each quote gets a score out of 100 per factor, computed in Python (``models/scoring.py``),
never by the AI:

- **Price**: cheapest total (delivery and installation included) = 100, scaled down by
  the share of items left unquoted.
- **Delivery**: on time = 100, minus 5 points per day late (0 with *Strict Deadline*).
- **Spec match**: vendor answers per characteristic; a mandatory characteristic not met
  gives 0 for that item.
- **Past performance**: vendor on-time delivery rate (50 when unknown).
- **Warranty**: longest warranty = 100.

The total is the weighted average, weights set by the request priority
(Balanced, Cheapest, Urgent, Quality First or Custom).

Hidden budget
=============

The budget and target prices are internal: they never appear on the specification PDF,
the RFQ lines, the email or the vendor portal.

Technical notes
===============

- Requires Odoo Enterprise (``ai`` module); AI calls use the database AI/IAP setup.
- The agent (``ai_agent_procurement_advisor``) always has its five tools
  (``data/ai_tool_data.xml``); they act on the request the chat is linked to through the
  ``ai.composer`` rule ``chatter_ai_button`` on ``procurement.request``.
- Items without a product are ordered with the generic *Procurement Item* product;
  **Create Products for Items** turns them into real products.
