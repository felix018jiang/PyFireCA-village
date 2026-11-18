"""Read saved gulou failures and attribute radiation without another thermal solve."""

from __future__ import annotations

import argparse
import json
import tempfile
import zipfile
from pathlib import Path

from audit_heritage_bridge_paths import attribute
from shapely.geometry import Polygon
from shapely.ops import unary_union


def audit(roots):
    runs = []
    inventory = json.loads((Path(roots[0]) / "inventory.json").read_text())
    geometry = {
        b["building_id"]: unary_union([Polygon(p["exterior"], p["holes"]) for p in b["polygons"]])
        for b in inventory["buildings"]
    }
    neighbours = []
    for identifier, roof in geometry.items():
        distance = roof.distance(geometry["B0073"])
        if identifier == "B0073" or distance > 20:
            continue
        neighbours.append(
            {
                "building_id": identifier,
                "distance_to_gulou_m": distance,
                "footprint_area_m2": roof.area,
                "overlaps": [
                    {"other_id": other, "area_m2": roof.intersection(g).area}
                    for other, g in geometry.items()
                    if other != identifier and roof.intersection(g).area > 1e-6
                ],
            }
        )
    for root in map(Path, roots):
        experiment = json.loads((root / "experiment.json").read_text())
        cohort = experiment["config"]["validation_cohort"]
        summary = json.loads((root / "summary.json").read_text())
        for row in summary["runs"]:
            if row["plan_id"] != "remove-B0061" or not any(
                o["building_id"] == "B0073" and o["ignited"] for o in row["core_outcomes"]
            ):
                continue
            run_id = f"{row['draw_id']}-h{row['scale_m']}".replace(".", "p")
            folder = root / run_id / row["plan_id"]
            with tempfile.TemporaryDirectory(prefix="pyfireca-gulou-audit-") as temporary:
                scratch = Path(temporary)
                for name in ("layout.json", "evaluation.json", "final-state.npz", "contact.npz"):
                    if (folder / name).exists():
                        (scratch / name).symlink_to((folder / name).resolve())
                with zipfile.ZipFile(folder / "node-storage-not-map.zip") as saved:
                    saved.extractall(scratch)
                details = attribute(scratch, "B0073")
            details = {key.replace("bridge", "gulou"): value for key, value in details.items()}
            details.update(
                case=str(folder), cohort=cohort, draw_id=row["draw_id"], scale_m=row["scale_m"]
            )
            runs.append(details)
    return {
        "schema_version": "heritage-gulou-path-audit-1",
        "new_thermal_runs": 0,
        "target_id": "B0073",
        "neighbours": sorted(neighbours, key=lambda n: n["distance_to_gulou_m"]),
        "runs": runs,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roots", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(audit(args.roots), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
