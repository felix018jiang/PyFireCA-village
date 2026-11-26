"""Render the actual intervention geometry and separately scoped evidence panels."""

# ruff: noqa: RUF001 -- Chinese figure labels use Chinese punctuation.

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
INK, MUTED = "#183342", "#5c6b72"
GREEN, RED, ORANGE = "#18745e", "#b45138", "#b77922"


def render(output):
    output = Path(output)
    sources = {
        "inventory": ROOT / "runs/research/heritage-exact-small-20261024/inventory.json",
        "confirmation": ROOT / "docs/results/heritage-triple-validation-20261024.json",
        "B0063": ROOT / "docs/results/heritage-B0063-ablation-20261024.json",
        "positions": ROOT / "docs/results/heritage-position-controls-20261024.json",
        "exact": ROOT / "docs/results/heritage-exact-small-20261024.json",
    }
    data = {name: json.loads(path.read_text()) for name, path in sources.items()}
    assert all(data[name]["complete"] for name in ("confirmation", "B0063", "positions", "exact"))
    geometries = {
        b["building_id"]: unary_union([Polygon(p["exterior"], p["holes"]) for p in b["polygons"]])
        for b in data["inventory"]["buildings"]
    }
    image = Image.new("RGB", (2400, 1700), "#ffffff")
    draw = ImageDraw.Draw(image)

    def font(size):
        return ImageFont.truetype("/System/Library/Fonts/STHeiti Medium.ttc", size)

    def text(x, y, value, size=26, fill=INK):
        draw.text((x, y), value, font=font(size), fill=fill)

    text(45, 28, "当前村内防火线：三处建筑干预与双核心保护", 46)
    text(
        45,
        91,
        "岜团村 · 2026-10-24  |  25 m²屋面点火 · 1200 s · 无风 · 1 m / 0.5 m双尺度",
        26,
        MUTED,
    )
    text(
        45,
        137,
        "拆除 B0061＋B0071＋B0076  |  名义面积451.24 m²  |  "
        "实际清空443.84 m²  |  保留共享屋面7.40 m²",
        28,
    )
    selected, cores = {"B0061", "B0071", "B0076"}, {"B0073", "B0120"}

    def map_panel(rect, window, title, *, overview=False):
        left, top, right, bottom = rect
        text(left, top - 42, title, 30)
        draw.rectangle(rect, fill="#f5f7f7", outline="#d5dfe1", width=2)
        xmin, ymin, xmax, ymax = window.bounds
        scale = min((right - left - 35) / (xmax - xmin), (bottom - top - 35) / (ymax - ymin))
        ox = left + ((right - left) - (xmax - xmin) * scale) / 2
        oy = top + ((bottom - top) - (ymax - ymin) * scale) / 2

        def xy(x, y):
            return ox + (x - xmin) * scale, oy + (ymax - y) * scale

        for identifier, geometry in geometries.items():
            clipped = geometry.intersection(window)
            parts = [clipped] if clipped.geom_type == "Polygon" else getattr(clipped, "geoms", ())
            fill = (
                "#efb8a7"
                if identifier in selected
                else "#8ac6b1"
                if identifier in cores
                else "#d8dfe1"
            )
            edge = RED if identifier in selected else GREEN if identifier in cores else "#9caeb5"
            for p in parts:
                if p.geom_type != "Polygon" or p.is_empty:
                    continue
                draw.polygon(
                    [xy(x, y) for x, y in p.exterior.coords],
                    fill=fill,
                    outline=edge,
                    width=3 if identifier in selected | cores else 1,
                )
                for ring in p.interiors:
                    draw.polygon([xy(x, y) for x, y in ring.coords], fill="#f5f7f7")
        labels = {
            "B0073": (14, -48, "鼓楼 B0073"),
            "B0120": (10, 30, "风雨桥 B0120"),
            "B0071": (-140, -34, "拆 B0071"),
            "B0076": (18, 10, "拆 B0076"),
            "B0061": (20, -55, "拆 B0061"),
            "B0075": (-135, 14, "火源 B0075"),
            "B0062": (14, -40, "火源 B0062"),
            "B0063": (-155, 35, "补充源 B0063"),
        }
        if overview:
            labels = {i: (8, 0, i) for i in cores | selected}
        for identifier, (dx, dy, label) in labels.items():
            p = geometries[identifier].representative_point()
            if not window.covers(p):
                continue
            u, v = xy(p.x, p.y)
            if identifier not in cores | selected:
                draw.ellipse((u - 5, v - 5, u + 5, v + 5), fill="white", outline=ORANGE, width=3)
            if not overview or identifier in cores:
                draw.line((u, v, u + dx, v + dy + 15), fill=INK, width=1)
                draw.text(
                    (u + dx, v + dy),
                    label,
                    font=font(23 if not overview else 21),
                    fill=INK,
                    stroke_width=3,
                    stroke_fill="white",
                )
        metres = 100 if overview else 10
        u, v = left + 25, bottom - 32
        draw.line((u, v, u + metres * scale, v), fill=INK, width=5)
        text(u, v - 32, f"{metres} m", 22)
        text(right - 45, top + 12, "N", 23)
        draw.line((right - 35, top + 65, right - 35, top + 43), fill=INK, width=3)
        draw.polygon(
            [(right - 35, top + 35), (right - 41, top + 47), (right - 29, top + 47)], fill=INK
        )

    bounds = unary_union(list(geometries.values())).bounds
    map_panel(
        (45, 255, 650, 875),
        box(bounds[0] - 10, bounds[1] - 10, bounds[2] + 10, bounds[3] + 10),
        "A  全村建筑与干预位置",
        overview=True,
    )
    map_panel(
        (700, 255, 1500, 875), box(347230, 2869735, 347307, 2869810), "B  鼓楼侧：B0071＋B0076"
    )
    map_panel((1550, 255, 2355, 875), box(347267, 2869670, 347358, 2869756), "C  桥头侧：B0061")
    text(
        45,
        902,
        "图例：绿色＝核心遗产；红色＝拟拆除建筑；橙色空心点＝代表性起火建筑。"
        "实际建筑轮廓／UTM 49N。",
        25,
        MUTED,
    )
    text(
        45,
        946,
        "这些是经模拟验证的建筑分隔位置；图中未假定三栋拆除后构成连续、等宽的实体隔离带。",
        24,
        MUTED,
    )

    text(45, 1022, "D  新随机起火独立确认", 32)
    text(45, 1068, "每层6次抽样；数字为双尺度联合失效情景数", 23, MUTED)
    cols = ["鼓楼近邻", "桥头近邻", "背景层"]
    for j, label in enumerate(cols):
        text(285 + j * 115, 1120, label, 23)
    plan_names = ["无拆除", "仅拆B0061", "原三栋"]
    strata = ["near-B0073", "near-B0120", "background"]
    matrix = []
    for i, (plan, label) in enumerate(zip(data["confirmation"]["plans"], plan_names, strict=True)):
        text(45, 1175 + i * 65, label, 25)
        counts = {s["stratum"]: s["joint_failures"] for s in plan["strata"]}
        matrix.append([counts[s] for s in strata])
        for j, key in enumerate(strata):
            n = counts[key]
            x, y = 280 + j * 115, 1166 + i * 65
            draw.rounded_rectangle(
                (x, y, x + 100, y + 52),
                radius=8,
                fill="#e0f0e9" if n == 0 else "#f0c4ae" if n < 6 else "#bb654b",
            )
            text(x + 25, y + 10, f"{n}/6", 27, "white" if n == 6 else INK)
    text(45, 1390, "原三栋：18/18次抽样保护两核心", 27, GREEN)
    text(45, 1435, "另6个B0063新落点：风雨桥6/6→0/6", 23, MUTED)
    assert matrix == [[2, 6, 0], [2, 0, 0], [0, 0, 0]]

    text(760, 1022, "E  等栋数／近等面积位置对照", 32)
    text(760, 1068, "六个比较情景；四个新落点＋两个旧不利点", 23, MUTED)
    alt_names = ["无拆除", "原三栋", "桥头替换", "鼓楼侧近等面积", "全局近等面积"]
    failures = []
    for i, (plan, label) in enumerate(zip(data["positions"]["plans"], alt_names, strict=True)):
        y = 1130 + i * 62
        text(760, y, label, 24)
        x = 1030
        draw.rectangle((x, y + 3, x + 255, y + 33), fill="#eef1f2")
        n = plan["joint_failures"]
        failures.append(n)
        if n:
            draw.rectangle((x, y + 3, x + n / 6 * 255, y + 33), fill="#bc7757")
        else:
            draw.line((x, y + 3, x, y + 33), fill=GREEN, width=5)
        text(1305, y, f"{n}/6", 26, GREEN if n == 0 else INK)
    text(760, 1470, "结果用于位置比较，不估计全村失效概率。", 23, MUTED)
    assert failures == [4, 0, 1, 2, 3]

    text(1550, 1022, "F  小集合精确最优性验证", 32)
    text(1550, 1068, "只在B0061／B0071／B0076三个候选内搜索", 23, MUTED)
    table = data["exact"]["subset_table"]
    for k in range(4):
        rows = [r for r in table if len(r["demolished_ids"]) == k]
        passed = sum(r["status"] == "passed" for r in rows)
        text(1550, 1130 + k * 65, f"拆{k}栋  ·  {len(rows)}个组合", 27)
        text(
            1880,
            1130 + k * 65,
            "全部失效" if not passed else "通过全部情景",
            27,
            RED if not passed else GREEN,
        )
    text(1550, 1435, "8个子集完整判定；21次CA完成认证。", 25)
    text(1550, 1480, "认证范围：三候选、三设计情景。", 24, MUTED)

    draw.line((45, 1570, 2355, 1570), fill="#d7e0e1", width=2)
    text(
        45,
        1602,
        "当前结论：原三栋保护效果稳定，三个直接减拆方案均有反例；全331栋最优性仍需扩大搜索。",
        27,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    image.save(output.with_suffix(".pdf"), resolution=150)
    evidence = {
        "sources": {
            k: {
                "path": str(v.relative_to(ROOT)),
                "sha256": hashlib.sha256(v.read_bytes()).hexdigest(),
            }
            for k, v in sources.items()
        },
        "independent_confirmation_matrix": matrix,
        "position_comparison_failures": failures,
        "projection": "EPSG:32649",
        "counts_are_failure_scenarios_not_annual_risk": True,
    }
    output.with_suffix(".json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    render(args.output)
