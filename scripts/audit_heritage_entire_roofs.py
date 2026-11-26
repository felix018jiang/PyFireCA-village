"""Count original annotated roof footprints for which every modeled fuel patch burned out."""

import argparse
from pathlib import Path

import numpy as np
from audit_heritage_core_exposure import sources
from prepare_heritage_joint_longcap import encoded, immutable

from pyfireca.planning.fire_loss import _COMPONENT_ID
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json


def count(manifest):
    folder = manifest.parent
    files = read_json(manifest)["files_sha256"]
    for name in ("check.json", "fire-loss.json", "layout.npz", "loss-basis.npz", "final-state.npz"):
        if file_sha256(folder / name) != files[name]:
            raise ValueError("native roof attribution or fire state changed")
    check = read_json(folder / "check.json")
    if check["status"] != "passed" or check["evidence"]["invalid_reasons"]:
        raise ValueError("only closed, heritage-protected cohort observations allowed")
    loss = read_json(folder / "fire-loss.json")["physical_remaining_roof_annotation_counts"]
    rows = {r["building_id"]: r for r in loss["per_original_owner"]}
    with (
        np.load(folder / "layout.npz") as layout,
        np.load(folder / "loss-basis.npz") as basis,
        np.load(folder / "final-state.npz") as state,
    ):
        area = basis["baseline_covered_area_m2"].ravel()
        retained = basis["post_intervention_covered_area_m2"].ravel()
        burned = np.isfinite(state["burnout_time_s"].ravel())
        if not np.array_equal(area[burned], retained[burned]) or np.any(area[burned] <= 0):
            raise ValueError("a cleared patch was charged as fire damage")
        components, cells = layout["component_id"], layout["physical_cell_id"].ravel()
        represented, any_burned, any_unburned, ambiguous = set(), set(), set(), set()
        for node in np.flatnonzero(area > 0):
            comp = _COMPONENT_ID.fullmatch(components[node])
            if comp is None or int(comp[1]) != cells[node]:
                raise ValueError("native immutable component cannot be traced to original roof")
            if comp[2] == "ground":
                continue
            ids = comp[2].split("+")
            represented.update(ids)
            if len(ids) > 1:
                ambiguous.update(ids)
            (any_burned if burned[node] else any_unburned).update(ids)
        entire = any_burned - any_unburned
        if represented != set(rows) - set(loss["unrepresented_original_ids"]):
            raise ValueError("original modeled roof population differs from the native ledger")
        if any_burned != {
            k for k, r in rows.items() if (r["burned_out_area_m2"]["upper"] or 0) > 0
        }:
            raise ValueError("burned patch intersections differ from the native ledger")
        if any(
            rows[k]["original_covered_area_m2"] != rows[k]["burned_out_area_m2"]["upper"]
            for k in entire
        ):
            raise ValueError("whole modeled roof does not reproduce the original area sum")
        return dict(
            entire_original_footprints=len(entire),
            entire_with_overlapping_original_annotations=len(entire & ambiguous),
            partially_burned_original_footprints=len(any_burned - entire),
            unrepresented_original_annotations=len(set(rows) - represented),
        )


def audit():
    refs, cohorts = sources()
    summaries = []
    for village, plan, cohort, paths, expected in cohorts:
        lookup = {
            file_sha256(p): p for run in paths for p in run.glob("evaluations/*/complete.json")
        }
        expected_count = 16 if village == "batuan" and plan == "joint" else 8
        if len(set(expected)) != len(expected) or len(expected) != expected_count:
            raise ValueError("incorrect cohort size")
        records = [count(lookup[h]) for h in expected]
        summaries.append(
            dict(
                village=village,
                plan=plan,
                cohort=cohort,
                cases=len(records),
                worst_entire_original_roof_footprints=max(
                    v["entire_original_footprints"] for v in records
                ),
                worst_partially_burned_original_roof_footprints=max(
                    v["partially_burned_original_footprints"] for v in records
                ),
                maximum_whole_burned_with_overlapping_annotations=max(
                    v["entire_with_overlapping_original_annotations"] for v in records
                ),
                unrepresented_original_roof_annotations=max(
                    v["unrepresented_original_annotations"] for v in records
                ),
                manifest_sha256=sorted(expected),
            )
        )
    return dict(
        schema_version="entire-original-modeled-roof-footprints-1",
        definition=(
            "An original annotated roof has positive modeled area and every one of its "
            "positive-area original physical patches, including shared patches, burned "
            "out. Cleared patches count as unburned. Each roof label is counted once."
        ),
        interpretation=(
            "This measures roof-fuel exhaustion over the entire modeled footprint. "
            "Building collapse and structural destruction require separate evidence."
        ),
        cohorts=summaries,
        source_receipts_sha256=refs,
        additional_native_fires=0,
        method_sha256=file_sha256(__file__),
    )


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", required=True, type=Path)
    path = cli.parse_args().output
    report = audit()
    report["receipt_sha256"] = content_hash(report)
    immutable(path, encoded(report))
