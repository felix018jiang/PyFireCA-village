"""English editions of the manuscript's ReportLab site, model and summary panels."""

import csv
import io
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import fmean

import pypdfium2 as pdfium
from reportlab.lib.colors import HexColor
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[1]
SUPPLEMENT = ROOT / "paper/data/supplement"
WIND = ROOT / "output/heritage-wind-20261112"
INK, GREY, GRID = "#25373e", "#556870", "#d5dfe1"
TEAL, RUST = "#176f75", "#a64d36"


def records(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def label(c, x, y, value, size=10, color=INK, align="left", bold=False):
    c.setFillColor(HexColor(color))
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    draw = {"left": c.drawString, "right": c.drawRightString, "center": c.drawCentredString}
    draw[align](x, y, value)


def line(c, x, y, end_x, end_y, color=GRID, width=0.7):
    c.setStrokeColor(HexColor(color))
    c.setLineWidth(width)
    c.line(x, y, end_x, end_y)


def export_panel(path, size, draw):
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=size, invariant=True)
    c.setFillColorRGB(1, 1, 1)
    c.rect(0, 0, *size, fill=1, stroke=0)
    draw(c)
    c.save()
    pdf = pdfium.PdfDocument(buf.getvalue())
    page = pdf[0]
    bitmap = page.render(scale=4)
    try:
        bitmap.to_pil().save(path, dpi=(288, 288))
    finally:
        bitmap.close()
        page.close()
        pdf.close()


def site_map(c, village, x0, letter):
    features = json.loads((WIND / f"{village}-layouts.geojson").read_bytes())["features"]
    inventory = [f for f in features if f["properties"]["layer"] in ("building", "heritage")]
    # Keep the published overview's single-polygon drawing rule. The caption
    # counts roof IDs in the full inventory, including the multipart roofs.
    polys = [
        (f["properties"]["layer"], f["geometry"]["coordinates"])
        for f in inventory
        if f["geometry"]["type"] == "Polygon"
    ]
    xs, ys = zip(*(p for _, geom in polys for p in geom[0]), strict=True)
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    scale = min(240 / (xmax - xmin), 153 / (ymax - ymin))
    ox = x0 + (240 - (xmax - xmin) * scale) / 2
    oy = 390 + (153 - (ymax - ymin) * scale) / 2
    for layer, rings in sorted(polys, key=lambda item: item[0] == "heritage"):
        path = c.beginPath()
        for ring in rings:
            for i, (x, y) in enumerate(ring):
                (path.moveTo if i == 0 else path.lineTo)(
                    ox + (x - xmin) * scale, oy + (y - ymin) * scale
                )
            path.close()
        heritage = layer == "heritage"
        c.setFillColor(HexColor(RUST if heritage else "#a7b6bc"))
        c.setStrokeColor(HexColor(RUST if heritage else "#839399"))
        c.setLineWidth(1.1 if heritage else 0.12)
        c.drawPath(path, fill=1, stroke=1)
    n = sum(f["properties"]["layer"] == "heritage" for f in inventory)
    label(c, x0, 567, f"{letter}   {village.title()}", 15, bold=True)
    label(c, x0, 550, f"{len(inventory) - n} ordinary roofs  ·  {n} heritage structures", 10, GREY)
    line(c, x0, 368, x0 + 200 * scale, 368, INK, 1.6)
    for x in (x0, x0 + 200 * scale):
        line(c, x, 365, x, 371, INK, 1)
    label(c, x0, 352, "200 m", 9)
    x = x0 + 234
    line(c, x, 396, x, 415, INK, 1.3)
    for dx in (-2.5, 2.5):
        line(c, x, 415, x + dx, 411, INK, 1.2)
    label(c, x, 419, "N", 9, align="center")


def baseline_areas():
    cases = [r for r in records(SUPPLEMENT / "01_cases.csv") if r["plan_id"] == "baseline"]
    weights = {
        r["village"]: r
        for r in records(SUPPLEMENT / "07_village_weighted.csv")
        if r["plan_id"] == "baseline"
    }
    by_source = defaultdict(list)
    for row in cases:
        by_source[row["village"], row["stratum"], row["fire_id"]].append(row)
    if len(by_source) != 103 or any(
        len({r["wind_id"] for r in group}) != 9 or len(group) != 9 for group in by_source.values()
    ):
        raise ValueError(
            "The baseline figure requires the 103 original roofs and nine winds per roof"
        )
    for village in ("pingyan", "batuan"):
        reg = weights[village]
        for surface, name, field in (
            ("All roofs", "roof", "all_roof_burned"),
            ("Burnable ground", "ground", "ground_burned"),
        ):
            values = []
            for time in ("at_60min", "final"):
                key = f"{time}_{field}_m2"
                area = sum(
                    float(reg[f"{s}_weight"])
                    * fmean(
                        fmean(float(r[key]) for r in group)
                        for (v, stratum, _), group in by_source.items()
                        if (v, stratum) == (village, s)
                    )
                    for s in ("near", "far")
                )
                if not math.isclose(area, float(reg[f"mean_{time}_{name}_m2"]), rel_tol=1e-12):
                    raise ValueError(
                        f"Baseline area differs from the archived result: {village} {key}"
                    )
                values.append(area / 10_000)
            yield village.title(), surface, *values


def model_and_sites(c):
    site_map(c, "pingyan", 15, "a")
    site_map(c, "batuan", 290, "b")
    line(c, 276, 365, 276, 574)
    line(c, 8, 342, 552, 342)
    label(c, 12, 323, "c   State transitions in a combustible cell", 14, bold=True)
    label(
        c,
        12,
        306,
        "Roofs, open-ground vegetation and woody vegetation share these rules and use "
        "material-specific parameters.",
        10,
        GREY,
    )
    xs = (14, 156, 299, 443)
    for i, (x, state) in enumerate(
        zip(xs, ("Unburned", "Heating", "Burning", "Burned out"), strict=True)
    ):
        c.setFillColor(HexColor(("#edf5f4", "#edf5f4", "#f8ebe6", "#e9eded")[i]))
        c.setStrokeColor(HexColor("#c58b77" if i == 2 else "#aebfc1"))
        c.setLineWidth(0.6)
        c.roundRect(x, 268, 100, 27, 3, fill=1, stroke=1)
        label(c, x + 50, 277, state, 12, RUST if i == 2 else INK, "center")
        if i < 3:
            start, end, y = x + 104, xs[i + 1] - 5, 281
            line(c, start, y, end, y, GREY, 0.9)
            for dy in (-2.5, 2.5):
                line(c, end - 4, y + dy, end, y, GREY, 0.9)
                if i == 0:
                    line(c, start + 4, y + dy, start, y, GREY, 0.9)
    label(
        c,
        12,
        251,
        "Radiation transfers heat between patches. Flame contact also transfers heat across a "
        "shared edge when",
        10.5,
    )
    label(
        c,
        12,
        237,
        "the receiver lies within the vertical reach of the source flame. A cell that meets the "
        "ignition criteria begins to burn",
        10.5,
    )
    label(
        c,
        12,
        223,
        "at its material-specific ignition temperature and burns out when its available "
        "energy is exhausted.",
        10.5,
    )
    line(c, 8, 210, 552, 210)
    label(c, 12, 190, "d   Mean area burned out without clearance", 14, bold=True)
    label(
        c,
        12,
        172,
        "Village means are weighted by the number of eligible ignition-source roofs. Areas are "
        "in hectares (ha).",
        10,
        GREY,
    )

    def axis_x(value):
        return 151 + math.log10(value / 0.05) / math.log10(100 / 0.05) * 241

    def marker(x, y, final):
        c.setLineWidth(1.3)
        c.setStrokeColor(HexColor(RUST if final else TEAL))
        c.setFillColor(HexColor(RUST if final else "#ffffff"))
        if final:
            c.rect(x - 3, y - 3, 6, 6, fill=1, stroke=1)
        else:
            c.circle(x, y, 3, fill=1, stroke=1)

    for tick in (0.1, 1, 10, 100):
        x = axis_x(tick)
        line(c, x, 35, x, 143)
        label(c, x, 17, f"{tick:g}", 10, GREY, "center")
    label(c, 12, 151, "Village and surface", 10, GREY)
    label(c, 269, 151, "Area burned out (log scale)", 10, GREY, "center")
    marker(418, 154, False)
    label(c, 427, 150, "1 h", 10, TEAL)
    marker(475, 154, True)
    label(c, 484, 150, "Extinction", 10, RUST)
    for i, (village, surface, at_hour, final) in enumerate(baseline_areas()):
        y = 129 - 26 * i
        label(c, 12, y - 3, f"{village} · {surface}", 10.5)
        line(c, axis_x(at_hour), y, axis_x(final), y, "#8ea5a6", 1.2)
        marker(axis_x(at_hour), y, False)
        marker(axis_x(final), y, True)
        label(c, 449, y - 3, f"{at_hour:.2f}", 11, align="right")
        label(c, 534, y - 3, f"{final:.2f}", 11, align="right")


def extinction_replays(c):
    label(c, 8, 157, "Time to extinction in full-domain reference runs (h)", 14, bold=True)
    data = ROOT / "output/heritage-geometry-validation-20261118"
    for i, (village, suffix) in enumerate((("Pingyan", ""), ("Batuan", "-batuan"))):
        replay = json.loads(
            (data / f"paired-late-ignition-reference-check{suffix}.json").read_bytes()
        )
        times = {
            r["case_id"].endswith("_multipoint-wide"): r["observation_at_extinction"][
                "elapsed_simulated_s"
            ]
            for r in replay["cases"]
        }
        for j, (wide, name) in enumerate(((True, "Multipoint, wide"), (False, "Compact"))):
            hours = times[wide] / 3600
            y = 126 - 25 * (2 * i + j)
            label(c, 8, y - 4, f"{village} · {name}", 10.5)
            line(c, 148, y, 476, y)
            c.setFillColor(HexColor(TEAL if wide else RUST))
            c.rect(148, y - 3, 328 * hours / 12, 6, fill=1, stroke=0)
            label(c, 540, y - 4, f"{hours:.2f} h", 11, align="right")
    for k in (0, 3, 6, 9, 12):
        label(c, 148 + 328 * k / 12, 19, str(k), 10, GREY, "center")


def structure_types(c):
    data = records(SUPPLEMENT / "06_heritage_strata.csv")
    types = (
        ("Drum towers (2)", {"B0166", "B0073"}),
        ("Wind-and-rain bridges (3)", {"B0035", "B0036", "B0120"}),
    )
    label(c, 8, 140, "Heritage structures ignited under the compact layout", 14, bold=True)
    for i, (title, keys) in enumerate(types):
        group = [r for r in data if r["heritage_id"] in keys]
        n = sum(int(r["compact_ignited_cases"]) for r in group)
        total = sum(int(r["pairs"]) for r in group)
        percent = 100 * n / total
        y = 105 - i * 38
        label(c, 8, y - 4, title, 11)
        for color, width in (("#e5ebeb", 241), (TEAL, 241 * percent / 20)):
            c.setFillColor(HexColor(color))
            c.rect(155, y - 7, width, 14, fill=1, stroke=0)
        label(c, 540, y - 4, f"{n:,} / {total:,} ({percent:.1f}%)", 11, align="right")
    for k in range(5):
        label(c, 155 + k * 241 / 4, 37, f"{5 * k}%", 9, GREY, "center")
    label(
        c,
        8,
        12,
        "The denominator counts each structure once per fire scenario in its village.",
        10,
        GREY,
    )
