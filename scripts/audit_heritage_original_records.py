"""Rebuild the published comparison tables from the original PyFireCA experiment records."""

import argparse
import csv
import hashlib
import io
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

import numpy as np


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def audit(project, photo_root=None):
    if not __debug__:
        raise RuntimeError("The original research checks require standard Python mode.")
    sys.path[:0] = [str(project / "scripts"), str(project / "src"), str(project)]
    import summarize_heritage_followup_20261103 as local
    import summarize_heritage_geometry_validation as geom
    import summarize_heritage_midbudget_routes as mid
    import summarize_heritage_wind_extinction as end
    import summarize_heritage_wind_strategies as pilot
    import summarize_heritage_wind_validation as standard
    from run_heritage_wind_strategies import solver_signature

    out = project / "output"
    p, f, s, g, m = [
        out / folder
        for folder in (
            "heritage-wind-20261112",
            "heritage-wind-final-20261112",
            "heritage-wind-validation-20261112",
            "heritage-geometry-validation-20261118",
            "heritage-midbudget-20261120",
        )
    ]
    evidence = {
        "solver_sha256": solver_signature(),
        "tables": {},
        "registrations": {},
        "materials": {},
    }
    assert (
        evidence["solver_sha256"]
        == "319063e2c633e904876b58c89e333c7004e564cdd15f44e71dd12bab72e8b0be"
    )
    for folder in (p, f, s, g, m):
        evidence["registrations"][folder.name] = sha(folder / "registration.json")

    def check_tables(folder, tables, writer):
        with tempfile.TemporaryDirectory() as tmp:
            for name, rows in tables.items():
                archived = folder / name
                rebuilt = Path(tmp) / name
                writer(rebuilt, rows)
                assert sha(rebuilt) == sha(archived), str(archived)
                evidence["tables"][str(archived.relative_to(project))] = {
                    "rows": len(rows),
                    "sha256": sha(archived),
                }
        print("Verified", folder.name, len(tables), "tables", flush=True)

    pr = read(p / "registration.json")
    assert pr["registration_sha256"] == pilot.content_hash(
        {k: v for k, v in pr.items() if k != "registration_sha256"}
    )
    for village in pr["villages"].values():
        assert sha(p / village["geojson_path"]) == village["geojson_sha256"]
    trials = pilot.load_results(p, pr)
    layout, radius = pilot.paired_rows(trials, pr)
    check_tables(
        p,
        {
            "cases.csv": [trials[k] for k in sorted(trials)],
            "heritage.csv": pilot.heritage_rows(trials),
            "by-wind.csv": pilot.summaries(trials),
            "by-source-stratum.csv": pilot.summaries(trials, "source_stratum"),
            "layout-pairs.csv": layout,
            "radius-pairs.csv": radius,
            "wind-pairs.csv": pilot.wind_effects(trials, pr),
        },
        pilot.write_csv,
    )
    finals = end.pair_cases(p, f, pr)
    check_tables(
        f, {"cases.csv": finals, "by-layout.csv": end.summarize(finals, pr)}, end.write_csv
    )
    sr = read(s / "registration.json")
    assert sr["original_registration_sha256"] == sha(p / "registration.json")
    assert sr["registration_sha256"] == standard.content_hash(
        {k: v for k, v in sr.items() if k != "registration_sha256"}
    )
    pools = standard.registered_fires(sr, pr)
    records = standard.load_cases(s, sr)
    native = standard.native_replay_evidence(s, records)
    solver = standard.solver_evidence(s, sr, records)
    old = read(s / "evidence.json")
    assert old["independent_native_replay"] == native and all(
        old[k] == v for k, v in solver.items()
    )
    assert old["case_and_extinction_certificate_fingerprints_sha256"] == standard.content_hash(
        sorted(
            (r["case_id"], r["result_sha256"], r["extinction_certificate_sha256"]) for r in records
        )
    )
    summary, sites = standard.summarize(records, sr)
    check_tables(
        s,
        {
            "cases.csv": records,
            "by-stratum.csv": summary,
            "paired-layouts.csv": standard.paired_summary(sites, sr, pools),
            "by-village-weighted.csv": standard.village_estimates(summary, pools),
        },
        standard.atomic_csv,
    )
    gr = geom.verify_registration(g)
    compact, heritage = geom.matched_cases(g, gr)
    assert (
        geom.replay_bindings(g, compact)
        == read(g / "evidence.json")["independent_numerical_replays"]
    )
    check_tables(
        g,
        {
            "paired-cases.csv": compact,
            "heritage-by-case.csv": heritage,
            "by-stratum.csv": geom.summarize(gr, compact),
            "by-heritage.csv": geom.heritage_summary(heritage),
        },
        geom.atomic_csv,
    )
    mr, ref, new = mid.verify()
    middle = mid.collected(mr, ref, new)
    strata, diffs = mid.tables(mr, middle)
    costs, maps = mid.construction(mr)
    assert maps == read(m / "核验记录.json")["changed_material_locations_m2"]
    check_tables(
        m,
        {
            "三方案配对.csv": middle,
            "分层结果.csv": strata,
            "分层配对差.csv": diffs,
            "路线清理量.csv": costs,
        },
        mid.atomic_csv,
    )
    side = project / "docs/results/heritage-followup-20261103"
    villages = [local.load_village(v, project / "runs/research") for v in ("batuan", "pingyan")]
    assert all(v["complete"] for v in villages)
    tables = dict(
        zip(
            (
                "core-checkpoints-dual-scale.csv",
                "common-retained-ordinary.csv",
                "intervention-areas.csv",
                "roof-ignition-curves.csv",
            ),
            local.aggregate_rows(villages),
            strict=True,
        )
    )

    def local_csv(path, rows):
        with (side / path.name).open(encoding="utf-8-sig", newline="") as inp:
            fields = next(csv.reader(inp))
        local.write_csv(path, rows, fields)

    check_tables(side, tables, local_csv)
    evidence["paired_scenarios"] = len(compact)
    evidence["layout_cases"] = len(records) + len(compact) + 2 * len(middle)
    assert (
        len(trials),
        len(finals),
        len(records),
        len(compact),
        len(middle),
        evidence["layout_cases"],
    ) == (760, 720, 4635, 927, 927, 7416)
    counts = Counter()
    count_rows = Counter()
    keys = {(r["village"], r["fire_id"], r["wind_id"]) for r in records}
    assert len(keys) == 927 and len({k[:2] for k in keys}) == 103
    for r in records:
        counts[r["plan_id"]] += int(r["final_any_heritage"])
        count_rows[r["plan_id"]] += 1
    for rows, name, attr in (
        (compact, "compact", "compact_core_ignited_count"),
        (middle, "uniform-mid", "uniform_heritage"),
        (middle, "aligned-mid", "aligned_heritage"),
    ):
        counts[name] = sum(int(r[attr]) > 0 for r in rows)
        count_rows[name] = len(rows)
    assert set(count_rows.values()) == {927}
    expected = {
        "baseline": 556,
        "heritage-narrow": 548,
        "multipoint-narrow": 486,
        "heritage-wide": 41,
        "multipoint-wide": 0,
        "compact": 352,
        "uniform-mid": 0,
        "aligned-mid": 0,
    }
    assert dict(counts) == expected
    evidence["affected_village_heritage_cases"] = dict(counts)
    evidence["pilot_one_hour_cases"] = len(trials)
    evidence["pilot_terminal_cases"] = len(finals)
    evidence["local_scale_cases"] = sum(len(v["records"]) for v in villages)

    def field(case, name):
        return np.load(case / (name + ".npy"), allow_pickle=False)

    for village, folder in {
        "batuan": "heritage-geometry-20261023-r2",
        "pingyan": "pingyan-physical-core-priority-20261029",
    }.items():
        case = project / "runs/research" / folder / "case"
        mask = field(case, "material_index") == 5
        values = [
            field(case, "ignition_temperature_K")[mask],
            field(case, "peak_hrrpua_W_m2_active")[mask],
            field(case, "initial_dry_fuel_mass_kg")[mask] / field(case, "covered_area_m2")[mask],
            field(case, "moisture_content_dry_basis")[mask],
        ]
        for a, v in zip(values, (520.0, 300000.0, 18.0, 0.10), strict=True):
            assert len(a) > 0 and np.allclose(a, v)
        evidence["materials"][village] = {
            "building_material_code": 5,
            "roof_material_parent_cells": int(mask.sum()),
            "ignition_K": 520,
            "peak_W_m2_active": 300000,
            "dry_kg_m2": 18,
            "moisture_dry_basis": 0.1,
            "manifest_sha256": sha(case / "manifest.json"),
        }

    field_file = out / "npj-heritage-science-zh" / "两座侗寨_建筑元胞材质类型赋值数据.zip"
    with ZipFile(field_file) as bundle, tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        copier = Path(__file__).with_name("apply_heritage_construction_types.py")
        subprocess.run(
            [
                sys.executable,
                str(copier),
                "--project",
                str(project),
                "--output",
                str(temp),
                "--verify-refinement",
            ],
            check=True,
        )
        for name in ("岜团_屋面元胞.csv", "平岩_屋面元胞.csv", "各村赋值统计.csv"):
            assert (temp / name).read_bytes() == bundle.read(name)
        for row in csv.DictReader(io.StringIO(bundle.read("各村赋值统计.csv").decode("utf-8-sig"))):
            v = {"平岩": "pingyan", "岜团": "batuan"}[row["村落"]]
            item = evidence["materials"][v]
            item["base_roof_nodes"] = item.get("base_roof_nodes", 0) + int(row["基准屋面元胞数"])
            item["roof_labels"] = item.get("roof_labels", 0) + int(row["屋面标签数"])
        if photo_root is not None:
            photographs = list(
                csv.DictReader(io.StringIO(bundle.read("照片例证.csv").decode("utf-8-sig")))
            )
            for picture in photographs:
                assert sha(photo_root / picture["原片相对路径"]) == picture["原片SHA256"]
            evidence["verified_field_figure_originals"] = len(photographs)
    assert sum(entry["roof_labels"] for entry in evidence["materials"].values()) == 1283
    assert sum(entry["base_roof_nodes"] for entry in evidence["materials"].values()) == 28716
    assert len(evidence["tables"]) == 25
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--photo-root", type=Path)
    args = parser.parse_args()
    audit_report = audit(args.project.resolve(), args.photo_root)
    text = json.dumps(audit_report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
