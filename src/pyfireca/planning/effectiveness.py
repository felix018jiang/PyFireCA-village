"""Declared effectiveness-only ordering, without a construction-cost term."""

from __future__ import annotations

from statistics import mean


def effectiveness_score(evaluations):
    """Lower is better: worst/mean homes, structural area, bridges, then delay.

    No area/length/currency term is used. Scenarios are an equal-weight stress
    test, not an estimated annual probability distribution. Preserve all ties.
    """
    if not evaluations or len({item.horizon_s for item in evaluations}) != 1:
        raise ValueError("score requires evaluations at the same horizon")
    if any(item.metrics["burning_reached_domain_edge"] for item in evaluations):
        raise ValueError("crop-edge ignition invalidates this search evaluation")
    metrics = [item.metrics for item in evaluations]
    return (
        max(m["ignited_house_count"] for m in metrics),
        mean(m["ignited_house_count"] for m in metrics),
        mean(m["ignited_building_area_m2"] for m in metrics),
        mean(m["ignited_bridge_count"] for m in metrics),
        -mean(m["house_ignition_free_time_s"] for m in metrics),
    )
