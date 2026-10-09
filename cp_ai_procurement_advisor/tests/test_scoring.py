# -*- coding: utf-8 -*-

from datetime import date

from odoo.tests import TransactionCase, tagged

from odoo.addons.cp_ai_procurement_advisor.models import scoring


@tagged('post_install', '-at_install')
class TestScoring(TransactionCase):

    def _quote(self, key, total, **kwargs):
        return {
            'key': key,
            'total': total,
            'coverage': kwargs.get('coverage', 1.0),
            'delivery_date': kwargs.get('delivery_date'),
            'spec': kwargs.get('spec', 100.0),
            'on_time_rate': kwargs.get('on_time_rate', -1),
            'warranty_months': kwargs.get('warranty_months', 0),
        }

    def test_price_score_relative_to_cheapest(self):
        results = scoring.score_quotes(
            [self._quote('a', 1000), self._quote('b', 2000)],
            scoring.WEIGHT_PRESETS['balanced'])
        self.assertEqual(results['a']['price'], 100.0)
        self.assertEqual(results['b']['price'], 50.0)

    def test_incomplete_quote_does_not_win_on_price(self):
        # 'b' looks cheaper because it skipped half the items
        results = scoring.score_quotes(
            [self._quote('a', 1000), self._quote('b', 600, coverage=0.5)],
            scoring.WEIGHT_PRESETS['cheapest'])
        self.assertEqual(results['a']['price'], 100.0)
        self.assertLess(results['b']['price'], results['a']['price'])

    def test_delivery_against_deadline(self):
        deadline = date(2026, 12, 1)
        self.assertEqual(scoring.score_delivery(date(2026, 11, 20), deadline), 100.0)
        self.assertEqual(scoring.score_delivery(date(2026, 12, 3), deadline), 90.0)
        self.assertEqual(scoring.score_delivery(date(2026, 12, 3), deadline, strict_deadline=True), 0.0)
        self.assertEqual(scoring.score_delivery(None, deadline), scoring.NEUTRAL_SCORE)

    def test_delivery_without_deadline_relative_to_earliest(self):
        earliest = date(2026, 11, 1)
        self.assertEqual(scoring.score_delivery(earliest, None, earliest), 100.0)
        self.assertEqual(scoring.score_delivery(date(2026, 11, 5), None, earliest), 80.0)

    def test_compliance(self):
        self.assertEqual(scoring.score_compliance([]), 100.0)
        self.assertEqual(scoring.score_compliance([('met', True), ('met', False)]), 100.0)
        self.assertEqual(scoring.score_compliance([('not_met', True), ('met', False)]), 0.0)
        # optional not met: weight 1 of 3 lost
        self.assertAlmostEqual(scoring.score_compliance([('met', True), ('not_met', False)]), 200 / 3)
        # unanswered counts as half
        self.assertEqual(scoring.score_compliance([(False, True)]), 50.0)

    def test_performance_and_warranty(self):
        self.assertEqual(scoring.score_performance(-1), scoring.NEUTRAL_SCORE)
        self.assertEqual(scoring.score_performance(92.5), 92.5)
        self.assertEqual(scoring.score_warranty(12, 24), 50.0)
        self.assertEqual(scoring.score_warranty(0, 0), 0.0)

    def test_total_and_rank(self):
        weights = {'price': 1, 'delivery': 1, 'spec': 0, 'performance': 0, 'warranty': 0}
        deadline = date(2026, 12, 1)
        results = scoring.score_quotes([
            self._quote('cheap_late', 1000, delivery_date=date(2026, 12, 21)),
            self._quote('pricey_on_time', 1250, delivery_date=date(2026, 11, 25)),
        ], weights, deadline=deadline)
        # cheap_late: (100 + 0) / 2, pricey_on_time: (80 + 100) / 2
        self.assertEqual(results['cheap_late']['total'], 50.0)
        self.assertEqual(results['pricey_on_time']['total'], 90.0)
        self.assertEqual(results['pricey_on_time']['rank'], 1)
        self.assertEqual(results['cheap_late']['rank'], 2)

    def test_scoring_is_repeatable(self):
        quotes = [self._quote('a', 1000, warranty_months=12), self._quote('b', 1100, warranty_months=24)]
        weights = scoring.WEIGHT_PRESETS['quality']
        self.assertEqual(scoring.score_quotes(quotes, weights), scoring.score_quotes(quotes, weights))
