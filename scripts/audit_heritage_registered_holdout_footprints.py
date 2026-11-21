"""Check whether the original fixed plans reuse a design ignition patch in held-out fires."""

import json

import schedule_heritage_registered_joint_families as dispatch
from shapely.geometry import shape

DESIGN = (
    "heritage-joint-loss-native-pingyan-20261111",
    "heritage-protected-union-pingyan-20261111",
)


def actions(p):
    return tuple(sorted(p["demolished_ids"])), tuple(sorted(p["cleared_physical_cell_ids"]))


def supports(exp, plan):
    cases = [
        r
        for r in exp["source_realizations"]
        if r["plan_id"] == plan["plan_id"] and r["scenario_id"].endswith("-h1")
    ]
    if len(cases) != 4:
        raise ValueError("four original families are required for each fixed physical action")
    shapes = []
    for case in cases:
        family = case["scenario_id"].rsplit("-h", 1)[0]
        other = next(
            r
            for r in exp["source_realizations"]
            if r["plan_id"] == plan["plan_id"] and r["scenario_id"] == family + "-h0.5"
        )
        owner = case["actual_source_id"]
        if owner is None or owner != other["actual_source_id"]:
            raise ValueError("mesh representations must use the same retained physical source")
        footprint = exp["problem"]["fires"][family]["footprints"][owner]
        geom = shape(footprint["geometry"])
        if not geom.is_valid or abs(geom.area - 25.0) > 1e-5 or footprint["source_id"] != owner:
            raise ValueError("unexpected held or development physical support")
        shapes.append((owner, geom))
    return shapes


def audit():
    held_path = dispatch.RUN / "experiment.json"
    held = dispatch.read(held_path)
    if held["problem_sha256"] != dispatch.PROBLEM:
        raise ValueError("held scientific problem changed")
    output = []
    digests = {str(held_path): dispatch.digest(held_path)}
    for i, plan in enumerate(held["config"]["plans"]):
        p = dispatch.ROOT / "runs/research" / DESIGN[int(i > 0)] / "experiment.json"
        design = dispatch.read(p)
        if (
            design["problem"]["case_sha256"] != held["problem"]["case_sha256"]
            or design["problem"]["inventory_sha256"] != held["problem"]["inventory_sha256"]
        ):
            raise ValueError("original physical or building coordinate bases differ")
        digests[str(p)] = dispatch.digest(p)
        selected = [q for q in design["config"]["plans"] if actions(q) == actions(plan)]
        if len(selected) != 1:
            raise ValueError("the physical cleared roof/ground action must be identical")
        original = supports(design, selected[0])
        hold = supports(held, plan)
        tolerance = 1e-6
        same_owner = sum(any(owner == previous for previous, _ in original) for owner, _ in hold)
        same_patch = sum(
            any(g.symmetric_difference(f).area < tolerance for _, f in original) for _, g in hold
        )
        overlap = sum(any(g.intersection(f).area > tolerance for _, f in original) for _, g in hold)
        output.append(
            dict(
                plan_id=plan["plan_id"],
                design_families=len(original),
                held_families=len(hold),
                held_footprints_geometrically_identical_to_any_design_footprint=same_patch,
                held_footprints_with_positive_area_overlap_with_any_design_footprint=overlap,
                held_families_using_an_owner_building_also_present_in_design=same_owner,
            )
        )
    return dict(
        schema="heritage-design-held-physical-support-overlap-1",
        registered_held_candidate_sha256=held["config"]["final_candidates_sha256"],
        full_native_physical_experiment_hashes=digests,
        comparison_basis=(
            "For the same demolition and ground clearance, compare held actual 25 m2 "
            "polygons against all four design polygons using original projected geometry. "
            "Mesh pairs count once."
        ),
        equality_tolerance_m2=1e-6,
        overlap_tolerance_m2=1e-6,
        plans=output,
        geometry_coordinates_or_source_identifiers_disclosed=False,
        CA_simulations=0,
        source_reselections=0,
        statistical_independence_test=False,
    )


if __name__ == "__main__":
    out = (
        dispatch.ROOT
        / "docs/results"
        / "heritage-pingyan-independent-vs-design-physical-footprint-overlap-20261111.json"
    )
    result = audit()
    if out.exists() and dispatch.read(out) != result:
        raise ValueError("published support audit changed")
    dispatch.save(out, result)
    print(json.dumps(result["plans"]))
