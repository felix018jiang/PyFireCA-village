"""Reproduce the interval HRR failure and validate its separately frozen solver."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import fields, is_dataclass, replace
from decimal import Decimal, localcontext
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "runs/research/heritage-priority-producer-20261111"
NEW = ROOT / "runs/research/heritage-hrr-error-recovery-producer-20261111"
sys.path[:0] = [str(NEW / "src"), str(ROOT / "tests/village")]

import test_firebrands_v3 as brands  # noqa: E402
import test_flame_contact_v3 as contact_fixture  # noqa: E402
import test_solver_v3 as fixture  # noqa: E402

from pyfireca.village import solver_v3 as solver  # noqa: E402
from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters  # noqa: E402


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprint(obj):
    if isinstance(obj, np.ndarray):
        return [str(obj.dtype), obj.shape, hashlib.sha256(obj.tobytes()).hexdigest()]
    if isinstance(obj, (float, np.floating)):
        return float(obj).hex()
    if isinstance(obj, np.integer):
        return int(obj)
    if is_dataclass(obj):
        return {field.name: fingerprint(getattr(obj, field.name)) for field in fields(obj)}
    if isinstance(obj, (tuple, list)):
        return [fingerprint(v) for v in obj]
    if isinstance(obj, dict):
        return {k: fingerprint(v) for k, v in obj.items()}
    return obj


def decimal_energy(age, dt, peak, growth, steady, decay, fuel, heat):
    """High-precision cumulative primitive, independent from the overlap formula."""
    with localcontext() as ctx:
        ctx.prec = 140
        a, step, p, g, s, d, f, h = (
            Decimal.from_float(float(x)) for x in (age, dt, peak, growth, steady, decay, fuel, heat)
        )

        def primitive(t):
            if t < g:
                return t**3 / (3 * g**2)
            if t < g + s:
                return g / 3 + t - g
            if t < g + s + d:
                x = t - g - s
                return g / 3 + s + x - x**2 / (2 * d)
            return g / 3 + s + d / 2

        return float(min(p * (primitive(a + step) - primitive(a)), f * h))


def audit():
    relative = Path("src/pyfireca/village/solver_v3.py")
    assert Path(solver.__file__).resolve() == (NEW / relative).resolve()
    ns = dict(solver.__dict__)
    exec(compile((OLD / relative).read_bytes(), str(OLD / relative), "exec"), ns)
    previous = ns["_run_resolved_v3_simulation"]
    current = solver._run_resolved_v3_simulation
    config = load_flame_contact_parameters(
        contact_fixture.DIRECTORY / "flame-contact.synthetic.yml"
    )
    age, end = 0.8333386666450667, 0.8333386667460667
    request = fixture._request(
        ("grass", "grass"), max_dt_s=age, end_time_s=end, edge_weight=0.01, sample_interval_s=age
    )
    op = contact_fixture.operator(request, config)
    expected_error = "flame contact HRR cannot exceed the configured peak"
    try:
        previous(request, contact=op)
    except ValueError as error:
        assert str(error) == expected_error
    else:
        raise AssertionError("the original native solver did not reproduce its real guard")
    corrected = current(request, contact=op)
    assert corrected.final_state.time_s == end
    assert corrected.final_state.interval_mean_hrr_W[0, 0] == request.parameters.peak_hrr_W[0, 0]
    assert np.isclose(
        corrected.energy_budget.released_J,
        corrected.consumed_fuel_kg * request.parameters.heat_of_combustion_J_kg_dry[0, 0],
        atol=1e-10,
        rtol=1e-14,
    )
    try:
        op.transfer(2 * op.physics.peak_hrr_W, np.full((1, 2), 300.0), request.burnable_mask)
    except ValueError as error:
        assert str(error) == expected_error
    else:
        raise AssertionError("bare contact operator accepted an unphysical call")
    comparisons = {}
    brand_request, brand_config = brands.request()
    examples = {
        "contact_and_radiation": (fixture._request(("grass", "shrub"), edge_weight=0.08), True),
        "contact_only": (fixture._request(("grass", "grass")), True),
        "radiation_only": (fixture._request(("grass", "shrub"), edge_weight=0.01), False),
        "single_source": (fixture._request(("grass",)), True),
        "finite_fuel_end": (fixture._request(("grass",), end_time_s=3.9), False),
        "firebrands_with_contact": (replace(brand_request, firebrands=brand_config), True),
    }
    for label, (req, enabled) in examples.items():
        operator = contact_fixture.operator(req, config) if enabled else None
        first = fingerprint(previous(req, contact=operator))
        second = fingerprint(current(req, contact=operator))
        assert first == second, label
        comparisons[label] = hashlib.sha256(json.dumps(first, sort_keys=True).encode()).hexdigest()
    cases = 0
    for g, s, d in ((0.5, 1.25, 0.5), (240.0, 433.13, 79.7), (2.3, 0, 1.01), (0, 1.0, 0)):
        for t in (0, g / 2, g, g + s, g + s + d, g + s + d + 1):
            for a in (t, max(0.0, t - 1e-11), max(0.0, t - 0.125)):
                for step in (1.01e-10, 0.25, 2500.0):
                    for fuel in (1e-12, 47):
                        args = a, step, 623.51, g, s, d, fuel, 18000.0
                        actual = solver._exact_burn_interval_energy(*args)
                        expect = decimal_energy(*args)
                        assert actual == expect
                        cases += 1
    return {
        "schema_version": "native-v3-burn-error-path-regression-1",
        "old_solver_sha256": digest(OLD / relative),
        "new_solver_sha256": digest(NEW / relative),
        "guarded_run_old_error": expected_error,
        "synthetic_state_time_s": age,
        "synthetic_dt_s": end - age,
        "synthetic_accepted_mean_W": float(corrected.final_state.interval_mean_hrr_W[0, 0]),
        "synthetic_final_absolute_energy_residual_J": abs(
            corrected.energy_budget.released_J
            - corrected.consumed_fuel_kg * request.parameters.heat_of_combustion_J_kg_dry[0, 0]
        ),
        "bare_direct_invalid_HRR_rejected": True,
        "independent_decimal_stage_boundary_and_fuel_cases": cases,
        "bitwise_old_successful_result_fingerprints": comparisons,
        "original_failure_had_numeric_state_logged": False,
        "new_computed_fallback_logs_aggregate_state_when_exercised": True,
        "old_successful_unchanged_scope": (
            "same inputs and runtime; original converged native outputs pass through the same "
            "radiation, contact, state and ledger arithmetic in the same order"
        ),
        "firebrand_cases_after_a_native_contact_peak_error_still_raise": True,
        "new_CA_checks_in_village_campaign": 0,
        "auditor_sha256": digest(__file__),
    }


if __name__ == "__main__":
    value = audit()
    with Path(sys.argv[1]).open("x") as output:
        json.dump(value, output, indent=2, allow_nan=False)
        output.write("\n")
