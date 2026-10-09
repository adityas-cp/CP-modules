# -*- coding: utf-8 -*-
"""Deterministic quote scoring.

Pure Python on purpose: the ranking must be explainable and repeatable, so the
AI never computes it. Every factor is a score out of 100, the total is the
weighted average of the factors.
"""

NEUTRAL_SCORE = 50.0
LATE_PENALTY_PER_DAY = 5.0

FACTORS = ('price', 'delivery', 'spec', 'performance', 'warranty')

WEIGHT_PRESETS = {
    'balanced': {'price': 35, 'delivery': 20, 'spec': 20, 'performance': 15, 'warranty': 10},
    'cheapest': {'price': 60, 'delivery': 10, 'spec': 15, 'performance': 10, 'warranty': 5},
    'urgent': {'price': 20, 'delivery': 50, 'spec': 15, 'performance': 10, 'warranty': 5},
    'quality': {'price': 15, 'delivery': 15, 'spec': 35, 'performance': 25, 'warranty': 10},
}

COMPLIANCE_VALUES = {'met': 1.0, 'alternative': 0.5, 'not_met': 0.0}
UNANSWERED_COMPLIANCE_VALUE = 0.5


def score_price(total, best_total, coverage):
    """Cheapest complete quote gets 100. Quotes missing items are scaled down
    by their coverage so leaving items out never makes a quote look cheaper."""
    if total <= 0 or best_total <= 0:
        return 0.0
    return best_total / total * 100.0 * coverage


def score_delivery(delivery_date, deadline=None, earliest_date=None, strict_deadline=False):
    if not delivery_date:
        return NEUTRAL_SCORE
    if deadline:
        late_days = (delivery_date - deadline).days
        if late_days <= 0:
            return 100.0
        if strict_deadline:
            return 0.0
        return max(0.0, 100.0 - late_days * LATE_PENALTY_PER_DAY)
    if not earliest_date:
        return NEUTRAL_SCORE
    return max(0.0, 100.0 - (delivery_date - earliest_date).days * LATE_PENALTY_PER_DAY)


def score_performance(on_time_rate):
    """`on_time_rate` is the partner OTD in percent, negative when unknown."""
    if on_time_rate is None or on_time_rate < 0:
        return NEUTRAL_SCORE
    return min(100.0, on_time_rate)


def score_warranty(months, best_months):
    if best_months <= 0:
        return 0.0
    return min(100.0, max(0, months) / best_months * 100.0)


def score_compliance(answers):
    """Score one item (or the general requirements) from the vendor answers.

    :param answers: list of (status, mandatory) tuples, status being one of
        COMPLIANCE_VALUES keys or False when the vendor did not answer.
    """
    if not answers:
        return 100.0
    if any(status == 'not_met' and mandatory for status, mandatory in answers):
        return 0.0
    total_weight = 0.0
    total_value = 0.0
    for status, mandatory in answers:
        weight = 2.0 if mandatory else 1.0
        total_weight += weight
        total_value += weight * COMPLIANCE_VALUES.get(status, UNANSWERED_COMPLIANCE_VALUE)
    return total_value / total_weight * 100.0


def weighted_total(scores, weights):
    weight_sum = sum(max(0, weights.get(factor, 0)) for factor in FACTORS)
    if not weight_sum:
        return 0.0
    return sum(scores[factor] * max(0, weights.get(factor, 0)) for factor in FACTORS) / weight_sum


def score_quotes(quotes, weights, deadline=None, strict_deadline=False):
    """Score a set of competing quotes.

    :param quotes: list of dicts with keys ``key``, ``total`` (comparable
        currency, delivery and installation included), ``coverage`` (0..1 ratio
        of quoted items), ``delivery_date`` (date or None), ``spec`` (0..100),
        ``on_time_rate`` (percent, negative when unknown), ``warranty_months``.
    :param weights: dict factor -> weight.
    :return: dict key -> dict factor -> score, with ``total`` and ``rank``.
    """
    totals = [q['total'] for q in quotes if q['total'] > 0 and q['coverage'] >= 1.0]
    if not totals:
        totals = [q['total'] for q in quotes if q['total'] > 0]
    best_total = min(totals) if totals else 0.0
    dates = [q['delivery_date'] for q in quotes if q['delivery_date']]
    earliest_date = min(dates) if dates else None
    best_months = max((q['warranty_months'] or 0 for q in quotes), default=0)

    results = {}
    for quote in quotes:
        scores = {
            'price': score_price(quote['total'], best_total, quote['coverage']),
            'delivery': score_delivery(quote['delivery_date'], deadline, earliest_date, strict_deadline),
            'spec': quote['spec'],
            'performance': score_performance(quote['on_time_rate']),
            'warranty': score_warranty(quote['warranty_months'] or 0, best_months),
        }
        scores = {factor: round(value, 2) for factor, value in scores.items()}
        scores['total'] = round(weighted_total(scores, weights), 2)
        results[quote['key']] = scores

    ranked = sorted(results, key=lambda key: results[key]['total'], reverse=True)
    for rank, key in enumerate(ranked, start=1):
        results[key]['rank'] = rank
    return results
