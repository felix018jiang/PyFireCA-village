"""Verify the restored manuscript and rebuild its main results from signed case records."""

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "paper"
WORD = PAPER / "两座侗寨防火线设计_中文大纲与方法结果全文.docx"
ARCHIVE = PAPER / "data/restored-experiments.zip"
ARCHIVE_SHA = "828b522f19b15cdc2add4f43cf317eb3e82f9e9ecae00d7801c6f5f99360c7eb"
WORD_SHA = "a3d6dd2f30e347b419e02e427560e32002b06ed0aaa2ebbc3bb199e68f7cfddb"
STUDIES = {
    "heritage-followup-20261103": "local-position",
    "heritage-wind-20261112": "pilot-hour",
    "heritage-wind-final-20261112": "pilot-terminal",
    "heritage-wind-validation-20261112": "standard",
    "heritage-geometry-validation-20261118": "compact",
    "heritage-midbudget-20261120": "midbudget",
}
PRIMARY = ("standard", "compact", "midbudget")
LAYOUTS = (
    "baseline",
    "heritage-narrow",
    "multipoint-narrow",
    "heritage-wide",
    "multipoint-wide",
    "compact",
    "uniform-mid",
    "open-ground-mid",
)
STRATA = (("pingyan", "near"), ("pingyan", "far"), ("batuan", "near"), ("batuan", "far"))
COMPARISONS = (
    ("uniform-mid", "multipoint-wide"),
    ("open-ground-mid", "multipoint-wide"),
    ("open-ground-mid", "uniform-mid"),
)
WORD_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def signed_digest(record, key, *, compact=False):
    payload = {k: v for k, v in record.items() if k != key}
    text = json.dumps(
        payload, sort_keys=True, allow_nan=False, separators=(",", ":") if compact else None
    )
    return digest(text.encode())


def verify_publication(archive):
    manifest = json.loads(archive.read("文件SHA256.json"))
    require(set(archive.namelist()) == {*manifest, "文件SHA256.json"}, "Archive inventory differs")
    for name, checksum in manifest.items():
        require(digest(archive.read(name)) == checksum, f"Archive member differs: {name}")
        path = Path(name)
        if name.startswith(("项目/src/", "项目/scripts/")):
            local = ROOT / path.relative_to("项目")
        elif name.startswith("原版补充数据/"):
            local = PAPER / "data/supplement" / path.name
        elif name.startswith("论文图件/"):
            local = PAPER / "figures" / path.name
        elif name.startswith("论文表格/"):
            local = PAPER / "tables" / path.name
        else:
            continue
        require(digest(local.read_bytes()) == checksum, f"Published file differs: {local}")

    audit = json.loads(archive.read("核验/数据核验.json"))
    require(json.loads((PAPER / "data/verification.json").read_bytes()) == audit, "Audit differs")
    require(len(audit["tables"]) == 25, "Expected 25 original result tables")
    for original, evidence in audit["tables"].items():
        path = Path(original)
        published = PAPER / "data/results" / STUDIES[path.parent.name] / path.name
        require(digest(published.read_bytes()) == evidence["sha256"], f"Table differs: {published}")
    require(
        digest((PAPER / "data/input-index.json").read_bytes())
        == manifest["核验/登记输入索引.json"],
        "Input index differs",
    )
    for path in (PAPER / "materials").glob("*.csv"):
        require(digest(path.read_bytes()) == manifest["建筑田野构造/" + path.name], path.name)
    photo_root = PAPER / "materials/photos"
    originals = {
        row["补充图S8分图"]: row["原片SHA256"]
        for row in read_rows(PAPER / "materials/照片例证.csv")
    }
    photos = read_rows(photo_root / "files.csv")
    require([row["panel"] for row in photos] == list("abcdefgh"), "S8 photo index differs")
    for photo in photos:
        name = photo["panel"] + ".jpg"
        require(
            photo["file"] == name
            and photo["source_sha256"] == originals[photo["panel"]]
            and digest((photo_root / name).read_bytes()) == photo["published_sha256"],
            f"Field photo differs: {name}",
        )
    require(
        len(audit["materials"]) == 2
        and sum(v["base_roof_nodes"] for v in audit["materials"].values()) == 28716,
        "Building cell count differs",
    )
    return audit


def verify_word():
    require(digest(WORD.read_bytes()) == WORD_SHA, "Manuscript differs from the restored Word file")
    with ZipFile(WORD) as doc:
        root = ET.fromstring(doc.read("word/document.xml"))
        media = {digest(doc.read(n)) for n in doc.namelist() if n.startswith("word/media/")}
    figures = sorted((PAPER / "figures").glob("Fig*"))
    require(
        len(figures) == 15 and media == {digest(p.read_bytes()) for p in figures},
        "Manuscript figures differ",
    )
    require(len(root.findall(".//m:oMathPara", WORD_NS)) == 5, "Expected five editable equations")
    tables = []
    for tab in root.findall("./w:body/w:tbl", WORD_NS):
        rows = []
        for row in tab.findall("w:tr", WORD_NS):
            cells = []
            for cell in row.findall("w:tc", WORD_NS):
                paragraphs = [
                    "".join(t.text or "" for t in p.findall(".//w:t", WORD_NS))
                    for p in cell.findall("w:p", WORD_NS)
                ]
                cells.append("\n".join(paragraphs))
            rows.append(cells)
        tables.append(rows)
    table_csvs = []
    for p in (PAPER / "tables").glob("*.csv"):
        with p.open(encoding="utf-8-sig", newline="") as source:
            table_csvs.append(list(csv.reader(source)))
    require(
        len(table_csvs) == len(tables) == 16 and all(t in table_csvs for t in tables),
        "Word tables differ from published CSVs",
    )
    return tables[:3]


def primary_cases(archive, audit):
    pairs = defaultdict(dict)
    for study, folder in STUDIES.items():
        if folder not in PRIMARY:
            continue
        registration = audit["registrations"][study]
        prefix = f"项目/output/{study}/cases/"
        for path in (name for name in archive.namelist() if name.startswith(prefix)):
            row = json.loads(archive.read(path))
            name = row["case_id"]
            require(name == Path(path).stem and row["registration_sha256"] == registration, name)
            require(signed_digest(row, "result_sha256") == row["result_sha256"], name)
            cert = json.loads(archive.read(path.replace("/cases/", "/certificates/")))
            require(
                signed_digest(cert, "certificate_sha256", compact=True)
                == cert["certificate_sha256"]
                == row["extinction_certificate_sha256"],
                name,
            )
            end = row["final"]
            hour = row["at_60min"]
            require(
                row["extinction_closed"]
                and end["active_m2"] == 0
                and cert["burning_node_count"] == cert["future_prescribed_event_count"] == 0
                and cert["eligible_thermal_ignition_node_count"] == cert["future_mean_hrr_W"] == 0
                and math.isclose(cert["actual_stop_time_s"], end["elapsed_simulated_s"])
                and math.isclose(
                    cert["cumulative_burned_out_covered_area_m2"],
                    end["ground_burned_out_m2"] + end["roof_burned_out_m2"],
                    rel_tol=1e-9,
                    abs_tol=1e-6,
                ),
                name,
            )
            for observation in (hour, end):
                require(
                    observation["energy_closure_error_J"]
                    <= observation["energy_closure_tolerance_J"],
                    name,
                )
            key = (row["village"], row["stratum"], row["fire_id"], row["wind_id"])
            plan = "compact" if folder == "compact" else row["plan_id"]
            require(
                plan in LAYOUTS and plan not in pairs[key], f"Duplicate or unknown layout: {name}"
            )
            if plan == "multipoint-wide":
                require(end["core_ignited_count"] == 0, name)
            pairs[key][plan] = {
                "heritage": int(end["core_ignited_count"] > 0),
                "hour": int(hour["core_ignited_count"] > 0),
                "burned": end.get("ordinary_roof_burned_out_m2", end["roof_burned_out_m2"]),
                "source": (
                    row["footprint_sha256"],
                    hour["source_area_m2"],
                    hour["source_energy_J"],
                ),
            }
    roofs = defaultdict(set)
    for (village, stratum, fire, wind), layouts in pairs.items():
        require(set(layouts) == set(LAYOUTS), "Incomplete paired comparison")
        sources = [v["source"] for v in layouts.values()]
        for footprint, area, energy in sources:
            require(
                footprint == sources[0][0]
                and math.isclose(area, sources[0][1], rel_tol=1e-9, abs_tol=1e-6)
                and math.isclose(energy, sources[0][2], rel_tol=1e-9),
                "Source differs by layout",
            )
        roofs[village, stratum, fire].add(wind)
    require(
        len(pairs) == 927
        and len(roofs) == 103
        and all(len(winds) == 9 for winds in roofs.values()),
        "Unexpected study size",
    )
    return pairs


def construction():
    costs = {}
    pools = {}
    results = PAPER / "data/results"
    for row in read_rows(results / "standard/by-stratum.csv"):
        village, plan = row["village"], row["plan_id"]
        pools[village, row["stratum"]] = int(row["remaining_eligible_sites"])
        costs[village, plan] = tuple(
            float(row[f]) for f in ("roof_cleared_m2", "ground_cleared_m2", "construction_m2")
        )
    for row in read_rows(results / "compact/paired-cases.csv"):
        costs[row["village"], "compact"] = tuple(
            float(row[f])
            for f in (
                "compact_roof_cleared_m2",
                "compact_ground_cleared_footprint_m2",
                "compact_construction_m2",
            )
        )
    arms = {"uniform": "uniform-mid", "aligned": "open-ground-mid", "wide": "multipoint-wide"}
    for row in read_rows(results / "midbudget/路线清理量.csv"):
        costs[row["village"], arms[row["arm"]]] = tuple(
            float(row[f]) for f in ("roof_cleared_m2", "ground_cleared_m2", "construction_m2")
        )
    return costs, pools


def check_numbers(observed, displayed):
    require(len(observed) == len(displayed), "Unexpected number of columns")
    for value, cell in zip(observed, displayed, strict=True):
        number = float(cell.replace(",", "").replace("\N{MINUS SIGN}", "-"))
        require(
            math.isclose(value, number, rel_tol=0, abs_tol=0.005001),
            f"Rebuilt value {value:g} differs from {cell}",
        )


def check_main_tables(pairs, audit, tables):
    costs, pools = construction()
    first, second, third = tables
    reached = {plan: sum(r[plan]["heritage"] for r in pairs.values()) for plan in LAYOUTS}
    expected = {**audit["affected_village_heritage_cases"]}
    expected["open-ground-mid"] = expected.pop("aligned-mid")
    require(reached == expected, "Terminal heritage counts differ")
    for i, (village, stratum) in enumerate(STRATA):
        subset = [(k[2], obs) for k, obs in pairs.items() if k[:2] == (village, stratum)]
        for j, plan in enumerate(LAYOUTS):
            row = first[1 + (i // 2) * len(LAYOUTS) + j]
            count = sum(obs[plan]["heritage"] for _, obs in subset)
            require(row[6 + i % 2] == f"{count}/{len(subset)}", "Table 1 ignition counts differ")
            check_numbers(costs[village, plan], row[3:6])
        for j, plan in enumerate(LAYOUTS[4:]):
            group = [obs[plan] for _, obs in subset]
            burned = [o["burned"] for o in group]
            loss = [area + costs[village, plan][0] for area in burned]
            check_numbers(
                [
                    sum(o["heritage"] for o in group),
                    sum(o["heritage"] - o["hour"] for o in group),
                    statistics.fmean(burned),
                    statistics.fmean(loss),
                    max(loss),
                ],
                second[1 + i * 4 + j][3:],
            )
        for j, (plan, reference) in enumerate(COMPARISONS):
            grouped = defaultdict(list)
            burned, lost = [], []
            for fire, cells in subset:
                delta = cells[plan]["burned"] - cells[reference]["burned"]
                total = (cells[plan]["burned"] + costs[village, plan][0]) - (
                    cells[reference]["burned"] + costs[village, reference][0]
                )
                burned.append(delta)
                lost.append(total)
                grouped[fire].append(total)
            means = [statistics.fmean(values) for values in grouped.values()]
            se = statistics.stdev(means) * math.sqrt(
                (1 - len(means) / pools[village, stratum]) / len(means)
            )
            check_numbers(
                [
                    statistics.fmean(burned),
                    statistics.fmean(lost),
                    se,
                    sum(v < -1e-5 for v in lost),
                    sum(abs(v) <= 1e-5 for v in lost),
                    sum(v > 1e-5 for v in lost),
                ],
                third[1 + i * 3 + j][2:],
            )
    return reached


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write verified study totals to this JSON file")
    args = parser.parse_args()
    require(digest(ARCHIVE.read_bytes()) == ARCHIVE_SHA, "Unexpected data archive")
    tables = verify_word()
    with ZipFile(ARCHIVE) as archive:
        audit = verify_publication(archive)
        pairs = primary_cases(archive, audit)
        counts = check_main_tables(pairs, audit, tables)
    report = {
        "paired_scenarios": len(pairs),
        "primary_terminal_records": sum(map(len, pairs.values())),
        "heritage_ignition_cases": counts,
    }
    result = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    print(result, end="")


if __name__ == "__main__":
    main()
