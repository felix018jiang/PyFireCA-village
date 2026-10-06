"""Restore original classification provenance without changing simulated materials."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from pyfireca.village.workflow_v3 import load_v3_case_bundle, write_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]
LABELS = {"0": "待核实地表", "1": "RGB推测", "2": "建筑标注(材质未实测)", "255": "域外"}


def prepare(case, initial_run, original, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    original = Path(original)
    bundle = load_v3_case_bundle(case)
    initial_viewer = Path(initial_run) / "viewer"
    manifest = json.loads((initial_viewer / "manifest.json").read_text())
    viewer_grid = json.loads((initial_viewer / "grid.json").read_text())
    if any(viewer_grid.get(key) != value for key, value in asdict(bundle.request.grid).items()):
        raise ValueError("initial replay grid does not match case")
    expected_contact = (
        json.loads(json.dumps(bundle.request.flame_contact.to_mapping()))
        if bundle.request.flame_contact is not None
        else None
    )
    if manifest.get("flame_contact") != expected_contact:
        raise ValueError("initial replay contact parameters do not match case")
    saved_material = np.fromfile(initial_viewer / "material.bin", dtype="<i4")
    np.testing.assert_array_equal(saved_material, bundle.request.material_index.ravel())
    spatial = json.loads((original / "manifest.json").read_text())
    if any(spatial["grid"].get(key) != value for key, value in asdict(bundle.request.grid).items()):
        raise ValueError("original evidence grid does not match simulation grid")
    domain = np.load(original / "arrays/domain_mask.npy", allow_pickle=False)
    known = np.load(original / "arrays/known_mask.npy", allow_pickle=False)
    old_material = np.load(original / "arrays/landcover_index.npy", allow_pickle=False)
    evidence = np.load(original / "arrays/evidence_index.npy", allow_pickle=False).astype(np.uint8)
    np.testing.assert_array_equal(domain, bundle.request.domain_mask)
    if np.any(old_material[known] != bundle.request.landcover_index[known]):
        raise ValueError("known classifications changed; original provenance cannot be reused")
    evidence[domain & ~known] = 0
    evidence[~domain] = 255
    request = replace(bundle.request, material_evidence_index=evidence)
    metadata = dict(bundle.metadata)
    metadata["material_evidence_source"] = dict(
        path=str(original.resolve()),
        manifest_sha256=hashlib.sha256((original / "manifest.json").read_bytes()).hexdigest(),
        evidence_sha256=hashlib.sha256(
            (original / "arrays/evidence_index.npy").read_bytes()
        ).hexdigest(),
        policy="restore original provenance; leave all material and physics arrays unchanged",
    )
    write_v3_case_bundle(
        request,
        output / "case",
        metadata=metadata,
        material_labels=bundle.material_labels,
        landcover_labels=bundle.landcover_labels,
    )
    viewer = output / "run/viewer"
    shutil.copytree(initial_viewer, viewer)
    (viewer / "material_evidence.bin").write_bytes(evidence.tobytes(order="C"))
    manifest["material_evidence"] = dict(
        path="material_evidence.bin", dtype="uint8", order="C", labels=LABELS
    )
    manifest["scenario"]["material_evidence_source"] = metadata["material_evidence_source"]
    (viewer / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    for name in (
        "app.js",
        "styles.css",
        "core-metrics.mjs",
        "chunk-replay.mjs",
        "component-view.mjs",
    ):
        shutil.copy2(ROOT / "src/pyfireca/village/web" / name, viewer / "assets" / name)
    shutil.copy2(ROOT / "src/pyfireca/village/web/index.html", viewer / "index.html")
    counts = {LABELS[str(code)]: int((evidence == code).sum()) for code in (0, 1, 2, 255)}
    record = dict(
        counts=counts,
        source_case=str(Path(case).resolve()),
        initial_result_source=str(Path(initial_run).resolve()),
        physics_recomputed=False,
    )
    (output / "evidence-report.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(record, ensure_ascii=False), flush=True)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case", type=Path, default=ROOT / "runs/gaoyou-finite-receiver-full-20261006/case"
    )
    parser.add_argument(
        "--initial-run", type=Path, default=ROOT / "runs/gaoyou-finite-receiver-full-20261006/run"
    )
    parser.add_argument(
        "--original", type=Path, default=ROOT / "runs/gaoyou-vegetation-v3/v01-spatial-utm49n-final"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.case, args.initial_run, args.original, args.output)
