"""Independently verify parent-polygon separation, action necessity and ambient invariance."""

import argparse
import json
import math
from dataclasses import asdict
from pathlib import Path

import numpy as np
import shapely

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study
from pyfireca.village.physics_v2 import (
    advance_surface_heating_and_evaporation,
    net_surface_heat_flux_W_m2,
)


def require(test, message):
    if not test:
        raise ValueError(message)


def distances_to_individual_core_atoms(geometry, core, outside, cutoff):
    """Envelope rejection is a lower bound; exact individual distances resolve every candidate."""
    bounds = shapely.bounds(geometry[outside])
    minimum = np.full(len(outside), np.inf)
    for node in core:
        left, bottom, right, top = geometry[node].bounds
        possible = (
            (bounds[:, 0] <= right + cutoff)
            & (bounds[:, 2] >= left - cutoff)
            & (bounds[:, 1] <= top + cutoff)
            & (bounds[:, 3] >= bottom - cutoff)
        )
        indices = np.flatnonzero(possible)
        minimum[indices] = np.minimum(
            minimum[indices], shapely.distance(geometry[outside[indices]], geometry[node])
        )
    # Any pair excluded by the expanded envelopes is farther than cutoff.
    return minimum


def audit(path):
    cert = read_json(path)
    seal = cert.pop("certificate_sha256")
    require(content_hash(cert) == seal, "certificate seal mismatch")
    root = Path(__file__).resolve().parents[1]
    require(
        file_sha256(root / "scripts/check_heritage_core_separation.py") == cert["method_sha256"],
        "certificate method changed",
    )
    run = Path(cert["native_reference_run"])
    experiment = read_json(run / "experiment.json")
    native = experiment["problem"]
    require(
        native["case_sha256"] == cert["native_case_sha256"]
        and experiment["problem_sha256"] == cert["native_problem_sha256"]
        and native["inventory_sha256"] == cert["inventory_sha256"],
        "native identity mismatch",
    )
    frozen = {p: v for p, v in experiment["source_sha256"].items() if p.startswith("src/")}
    for code, digest in frozen.items():
        require(file_sha256(run / "source-snapshot" / code) == digest, "snapshot changed")
        require(file_sha256(root / code) == digest, "reader differs from bound snapshot")
    config = experiment["config"]
    bundle, transport, inventory = load_study(Path(config["study_path"]))
    parent = bundle.request
    require(
        request_fingerprint(parent) == cert["native_case_sha256"]
        and inventory.sha256 == cert["inventory_sha256"]
        and asdict(transport) == native["transport_parameters"]
        and parent.firebrands is None,
        "physical inputs changed",
    )
    radius = transport.exposure_radius_m
    require(radius == cert["model_radius_m"] and radius > 0, "support radius mismatch")
    layout = resolve_roof_layout(
        parent,
        inventory,
        max_nodes=config["max_nodes"],
        core_overlap_policy=config.get("core_overlap_policy", "reject"),
    )
    geometry = np.asarray(layout.geometry, dtype=object)
    core_ids = {b.building_id for b in inventory.targets}
    core_mask = np.array([bool(set(group) & core_ids) for group in layout.owners])
    core = np.flatnonzero(core_mask)
    outside = np.flatnonzero(layout.node_inputs.burnable_mask.ravel() & ~core_mask)
    cutoff = radius + parent.grid.cell_size_m
    distances = distances_to_individual_core_atoms(geometry, core, outside, cutoff)
    obligatory = outside[distances <= radius]
    roof, grass = set(cert["roof_actions"]), set(cert["ground_parent_actions"])
    allowed = (
        parent.domain_mask
        & parent.known_mask
        & parent.burnable_mask
        & (parent.building_index < 0)
        & (parent.parameters.covered_area_m2 > 0)
    ).ravel()
    protected = core_ids | set(inventory.protected_ids)
    occupied = {int(c) for c, ids in zip(layout.cell_id, layout.owners, strict=True) if ids}
    require(
        roof <= set(inventory.candidate_ids)
        and not roof & protected
        and not grass & occupied
        and all(allowed[c] for c in grass)
        and parent.grid.cell_size_m == 5,
        "actions conflict with legal ordinary roof or ground scope",
    )
    # If any owner of a required roof is taken back, that original physical patch stays.
    forced_roof = {owner for i in obligatory for owner in layout.owners[i]}
    forced_grass = {int(layout.cell_id[i]) for i in obligatory if not layout.owners[i]}
    require((forced_roof, forced_grass) == (roof, grass), "extra or absent mandatory action")
    cleared = np.array([bool(g) and set(g) <= roof for g in layout.owners])
    cleared |= np.isin(layout.cell_id, list(grass))
    require(not np.any(cleared[core]) and np.all(cleared[obligatory]), "isolation mask invalid")
    retained_distances = distances[~cleared[outside]]
    min_gap = float(retained_distances.min())
    require(radius < min_gap < cutoff, "retained gap was not bracketed")
    require(
        abs(min_gap - cert["minimum_remaining_gap_m"]) < 1e-7
        and len(obligatory) == cert["necessary_external_parent_patch_count"]
        and len(retained_distances) == cert["remaining_external_burnable_parent_patch_count"],
        "independent gap or complete counts disagree",
    )
    # Evaluate removed union independently from full original building geometries.
    selected_geometry = shapely.union_all(
        [b.geometry for b in inventory.buildings if b.building_id in roof]
    )
    retained_geometry = shapely.union_all(
        [b.geometry for b in inventory.buildings if b.building_id not in roof]
    )
    roof_area = float(selected_geometry.difference(retained_geometry).area)
    cost = math.fsum((roof_area, len(grass) * parent.grid.cell_area_m2))
    require(
        abs(cost - cert["minimum_construction_area_m2"]) <= 1e-6, "raw polygon AND-cost disagrees"
    )
    pars = layout.node_inputs.parameters
    ambient = parent.scenario.ambient_temperature_K
    require(cert["initial_ambient_K"] == ambient, "ambient differs")
    temperature = np.full(len(core), ambient)
    zero = np.zeros_like(temperature)
    flux = net_surface_heat_flux_W_m2(
        temperature,
        zero,
        zero,
        pars.absorptivity.ravel()[core],
        pars.emissivity.ravel()[core],
        pars.ambient_heat_transfer_coefficient_W_m2_K.ravel()[core],
        pars.backing_conductance_W_m2_K.ravel()[core],
        ambient,
    )
    mass = pars.effective_heated_dry_mass_kg.ravel()[core] / pars.covered_area_m2.ravel()[core]
    water = mass * pars.moisture_content_dry_basis.ravel()[core]
    next_temp, _ = advance_surface_heating_and_evaporation(
        temperature,
        water,
        mass,
        pars.specific_heat_J_kg_K.ravel()[core],
        flux,
        config["max_dt_s"],
        ambient_temperature_K=ambient,
    )
    require(
        np.all(flux == 0)
        and np.array_equal(next_temp, temperature)
        and np.all(pars.ignition_temperature_K.ravel()[core] > temperature),
        "native ambient induction fails",
    )
    return {
        "village": cert["village"],
        "input_certificate_sha256": file_sha256(path),
        "frozen_native_source_files_verified": len(frozen),
        "model_support_m": radius,
        "original_domain_parent_patches": len(geometry),
        "core_parent_patches": len(core),
        "external_burnable_patches_examined": len(outside),
        "compulsory_external_patches": len(obligatory),
        "inclusion_forced_roof_actions": len(roof),
        "inclusion_forced_ground_cells": len(grass),
        "native_ground_cell_area_m2": parent.grid.cell_area_m2,
        "minimum_verified_retained_gap_m": min_gap,
        "certified_C_m2": cert["minimum_construction_area_m2"],
        "raw_owner_difference_C_m2": cost,
        "remaining_possible_cross_supports": 0,
        "native_ambient_step_unchanged": True,
        "protected_buildings": len(core_ids),
        "whole_domain_burned_area_or_count_computed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("certificates", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = {
        "schema_version": "heritage-independent-core-separation-verification-1",
        "auditor_sha256": file_sha256(__file__),
        "villages": [audit(p) for p in args.certificates],
        "minimum_for": (
            "strict polygon separation in the declared eligible whole-owner and whole-ground "
            "action space"
        ),
        "decision_time": (
            "clearance precedes ignition; any subsequent direct event must act on retained "
            "exterior fuel"
        ),
        "heat_induction": (
            "The frozen point and area radiation supports require unshifted horizontal "
            "separation within the radius. Refined local flame contacts require polygon "
            "boundary intersection. Every refinement is a subset of its parent component. No "
            "incoming channels plus unignited ambient cores yields zero temperature increment "
            "and zero emitted power at every valid solver step."
        ),
        "cutoff_model": (
            "No firebrands, additional external heat source or time-varying ambient forcing."
        ),
        "global_CA_protection_minimum_certified": False,
        "new_CA_trajectories": 0,
        "full_radiation_graphs_built": 0,
    }
    result["audit_seal"] = content_hash(result)
    with args.output.open("x") as f:
        json.dump(result, f, indent=2, allow_nan=False)
        f.write("\n")


if __name__ == "__main__":
    main()
