"""Adaptive step-integrated radiative energy contract for future dynamic graphs.

Integrate Q_rad(t)*F_edge(t), not mean(Q_rad)*F_edge(step end). The caller must
supply geometry-validated fractions and all wind/burn-curve discontinuities.
This does not update thermal state or generate a graph, wind or firebrands.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

import numpy as np
from numpy.polynomial.legendre import leggauss

from pyfireca.village.wind_v3 import _number


@dataclass(frozen=True, slots=True)
class IntegratedRadiation:
    edge_energy_J: np.ndarray
    source_energy_J: np.ndarray
    unassigned_source_energy_J: np.ndarray
    target_energy_J: np.ndarray
    estimated_edge_error_J: np.ndarray
    quadrature_orders: tuple[int, ...]
    callback_evaluations: int
    closure_error_J: float
    time_panels_s: tuple[tuple[float, float], ...]
    attempted_panels: int


def _indices(value, node_count, name):
    array = np.asarray(value)
    if array.ndim != 1 or array.dtype.kind not in "iu":
        raise ValueError(f"{name} must be one-dimensional integer node indices")
    if np.any(array < 0) or np.any(array >= node_count):
        raise ValueError(f"{name} contains an out-of-range node")
    return array.astype(np.int64, copy=True)


def _nonnegative(value, shape, name):
    array = np.asarray(value)
    if array.dtype.kind not in "iuf" or array.shape != shape:
        raise ValueError(f"{name} must be a numeric array with shape {shape}")
    array = array.astype(np.float64, copy=True)
    if np.any(~np.isfinite(array)) or np.any(array < 0):
        raise ValueError(f"{name} must be finite and nonnegative")
    return array


def integrate_dynamic_radiation(
    *,
    start_s,
    end_s,
    node_count,
    edge_sources,
    edge_targets,
    evaluate,
    break_times_s=(),
    initial_order=3,
    max_order=24,
    relative_tolerance=1e-5,
    absolute_tolerance_J=1e-8,
    max_evaluations=10_000,
    max_nodes=20_000,
    max_edges=500_000,
    max_subdivisions=8,
    max_panels=256,
) -> IntegratedRadiation:
    """Converge edge/source/unassigned energies separately on every time panel.

    evaluate(t) -> (radiative_source_power_W[node], fraction[edge]). Fractions
    are geometry outputs, not normalized. Their outgoing sum must be <=1 at
    every queried t. Unassigned energy can include terrain absorption and
    truncation, so it must not be labelled atmospheric escape.
    """
    start = _number(start_s, "start_s", minimum=0)
    end = _number(end_s, "end_s", minimum=0)
    if end <= start:
        raise ValueError("end_s must exceed start_s")
    for name, value in (
        ("node_count", node_count),
        ("initial_order", initial_order),
        ("max_order", max_order),
        ("max_evaluations", max_evaluations),
        ("max_nodes", max_nodes),
        ("max_edges", max_edges),
        ("max_panels", max_panels),
    ):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if type(max_subdivisions) is not int or max_subdivisions < 0:
        raise ValueError("max_subdivisions must be a nonnegative integer")
    if node_count > max_nodes:
        raise ValueError("RESOURCE_LIMIT: dynamic radiation node limit exceeded")
    if max_order < 2 * initial_order:
        raise ValueError("max_order must permit at least two quadrature orders")
    relative = _number(relative_tolerance, "relative_tolerance", minimum=0)
    absolute = _number(absolute_tolerance_J, "absolute_tolerance_J", minimum=0)
    if relative == 0 and absolute == 0:
        raise ValueError("at least one quadrature tolerance must be positive")
    sources = _indices(edge_sources, node_count, "edge_sources")
    targets = _indices(edge_targets, node_count, "edge_targets")
    if sources.shape != targets.shape:
        raise ValueError("edge sources and targets must have equal shapes")
    if sources.size > max_edges:
        raise ValueError("RESOURCE_LIMIT: dynamic radiation edge limit exceeded")
    if np.any(sources == targets) or len(set(zip(sources, targets, strict=True))) != sources.size:
        raise ValueError("radiation edges must be unique and cannot target their own source node")
    breaks = [_number(value, "break time", minimum=0) for value in break_times_s]
    if any(value <= start or value >= end for value in breaks) or any(
        b <= a for a, b in pairwise(breaks)
    ):
        raise ValueError("break times must strictly increase inside the step")
    bounds = [start, *breaks, end]
    orders = []
    order = initial_order
    while order <= max_order:
        orders.append(order)
        if (len(bounds) - 1) * sum(orders) > max_evaluations:
            raise ValueError("RESOURCE_LIMIT: time quadrature evaluation upper bound exceeded")
        order *= 2
    if not callable(evaluate):
        raise TypeError("evaluate must be callable")
    total_edges = np.zeros(sources.size)
    total_source = np.zeros(node_count)
    total_unassigned = np.zeros(node_count)
    edge_errors = np.zeros(sources.size)
    used_orders, evaluations, accepted_panels = [], 0, []
    pending = [(low, high, 0) for low, high in reversed(list(pairwise(bounds)))]
    attempted_panels = 0
    while pending:
        low, high, depth = pending.pop()
        if attempted_panels >= max_panels or evaluations + sum(orders) > max_evaluations:
            raise ValueError("RESOURCE_LIMIT: adaptive time-panel/evaluation limit exceeded")
        attempted_panels += 1
        previous = None
        for order in orders:
            nodes, weights = leggauss(order)
            width = high - low
            times = low + (nodes + 1) * width / 2
            if np.any(times <= low) or np.any(times >= high):
                raise ValueError("time panel loses numerical resolution")
            estimates = [np.zeros(sources.size), np.zeros(node_count), np.zeros(node_count)]
            for time, weight in zip(times, weights, strict=True):
                power_raw, fractions_raw = evaluate(float(time))
                evaluations += 1
                power = _nonnegative(power_raw, (node_count,), "radiative source power")
                fractions = _nonnegative(fractions_raw, sources.shape, "edge fractions")
                outgoing = np.bincount(sources, weights=fractions, minlength=node_count)
                if np.any(~np.isfinite(outgoing)) or np.any(outgoing > 1):
                    raise ValueError("RADIATION_SOURCE_BUDGET: outgoing fractions exceed one")
                with np.errstate(over="ignore", invalid="ignore"):
                    energy = power * (float(weight) * width / 2)
                    estimates[0] += energy[sources] * fractions
                    estimates[1] += energy
                    estimates[2] += energy * (1 - outgoing)
                if any(np.any(~np.isfinite(value)) for value in estimates):
                    raise ValueError("dynamic radiation energy overflows")
            if previous is not None:
                errors = [np.abs(a - b) for a, b in zip(estimates, previous, strict=True)]
                # Check individual edges, source totals and unassigned totals;
                # a large energy at another receiver cannot hide a small edge.
                if all(
                    np.all(
                        error
                        <= absolute * ((high - low) / (end - start)) + relative * np.abs(value)
                    )
                    for error, value in zip(errors, estimates, strict=True)
                ):
                    total_edges += estimates[0]
                    total_source += estimates[1]
                    total_unassigned += estimates[2]
                    edge_errors += errors[0]
                    used_orders.append(order)
                    accepted_panels.append((low, high))
                    break
            previous = estimates
        else:
            if depth >= max_subdivisions:
                raise ValueError("TIME_QUADRATURE_NOT_CONVERGED: subdivision limit reached")
            midpoint = low / 2 + high / 2
            if not low < midpoint < high:
                raise ValueError("time panel loses numerical resolution")
            pending.extend(((midpoint, high, depth + 1), (low, midpoint, depth + 1)))
    target_energy = np.bincount(targets, weights=total_edges, minlength=node_count)
    received_by_source = np.bincount(sources, weights=total_edges, minlength=node_count)
    with np.errstate(over="ignore", invalid="ignore"):
        residual = total_source - received_by_source - total_unassigned
    if any(
        np.any(~np.isfinite(value))
        for value in (total_edges, total_source, total_unassigned, target_energy, residual)
    ):
        raise ValueError("integrated radiation energy overflows")
    if np.any(np.abs(residual) > 1e-8 + 1e-12 * total_source):
        raise ValueError("integrated radiation energy closure failed")
    for array in (total_edges, total_source, total_unassigned, target_energy, edge_errors):
        array.setflags(write=False)
    return IntegratedRadiation(
        total_edges,
        total_source,
        total_unassigned,
        target_energy,
        edge_errors,
        tuple(used_orders),
        evaluations,
        float(np.max(np.abs(residual))),
        tuple(accepted_panels),
        attempted_panels,
    )
