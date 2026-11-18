"""Bind the four selected coarse cases to shared intact mesh counts before native replay."""

import json
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    base = root / "runs/research"
    prod = base / "heritage-hrr-error-recovery-producer-20261111"
    out = base / "heritage-pingyan-zero-core-only-coarse-control-20261111"
    cfg = (
        root
        / "examples/research/heritage/area-20261110/pingyan-zero-core-only-coarse-control-20261111.json"
    )
    evidence_path = (
        root / "docs/results/heritage-pingyan-zero-heat-native-support-and-capacity-20261111.json"
    )
    control_path = (
        root
        / "docs/results/heritage-pingyan-zero-core-only-coarse-control-registration-20261111.json"
    )
    sys.path[:0] = [str(prod / "scripts"), str(prod / "src")]
    import run_heritage_joint_comparison as native
    from pyfireca.planning.heritage import content_hash
    from pyfireca.planning.strategy_io import file_sha256, read_json

    c = read_json(cfg)
    witness = read_json(evidence_path)
    control = read_json(control_path)
    for sealed in (witness, control):
        if sealed["receipt_sha256"] != content_hash(
            {k: v for k, v in sealed.items() if k != "receipt_sha256"}
        ):
            raise ValueError("source geometry witness changed")
    if (
        not Path(native.__file__).is_relative_to(prod)
        or file_sha256(prod / "src/pyfireca/village/solver_v3.py")
        != c["required_solver_source_sha256"]
    ):
        raise RuntimeError("wrong frozen solver")
    if out.exists():
        raise FileExistsError("coarse output exists; refusing duplicated allocation")

    def selected_intact_counts(oracle):
        if (
            content_hash(oracle.problem["fires"])
            != witness["common_layout_source_and_refinement_spec_sha256"]
            or content_hash(oracle.problem["fires"])
            != control["full_common_source_footprints_layout_and_quadrature_specs_sha256"]
            or not oracle.freeze_integration_membership
        ):
            raise ValueError("geometry or fixed quadrature differs")
        expected = {s for _, s in native.execution_checks(c)}
        rows = []
        b = oracle.base.physical_request
        eligible = (
            b.domain_mask
            & b.known_mask
            & b.burnable_mask
            & (b.building_index < 0)
            & (b.parameters.covered_area_m2 > 0)
        ).ravel()
        (p,) = oracle.plans.values()
        if not all(eligible[i] for i in p.cleared_physical_cell_ids):
            raise ValueError("noneligible control ground intervention")
        for i, f in enumerate(c["source_fires"], 1):
            label = f["fire_id"]
            s = label + "-h1"
            if s not in expected or oracle.actual_source_for(p, s) is None:
                raise ValueError("coarse registered source missing")
            source = oracle.actual_source_for(p, s)
            geometry = oracle.fires[label]["footprints"][source]
            raw = next(r for r in witness["rows"] if r["draw_ordinal"] == i and r["scale_m"] == 1)
            (support,) = raw["physical_support_by_plan"].values()
            if (
                support["footprint_sha256"] != geometry.sha256
                or abs(support["native_budget"]["covered_area_m2"] - 25) > 1e-6
                or source in p.demolished_ids
            ):
                raise ValueError("common physical source changed")
            bounds = {k: raw[k] for k in ("raw_point_edges", "integrated_edges_upper_bound")}
            if max(bounds.values()) > oracle.max_radiation_edges or raw["nodes"] > oracle.max_nodes:
                raise ValueError("coarse intact bounds exceed hard native ceiling")
            rows.append(
                dict(
                    fire_id=label,
                    scale_m=1.0,
                    nodes=raw["nodes"],
                    **bounds,
                    passed=True,
                    actual_stage_upper_bound_by_mask_monotonicity={
                        "after_roofs_before_grass": bounds,
                        "after_roofs_and_grass": bounds,
                    },
                )
            )
        if len(rows) != len(expected) or len(rows) != 4:
            raise ValueError("selected coarse denominator changed")
        return rows

    native.prepare_geometry_preflight = selected_intact_counts
    native.prepare(cfg, out)
    binding = dict(
        schema_version="shared-mesh-exact-intact-bounds-for-selected-coarse-control-1",
        source_geometry_sha256=file_sha256(evidence_path),
        configuration_sha256=file_sha256(cfg),
        preflight_file_sha256=file_sha256(out / "preflight.json"),
        experiment_sha256=file_sha256(out / "experiment.json"),
        method_sha256=file_sha256(__file__),
        verified_common_source_mesh_rows=4,
        unselected_half_metre_cases_authorized=0,
        proof="Both native builders retain the registered integrated mask and quadrature, and remove emitters or targets under clearing. Each point or area-integrated admissible pair is a subset of the same counted intact geometry. Physical budget 4 independently; no half-metre trajectory allowed.",
        new_CA_checks=0,
    )
    binding["receipt_sha256"] = content_hash(binding)
    with (out / "selected-intact-geometry-binding.json").open("x") as f:
        json.dump(binding, f, indent=2)
        f.write("\n")
    print("coarse intact bounds and all four physical supports accepted", flush=True)


if __name__ == "__main__":
    main()
