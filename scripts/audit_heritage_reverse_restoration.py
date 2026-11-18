"""Study sufficient guards for restoring demonstrably uninfluential cleared fuel.

This is a research diagnostic, not an oracle or a native-run certificate. Village
screening reads existing closed artifacts only. Optional CA controls are tiny
synthetic cases using the unchanged production equations and operators.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import fields, replace
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = re.compile(r"cell([0-9]+):([^:\s]+):[0-9]+(?:/local[0-9]+)*")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def hash_value(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def source_restoration_guard(priority, demolished_ids):
    removed = set(demolished_ids)
    source = next((identifier for identifier in priority if identifier not in removed), None)
    if source is None:
        raise ValueError("a source-empty anchor cannot justify restoration")
    return source, sorted(set(priority[: priority.index(source)]) & removed)


def roof_clear_mask(owners, demolished_ids):
    import numpy as np

    removed = set(demolished_ids)
    return np.asarray([bool(group) and set(group) <= removed for group in owners])


def box_offsets(radius, cell_size):
    import math

    maximum = math.ceil(radius / cell_size) + 1
    return [
        (dr, dc)
        for dr in range(-maximum, maximum + 1)
        for dc in range(-maximum, maximum + 1)
        if (max(abs(dr) - 1, 0) * cell_size) ** 2 + (max(abs(dc) - 1, 0) * cell_size) ** 2
        <= radius**2
    ]


def parent_cell_zone(cells, ignited, shape, radius, cell_size):
    """Parent boxes contain every local polygon and quadrature point."""
    import numpy as np

    height, width = map(int, shape)
    offsets = box_offsets(radius, cell_size)
    source_cells, multiplicity = np.unique(cells[ignited], return_counts=True)
    zone = np.zeros(height * width, bool)
    counts = np.bincount(cells, minlength=height * width)
    active_edge_upper = 0
    for source, number in zip(source_cells, multiplicity, strict=True):
        row, col = divmod(int(source), width)
        neighbors = [
            (row + dr) * width + col + dc
            for dr, dc in offsets
            if 0 <= row + dr < height and 0 <= col + dc < width
        ]
        zone[neighbors] = True
        active_edge_upper += int(number) * int(counts[neighbors].sum())
    return zone, active_edge_upper


def screen_native_anchor(run, config_path, plan_id):
    import numpy as np
    import summarize_heritage_joint_pareto as reader

    run, config_path = Path(run).resolve(), Path(config_path).resolve()
    experiment = reader.verify_header(run, config_path)
    snapshot = run / "source-snapshot"
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(snapshot / "scripts"), str(snapshot / "src")]
    import run_heritage_joint_comparison as native

    config, problem = experiment["config"], experiment["problem"]
    study_path = (config_path.parent / config["study_path"]).resolve()
    study = json.loads(study_path.read_text())
    parent_manifest = json.loads(
        ((study_path.parent / study["case_dir"]).resolve() / "manifest.json").read_text()
    )
    ambient = parent_manifest["scenario"]["ambient_temperature_K"]
    cell_size = parent_manifest["grid"]["cell_size_m"]
    row = next(p for p in config["plans"] if p["plan_id"] == plan_id)
    plan = native.plan_from_mapping(row)
    D, G = set(plan.demolished_ids), set(plan.cleared_physical_cell_ids)
    actions = [*sorted(D), *[f"G{cell}" for cell in sorted(G)]]
    blocked_source, source_rows = set(), []
    for label, fire in problem["fires"].items():
        priority = fire["protocol"]["draws"][0]["priority_ids"]
        source, blocked_values = source_restoration_guard(priority, D)
        blocked = set(blocked_values)
        blocked_source |= blocked
        source_rows.append(
            {
                "fire_id": label,
                "actual_source_id": source,
                "restoration_changes_first_retained_source_ids": sorted(blocked),
            }
        )
    eligibility = {a: True for a in actions}
    zero_effect = {a: True for a in actions}
    observations, action_nodes = [], {}
    for typed_plan, scenario in native.expected_checks(config):
        if typed_plan.plan_id != plan_id:
            continue
        folder = native.result_folder(run, plan, scenario)
        check = native.read_complete(
            folder,
            experiment["problem_sha256"],
            plan_id=plan_id,
            scenario_id=scenario,
            demolished_ids=plan.demolished_ids,
            cleared_physical_cell_ids=plan.cleared_physical_cell_ids,
            terminal_required=True,
        )
        valid, _, _, record = native.terminal_observation_status(folder, check)
        if not valid or check["status"] != "passed" or check["evidence"]["invalid_reasons"]:
            raise ValueError("anchor requires every original strict closed core-safe case")
        with np.load(folder / "layout.npz", allow_pickle=False) as data:
            cells = data["physical_cell_id"].astype(int)
            components = data["component_id"]
            shape = data["physical_grid_shape"]
            post = data["covered_area_m2"].ravel()
        groups = []
        for cell, component in zip(cells, components, strict=True):
            match = COMPONENT.fullmatch(str(component))
            if match is None or int(match[1]) != cell:
                raise ValueError("screen requires the immutable original component-owner schema")
            group = () if match[2] == "ground" else tuple(match[2].split("+"))
            if len(group) != len(set(group)):
                raise ValueError("duplicate immutable roof owner")
            groups.append(group)
        with np.load(folder / "loss-basis.npz", allow_pickle=False) as data:
            before = data["baseline_covered_area_m2"].ravel()
        cleared = roof_clear_mask(groups, D) | np.isin(cells, list(G))
        if not np.array_equal(post, np.where(cleared, 0, before)):
            raise ValueError("archived clearing is not the original owner-AND/whole-cell action")
        with np.load(folder / "final-state.npz", allow_pickle=False) as data:
            ignited = np.isfinite(data["ignition_time_s"].ravel())
        if np.any(ignited & cleared):
            raise ValueError("an originally cleared node ignited")
        fire = problem["fires"][check["evidence"]["fire_id"]]
        expected_source = next(
            i for i in fire["protocol"]["draws"][0]["priority_ids"] if i not in D
        )
        if check["evidence"]["source_id"] != expected_source:
            raise ValueError("anchor actual source differs from complete retained priority")
        radius = problem["transport_parameters"]["exposure_radius_m"]
        zone, edge_upper = parent_cell_zone(cells, ignited, shape, radius, cell_size)
        old_edges = record["diagnostics"]["radiation_edge_count"]
        warm = (
            (~ignited)
            & (post > 0)
            & (
                np.asarray(record["diagnostics"]["maximum_valid_surface_temperature_by_node_K"])
                > ambient
            )
        )
        per_action = {}
        for action in actions:
            if action.startswith("G"):
                restore = (cells == int(action[1:])) & cleared & (before > 0)
                if restore.sum() != 1 or any(groups[n] for n in np.flatnonzero(restore)):
                    raise ValueError("grass restoration must be one complete nonroof parent node")
            else:
                restore = np.asarray([action in group for group in groups]) & cleared & (before > 0)
            candidate = not bool(zone[cells[restore]].any())
            eligibility[action] &= candidate
            zero_effect[action] &= not bool(restore.any())
            per_action[action] = {
                "restored_positive_physical_node_count": int(restore.sum()),
                "restored_covered_fuel_area_m2": float(before[restore].sum()),
                "outside_all_ignited_parent_box_15m_zones": candidate,
            }
        observations.append(
            {
                "scenario_id": scenario,
                "manifest": str(folder / "complete.json"),
                "manifest_sha256": reader.file_sha256(folder / "complete.json"),
                "actual_source_id": expected_source,
                "original_ignited_node_count": int(ignited.sum()),
                "warm_unignited_positive_area_node_count": int(warm.sum()),
                "radiation_edge_count": old_edges,
                "all_ignited_outdegree_conservative_upper": edge_upper,
                "strict_always_sparse_bound_verified": 4 * edge_upper < old_edges,
                "parent_box_bound_includes_all_node_geometries_and_quadrature": True,
                "action_screen": per_action,
            }
        )
        action_nodes[scenario] = per_action
    expected_count = len(problem["fires"]) * len(config["roof_scales_m"])
    if len(observations) != expected_count:
        raise ValueError("anchor lacks a complete source/support matrix")
    candidates = [a for a in actions if eligibility[a] and a not in blocked_source]
    reader.verify_header(run, config_path)
    return {
        "schema_version": "heritage-reverse-restoration-spatial-screen-1",
        "native_problem_sha256": experiment["problem_sha256"],
        "anchor_plan_id": plan_id,
        "original_action_count": len(actions),
        "original_roof_action_count": len(D),
        "original_grass_action_count": len(G),
        "source_policy_guard": source_rows,
        "source_switch_blocked_roof_ids": sorted(blocked_source),
        "spatial_and_source_screen_candidate_action_ids": candidates,
        "spatial_screen_candidate_grass_footprint_m2": 25
        * sum(a.startswith("G") for a in candidates),
        "zero_physical_effect_action_ids": [
            a for a in actions if zero_effect[a] and a not in blocked_source
        ],
        "per_scenario": observations,
        "candidate_status": "screening_only;not_restoration_certificate;not_native_restored_run",
        "positive_area_restoration_certified": False,
        "restored_native_CA_runs": 0,
        "large_radiation_or_contact_graphs_constructed": 0,
        "roof_construction_saving_computed": False,
        "remaining_proof_obligations": [
            "native restored baseline material/mask/support and original-owner-AND witness",
            "same source/direct event set and immutable mesh/integration membership",
            "exact retained ignited-source radiation span bytes; transparent no renormalization",
            "restored full operator passes source-weight-budget and graph resource admissibility",
            "all active subsets use unchanged sparse path; "
            "dense-path alternatives separately proved",
            "contact padded width and original burning-row bytes remain exactly unchanged",
            "restored zero-source ambient update and water storage are exact fixed points",
            "restored cold CFL including global contact h is at least original maxdt",
            "pointwise masked thermal-map identity on retained susceptible nodes",
            "new aggregate budget/domain/core acceptance independently validated or bounded",
            "new analysis lineage; never relabel the old certificate as a restored native result",
        ],
    }


def _load_fixture(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def small_native_controls():
    import numpy as np

    from pyfireca.planning.engines import roof_heritage as native_engine
    from pyfireca.planning.engines.roof_joint import RoofJointEvaluationEngine, RoofJointPlan
    from pyfireca.planning.evaluation import EvaluationScenario
    from pyfireca.planning.roof_ignition import (
        RoofIgnitionFootprint,
        footprint_ignitions,
        refine_roof_layout,
        refined_contact_operator,
    )
    from pyfireca.planning.roof_thermal import (
        resolve_roof_layout,
        roof_area_integrated_radiation_graph,
    )
    from pyfireca.village.exposure import SourceIndexedExposure
    from pyfireca.village.flame_contact_v3 import FlameContactParameters
    from pyfireca.village.physics_v2 import (
        advance_surface_heating_and_evaporation,
        monotone_thermal_timestep_limit_s,
        net_surface_heat_flux_W_m2,
    )
    from pyfireca.village.transport_v3 import build_v3_radiative_transport_edges

    model_modules = (
        "pyfireca.village.solver_v3",
        "pyfireca.village.physics_v2",
        "pyfireca.village.exposure",
        "pyfireca.village.flame_contact_v3",
        "pyfireca.village.extinction_v3",
        "pyfireca.planning.roof_thermal",
        "pyfireca.planning.roof_ignition",
        "pyfireca.planning.engines.roof_heritage",
        "pyfireca.planning.engines.roof_joint",
    )
    model_sources = {
        str(Path(module.__file__).resolve()): sha(module.__file__)
        for module in map(importlib.import_module, model_modules)
    }

    fixture = _load_fixture(
        ROOT / "tests/planning/test_roof_joint.py", "reverse_native_roof_fixture"
    )
    bundle, transport, inventory, _, grass = fixture.joint_case.__wrapped__()
    parent, far = bundle.request, bundle.request.grid.width - 1
    values = {
        name: getattr(parent.parameters, name).copy()
        for name in parent.parameters.__dataclass_fields__
    }
    for array in values.values():
        array.ravel()[far] = array.ravel()[grass[0]]
    masks = {
        name: getattr(parent, name).copy()
        for name in ("burnable_mask", "source_mask", "target_mask", "piloted_ignition_mask")
    }
    for array in masks.values():
        array.ravel()[far] = True
    classifications = {
        name: getattr(parent, name).copy() for name in ("material_index", "landcover_index")
    }
    for array in classifications.values():
        array.ravel()[far] = array.ravel()[grass[0]]
    parent = replace(
        parent,
        parameters=replace(parent.parameters, **values),
        flame_contact=FlameContactParameters(
            1100, 25, 0.1, 5, "synthetic", "reverse_restoration_mechanism_control"
        ),
        **masks,
        **classifications,
    )
    graph = build_v3_radiative_transport_edges(
        parent.grid,
        parent.source_mask,
        parent.target_mask,
        parent.parameters.receiver_area_m2,
        parent.parameters.source_height_m,
        parent.parameters.receiver_height_m,
        np.ones(parent.grid.shape),
        transport,
        max_edge_count=500000,
    )
    parent = replace(parent, exposure_edges=graph)
    bundle = replace(bundle, request=parent)

    def array_hash(array):
        a = np.ascontiguousarray(array)
        return hash_value(
            {
                "dtype": a.dtype.str,
                "shape": list(a.shape),
                "bytes": hashlib.sha256(a.tobytes()).hexdigest(),
            }
        )

    def state_hash(state, keep):
        return hash_value(
            {
                name: array_hash(getattr(state, name).ravel()[keep])
                for name in state.__dataclass_fields__
                if isinstance(getattr(state, name), np.ndarray)
            }
        )

    controls = []
    for scale in (1.0, 0.5):
        base = resolve_roof_layout(parent, inventory)
        layout = refine_roof_layout(
            base,
            inventory,
            cell_size_m=scale,
            refined_ids=tuple(inventory.by_id),
            footprints=(
                RoofIgnitionFootprint(
                    "S", inventory.by_id["S"].geometry, "synthetic_exact_source_roof"
                ),
            ),
        )
        membership = np.asarray([bool(g) for g in layout.owners])
        membership.setflags(write=False)
        engine = RoofJointEvaluationEngine(
            bundle,
            transport,
            inventory,
            source_policy="declared_fixed_sources",
            prepared_layout=layout,
            contact_builder=refined_contact_operator,
            radiation_builder=partial(
                roof_area_integrated_radiation_graph,
                integrated_ids=tuple(inventory.by_id),
                quadrature_spacing_m=0.5,
                frozen_integrated_node_mask=membership,
            ),
            max_radiation_edges=500000,
        )
        scenario = EvaluationScenario(f"restoration-h{scale:g}", footprint_ignitions(layout), 1500)
        keep = layout.cell_id != far
        results = []
        for restored in (False, True):
            traces = []
            context = {}
            original = native_engine._run_resolved_v3_simulation

            def traced(
                request, *, context=context, traces=traces, keep=keep, original=original, **kwargs
            ):
                exposure = SourceIndexedExposure.from_edges(request.exposure_edges)
                observer = kwargs.get("stop_when")
                context.update(request=request, contact=kwargs["contact"])

                def observe(state):
                    power = (
                        state.interval_mean_hrr_W * request.parameters.radiative_fraction
                    ).ravel()
                    active = np.flatnonzero(power > 0)
                    count = int(
                        np.sum(
                            exposure.source_offsets[active + 1] - exposure.source_offsets[active]
                        )
                    )
                    traces.append(
                        {
                            "time_hex": float(state.time_s).hex(),
                            "step": state.step_index,
                            "kept_state_sha256": state_hash(state, keep),
                            "radiation_path_for_observed_positive_sources": "none"
                            if not len(active)
                            else "dense"
                            if count
                            >= len(request.exposure_edges.source) * exposure.dense_edge_fraction
                            else "sparse",
                        }
                    )
                    return False if observer is None else observer(state)

                kwargs["stop_when"] = observe
                return original(request, **kwargs)

            native_engine._run_resolved_v3_simulation = traced
            try:
                plan = RoofJointPlan(
                    "restored" if restored else "cleared",
                    ("M",),
                    ground_evidence="synthetic_reverse_control",
                    cleared_physical_cell_ids=tuple(grass) if restored else (*grass, far),
                )
                record, post, result = engine.simulate(scenario, plan, stop_at_extinction=True)
            finally:
                native_engine._run_resolved_v3_simulation = original
            results.append((record, post, result, traces, context))
        old, new = results
        burned = np.flatnonzero(np.isfinite(old[2].final_state.ignition_time_s.ravel()))
        rows = []
        for item in (old, new):
            edges = item[4]["request"].exposure_edges
            mask = np.isin(edges.source, burned)
            rows.append(
                {k: array_hash(getattr(edges, k)[mask]) for k in ("source", "target", "weight")}
            )
        contact_old, contact_new = old[4]["contact"], new[4]["contact"]
        same_contact = (
            contact_old.neighbors.shape == contact_new.neighbors.shape
            and contact_old.neighbors[burned].tobytes() == contact_new.neighbors[burned].tobytes()
            and contact_old.geometry_fraction[burned].tobytes()
            == contact_new.geometry_fraction[burned].tobytes()
        )
        p = new[4]["request"].parameters
        R = ~keep
        area = p.covered_area_m2.ravel()[R]
        ambient = parent.scenario.ambient_temperature_K

        def field(name, p=p, R=R):
            return getattr(p, name).ravel()[R]

        cold_limit = monotone_thermal_timestep_limit_s(
            np.full(int(R.sum()), ambient),
            field("effective_heated_dry_mass_kg") / area,
            field("specific_heat_J_kg_K"),
            field("emissivity"),
            field("ambient_heat_transfer_coefficient_W_m2_K")
            + contact_new.parameters.heat_transfer_coefficient_W_m2_K,
            field("backing_conductance_W_m2_K"),
        )
        water = field("effective_heated_dry_mass_kg") * field("moisture_content_dry_basis")
        z = np.zeros(int(R.sum()))
        temp = np.full(int(R.sum()), ambient)
        flux = net_surface_heat_flux_W_m2(
            temp,
            z,
            z,
            field("absorptivity"),
            field("emissivity"),
            field("ambient_heat_transfer_coefficient_W_m2_K"),
            field("backing_conductance_W_m2_K"),
            ambient,
        )
        after_t, after_w = advance_surface_heating_and_evaporation(
            temp,
            water / area,
            field("effective_heated_dry_mass_kg") / area,
            field("specific_heat_J_kg_K"),
            flux,
            parent.scenario.max_dt_s,
            ambient_temperature_K=ambient,
        )
        offsets = SourceIndexedExposure.from_edges(old[4]["request"].exposure_edges).source_offsets
        bound = int(np.sum(offsets[burned + 1] - offsets[burned]))
        budget_differences = [
            {
                "field": item.name,
                "old": getattr(old[2].energy_budget, item.name),
                "new": getattr(new[2].energy_budget, item.name),
                "new_minus_old": getattr(new[2].energy_budget, item.name)
                - getattr(old[2].energy_budget, item.name),
            }
            for item in fields(old[2].energy_budget)
            if float(getattr(old[2].energy_budget, item.name)).hex()
            != float(getattr(new[2].energy_budget, item.name)).hex()
        ]
        controls.append(
            {
                "roof_scale_m": scale,
                "source_area_m2": float(
                    old[1]
                    .node_request.parameters.covered_area_m2.ravel()[
                        [e.col for e in old[1].node_request.scenario.ignitions]
                    ]
                    .sum()
                ),
                "restored_whole_ground_cell": far,
                "construction_area_reduction_m2": 25,
                "old_native_status": old[0].metrics["protection_status"],
                "new_native_status": new[0].metrics["protection_status"],
                "old_native_invalid": old[0].diagnostics["invalid_reasons"],
                "new_native_invalid": new[0].diagnostics["invalid_reasons"],
                "old_closed": old[0].diagnostics["extinction_closed"],
                "new_closed": new[0].diagnostics["extinction_closed"],
                "old_stop_s": old[2].final_state.time_s,
                "new_stop_s": new[2].final_state.time_s,
                "old_step_count": old[2].final_state.step_index,
                "new_step_count": new[2].final_state.step_index,
                "every_kept_native_step_state_clock_and_radiation_path_bitwise_equal": old[3]
                == new[3],
                "trace_sha256_old": hash_value(old[3]),
                "trace_sha256_new": hash_value(new[3]),
                "original_ignited_source_radiation_spans_exact": rows[0] == rows[1],
                "contact_padding_width_old": contact_old.neighbors.shape[1],
                "contact_padding_width_new": contact_new.neighbors.shape[1],
                "contact_original_ignited_rows_bytes_exact": same_contact,
                "old_radiation_edges": len(old[4]["request"].exposure_edges.source),
                "new_radiation_edges": len(new[4]["request"].exposure_edges.source),
                "original_all_ignited_outdegree": bound,
                "strict_always_sparse_proved": 4 * bound
                < len(old[4]["request"].exposure_edges.source),
                "restored_cold_CFL_s": cold_limit,
                "max_dt_s": parent.scenario.max_dt_s,
                "cold_CFL_guard": cold_limit >= parent.scenario.max_dt_s,
                "cold_temperature_exact_stationary": np.array_equal(after_t, temp),
                "ambient_zero_source_flux_nonpositive": bool(np.all(flux <= 0)),
                "restored_ignition_threshold_strictly_above_ambient": bool(
                    np.all(field("ignition_temperature_K") > ambient)
                ),
                "cold_water_storage_exact_stationary": np.array_equal(after_w * area, water),
                "old_and_new_fresh_native_validity_checked": True,
                "old_native_certificate_relabelled": False,
                "final_global_energy_budget_equal_in_this_control": old[2].energy_budget
                == new[2].energy_budget,
                "global_energy_budget_rounding_differences": budget_differences,
                "strict_village_certificate_generated_by_this_control": False,
            }
        )
    # Explicit numeric probes: full N never changes; masked reductions can.
    original_values = np.array([1e16, 1.0, 1.0, 1.0, 1.0])
    expanded_mask_values = np.insert(original_values, 1, np.zeros(127))
    if any(sha(path) != digest for path, digest in model_sources.items()):
        raise ValueError("native mechanism-control equations changed during the control")
    return {
        "schema_version": "reverse-restoration-small-native-controls-1",
        "native_CA_runs": 4,
        "native_model_source_sha256": model_sources,
        "synthetic_input_sources_sha256": {
            str(path): sha(path)
            for path in (
                ROOT / "tests/planning/test_roof_joint.py",
                ROOT / "examples/research/heritage/synthetic_case.py",
                ROOT / "examples/village/vegetation_v3/profiles.synthetic.yml",
            )
        },
        "no_propagation_coefficient_was_fitted_or_changed_between_plans": True,
        "controlled_physical_change": (
            "restore one prescribed far 25m2 nonroof cell; common layout and model unchanged"
        ),
        "controls": controls,
        "masked_reduction_zero_insertion_counterexample": {
            "numpy_version": np.__version__,
            "old_positive_and_zero_values": original_values.tolist(),
            "extra_zero_entries": 127,
            "old_sum": float(original_values.sum()),
            "new_sum": float(expanded_mask_values.sum()),
            "applies_to": (
                "susceptible/inert boolean-indexed reductions; not unchanged full-N state vectors"
            ),
            "bits_differ": original_values.sum().tobytes() != expanded_mask_values.sum().tobytes(),
        },
        "primary_numerical_source": (
            "https://raw.githubusercontent.com/numpy/numpy/v2.1.3/"
            "numpy/_core/src/umath/loops_arithm_fp.dispatch.c.src"
        ),
        "scope": "mechanism controls only;not a village restoration certificate",
    }


def main():
    import subprocess
    import tempfile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--plan-id")
    parser.add_argument("--small-controls", action="store_true")
    parser.add_argument("--native-screen-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    starting_sha = sha(__file__)
    protected = {Path(__file__).resolve()}
    if args.config is not None:
        protected.add(args.config.resolve())
    if args.run_root is not None:
        if args.output.resolve().is_relative_to(args.run_root.resolve()):
            parser.error("diagnostic output must be outside the original native archive")
        metadata = json.loads((args.run_root / "experiment.json").read_text())
        protected.update(Path(path).resolve() for path in metadata["references_sha256"])
    if args.output.resolve() in protected:
        parser.error("diagnostic output cannot overwrite a frozen input or source")
    if args.native_screen_worker:
        result = screen_native_anchor(args.run_root, args.config, args.plan_id)
    else:
        result = {
            "schema_version": "heritage-reverse-restoration-design-audit-1",
            "positive_area_village_restoration_certified": False,
            "large_village_CA_runs": 0,
            "large_village_graphs_constructed": 0,
            "native_original_results_relabelled": False,
            "sufficient_condition_design": {
                "scope": "same finite model, source, common physical indices and numerical backend",
                "required_premises": [
                    "same N and geometry; old cleared R indices are INERT, never deleted",
                    "R stock/masks only; kept parameters and permissions exact",
                    "unchanged first-retained source, 0-time footprint and event list",
                    "no firebrands, external feedback or opaque renormalized radiation",
                    "no possible old-ever-ignited I radiation/contact incoming edge to R",
                    "I radiation target/weight bytes exact; whole new graph admissible",
                    "all-active-subset sparse guard; no sparse/dense equivalence assumed",
                    "contact padding and I rows exact; source allocations preserved",
                    "R: positive ignition slack, nonpositive no-source flux, fixed water",
                    "R cooling limit including global contact h is at least unchanged maxdt",
                    "kept float update map/minimum timestep terms confirmed unchanged",
                    "new stock/derived endpoint validity and closure checked independently",
                ],
                "inductive_conclusion_if_all_premises_are_verified": (
                    "same old-kept heat/burn/clock trajectory; R remains cold and unignited; "
                    "same core safety and physical burned-out N/A"
                ),
                "not_invariant": [
                    "R state/validity/initial or residual fuel/water/retained area",
                    "masked aggregate budget last bits and native metadata fingerprints",
                ],
                "native_restored_execution_claimed": False,
                "village_status": "incomplete proof: screen only; use CA ablation until guarded",
            },
        }
        if args.run_root is not None:
            if args.config is None or args.plan_id is None:
                parser.error("config and plan-id are required for village screening")
            with tempfile.TemporaryDirectory(prefix="reverse-screen-") as folder:
                output = Path(folder) / "screen.json"
                process = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--native-screen-worker",
                        "--run-root",
                        str(args.run_root.resolve()),
                        "--config",
                        str(args.config.resolve()),
                        "--plan-id",
                        args.plan_id,
                        "--output",
                        str(output),
                    ],
                    capture_output=True,
                    text=True,
                )
                if process.returncode:
                    raise ValueError(process.stdout + process.stderr)
                result["anchor_screen"] = json.loads(output.read_text())
        if args.small_controls:
            sys.path.insert(0, str(ROOT / "src"))
            result["small_controls"] = small_native_controls()
    if sha(__file__) != starting_sha:
        raise ValueError("diagnostic implementation changed during analysis")
    result["analysis_source_sha256"] = starting_sha
    result["audit_sha256"] = hash_value(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "large_village_CA_runs": 0,
                "positive_area_village_restoration_certified": False,
            }
        )
    )


if __name__ == "__main__":
    main()
