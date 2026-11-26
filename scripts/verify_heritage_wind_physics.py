"""Verify wind-run input bytes against the previous two-village preregistrations."""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

from run_heritage_wind_strategies import solver_signature

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.village.physics_v3 import V3CellPhysicsArrays

ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    "tests/planning/test_wind_strategy_native_equivalence.py",
    "tests/planning/test_heritage_wind_validation.py",
]


def verify_village(name, current):
    historical_path = (
        ROOT / f"examples/research/heritage/confirmation-20261110/{name}-registration.json"
    )
    old = read_json(historical_path)
    seal = old.pop("registration_sha256")
    if seal != content_hash(old):
        raise ValueError(f"{name}: historical registration has changed")
    study_path = Path(current["study_path"]).resolve()
    if (
        study_path != Path(old["study_path"]).resolve()
        or current["inventory_sha256"] != old["inventory_sha256"]
    ):
        raise ValueError(f"{name}: wind study or inventory differs from historical registration")
    study = read_json(study_path)
    case_dir = (study_path.parent / study["case_dir"]).resolve()
    # Enumerate the fields consumed by the V3 case loader and the heritage study loader.
    parent_fields = [
        *V3CellPhysicsArrays.__dataclass_fields__,
        "domain_mask",
        "known_mask",
        "burnable_mask",
        "source_mask",
        "target_mask",
        "piloted_ignition_mask",
        "material_index",
        "landcover_index",
        "building_index",
        "ground_elevation_m",
        "material_evidence_index",
        "edge_source",
        "edge_target",
        "edge_weight",
    ]
    inputs = {case_dir / f"{field}.npy" for field in parent_fields}
    inputs |= {study_path, case_dir / "manifest.json"}
    inputs |= {
        (study_path.parent / study[key]).resolve()
        for key in ("structures_path", "image_reference", "labelme_annotations")
    }
    old_hashes = {Path(key).resolve(): digest for key, digest in old["input_files_sha256"].items()}
    if inputs != set(old_hashes):
        raise ValueError(f"{name}: original manifest and loaded input sets differ")
    files = []
    for path in sorted(inputs):
        digest = file_sha256(path)
        if digest != old_hashes[path]:
            raise ValueError(f"historical input hash mismatch: {path}")
        relative = str(path.relative_to(ROOT))
        fresh = current["input_files_sha256"].get(relative)
        if fresh is not None and fresh != digest:
            raise ValueError(f"wind registration input hash mismatch: {path}")
        files.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "actual_sha256": digest,
                "matches_archived_sha256": True,
                "also_bound_in_layout_registration": fresh is not None,
            }
        )
    return {
        "study_path": str(study_path.relative_to(ROOT)),
        "case_dir": str(case_dir.relative_to(ROOT)),
        "historical_registration_path": str(historical_path.relative_to(ROOT)),
        "historical_registration_file_sha256": file_sha256(historical_path),
        "historical_registration_content_sha256": seal,
        "inventory_sha256": current["inventory_sha256"],
        "verification": "current wind study refers to the same path and same inventory; "
        "all bytes of the parent V3 inputs and geospatial files match the archive",
        "verified_file_count": len(files),
        "verified_bytes": sum(f["size_bytes"] for f in files),
        "files": files,
    }


def run_regressions():
    with TemporaryDirectory() as temp:
        path = Path(temp) / "results.xml"
        args = [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-o",
            "junit_family=xunit1",
            f"--junitxml={path}",
            *TESTS,
        ]
        subprocess.run(args, check=True, cwd=ROOT)
        rows = []
        for node in ElementTree.parse(path).findall(".//testcase"):
            if any(node.find(tag) is not None for tag in ("error", "skipped", "failure")):
                raise ValueError("expected every regression to execute and pass")
            row = {"test": node.attrib["name"], "passed": True}
            prop = node.find("properties/property[@name='physics_evidence']")
            if prop is not None:
                row["evidence"] = json.loads(prop.attrib["value"])
            rows.append(row)
    if len(rows) < 6 or sum("evidence" in row for row in rows) != 2:
        raise ValueError("unexpected regression evidence coverage")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/heritage-wind-20261112")
    folder = parser.parse_args().output.resolve()
    path = folder / "registration.json"
    registration = read_json(path)
    seal = registration.pop("registration_sha256")
    if content_hash(registration) != seal:
        raise ValueError("wind registration seal mismatch")
    signature = solver_signature()
    data = {
        "schema_version": "heritage-wind-independent-physics-verification-1",
        "wind_registration_file_sha256": file_sha256(path),
        "wind_registration_content_sha256": seal,
        "solver_sha256": signature,
        "wind_scope": "uniform, static, synthetic, grid-north FROM; local radiative flame tilt",
        "verification_file_sha256": {
            str(p.relative_to(ROOT)): file_sha256(p)
            for p in [
                Path(__file__).resolve(),
                *[ROOT / f for f in TESTS],
                ROOT / "tests/planning/test_roof_joint.py",
                ROOT / "examples/research/heritage/synthetic_case.py",
            ]
        },
        "villages": {v: verify_village(v, r) for v, r in registration["villages"].items()},
        "regressions": run_regressions(),
    }
    if solver_signature() != signature:
        raise ValueError("solver sources changed during verification")
    data["verified_historical_file_count"] = sum(
        v["verified_file_count"] for v in data["villages"].values()
    )
    data["verification_sha256"] = content_hash(data)
    write_json(folder / "physics-verification.json", data)
    print(
        "Verified inputs",
        data["verified_historical_file_count"],
        "receipt",
        data["verification_sha256"],
    )


if __name__ == "__main__":
    main()
