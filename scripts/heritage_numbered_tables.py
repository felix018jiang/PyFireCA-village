"""Generate editable English tables from the 16 archived paper CSV blocks."""

# ruff: noqa: RUF001 -- Keep the punctuation and scientific symbols of the source CSVs.

import csv
import re
from pathlib import Path

TABLE_ROOT = Path(__file__).resolve().parents[1] / "paper/tables"

# Each English block retains the grid and row order of its source CSV. Multiple
# blocks can share a caption in Word. The relative widths allow English headings
# and eight-digit area measurements to fit at the original text width.
BLOCKS = {
    "01a": (
        "补充表S1a",
        (0.78, 1.65, 1.05, 1.42, 1.52),
        (
            "Village",
            "5 m grid\n(rows × columns)",
            "Valid cells",
            "Valid domain\n(km²)",
            "Ordinary / heritage\nroofs",
        ),
    ),
    "01b": (
        "补充表S1b",
        (3.45, 1.02, 1.04, 0.91),
        ("Parameter (unit)", "Open-ground fuel", "Woody surface fuel", "Equivalent roof"),
    ),
    "02": (
        "补充表S7",
        (0.66, 1.73, 0.81, 1.06, 2.16),
        (
            "Village",
            "Construction type",
            "Roof labels",
            "Baseline roof cells",
            "Basis for classification",
        ),
    ),
    "03a": (
        "补充表S2a",
        (0.62, 0.7, 0.55, 0.91, 1.17, 1.17, 1.3),
        (
            "Village",
            "Site",
            "Roof labels",
            "Nominal area",
            "Union of selected outlines",
            "Area actually cleared",
            "Area shared with retained roofs",
        ),
    ),
    "03b": (
        "补充表S2b",
        (0.66, 1.36, 1.05, 1.69, 1.66),
        (
            "Village",
            "Local layout",
            "Fixed fire scenarios",
            "1 m grid\n20 / 40 / 60 min",
            "0.5 m grid\n20 / 40 / 60 min",
        ),
    ),
    "04a": (
        "补充表S3a",
        (1.1, 0.92, 1.02, 0.98, 1.15, 1.25),
        (
            "Finalized layout",
            "Roof labels cleared",
            "Roof area\n(m²)",
            "Ground area\n(m²)",
            "Total cleared\n(m²)",
            "Design tests passed",
        ),
    ),
    "04b": (
        "补充表S3b",
        (0.67, 1.1, 0.59, 1.54, 1.56, 0.96),
        (
            "Village",
            "Layout",
            "Runs",
            "Design sources\n1 h → extinction",
            "Newly sampled roofs\n1 h → extinction",
            "Late first ignition",
        ),
    ),
    "04c": (
        "补充表S3c",
        (1.16, 1.24, 1.4, 1.24, 1.38),
        (
            "Layout",
            "Pingyan\nChanged ignition count (pairs / 4)",
            "Pingyan\nMax. absolute roof area difference (m²)",
            "Batuan\nChanged ignition count (pairs / 4)",
            "Batuan\nMax. absolute roof area difference (m²)",
        ),
    ),
    "05a": (
        "补充表S4a",
        (0.64, 1.01, 0.40, 0.49, 1.05, 0.67, 1.02, 1.14),
        (
            "Stratum",
            "Layout",
            "Runs",
            "Active at 1 h",
            "Scenarios with ignition\n1 h → extinction",
            "Structures ignited by end",
            "Mean roof area (m²)",
            "Mean ground area (m²)",
        ),
    ),
    "05b": (
        "补充表S4b",
        (0.62, 1.02, 0.83, 0.84, 1.08, 0.96, 1.07),
        (
            "Village",
            "Layout",
            "Pairs with ignition by 1 h",
            "Pairs with ignition by end",
            "Roofs with ignition in any wind",
            "Mean final roof area (m²)",
            "Mean final ground area (m²)",
        ),
    ),
    "06a": (
        "补充表S6a",
        (2.14, 1.09, 1.09, 0.98, 1.12),
        (
            "Simulation set",
            "Scenarios × layouts",
            "Terminal records",
            "Full domain",
            "Certified subdomain",
        ),
    ),
    "06b": (
        "补充表S6b",
        (0.69, 1.08, 0.96, 1.13, 1.19, 1.37),
        (
            "Village",
            "Layout",
            "Certified extinction (s)",
            "Max. roof area difference (m²)",
            "Max. ground area difference (m²)",
            "Max. difference in structures ignited",
        ),
    ),
    "07": (
        "表1",
        (0.64, 1.15, 0.63, 0.89, 0.85, 0.94, 0.66, 0.66),
        (
            "Village",
            "Layout",
            "Band width (m)",
            "Ordinary roofs (m²)",
            "Burnable ground (m²)",
            "Total cleared (m²)",
            "Near-source cases",
            "Far-source cases",
        ),
    ),
    "08": (
        "表2",
        (0.66, 0.42, 1.11, 0.59, 0.53, 0.99, 1.07, 1.05),
        (
            "Stratum",
            "Pairs",
            "Layout",
            "Ignition cases",
            "Late cases",
            "Mean area burned out (m²)",
            "Mean total loss (m²)",
            "Max. total loss (m²)",
        ),
    ),
    "09": (
        "表3",
        (0.65, 1.21, 1.04, 1.11, 0.77, 0.55, 0.52, 0.57),
        (
            "Stratum",
            "Difference",
            "Mean difference in area burned out (m²)",
            "Mean difference in total loss (m²)",
            "Standard error (m²)",
            "Lower (pairs)",
            "Equal (pairs)",
            "Higher (pairs)",
        ),
    ),
    "10": (
        "补充表S5",
        (2.59, 0.96, 0.96, 0.96, 0.95),
        ("Measure", "Pingyan\nnear", "Pingyan\nfar", "Batuan\nnear", "Batuan\nfar"),
    ),
}

TERMS = {
    "平岩": "Pingyan",
    "岜团": "Batuan",
    "平岩近源": "Pingyan near",
    "平岩远源": "Pingyan far",
    "岜团近源": "Batuan near",
    "岜团远源": "Batuan far",
    "无清理": "No clearance",
    "遗产窄围护": "Heritage, narrow",
    "遗产宽围护": "Heritage, wide",
    "多点窄围护": "Multipoint, narrow",
    "多点宽围护": "Multipoint, wide",
    "精简布局": "Compact",
    "多点宽": "Multipoint, wide",
    "精简": "Compact",
    "均匀收窄": "Uniform narrowing",
    "沿不燃地表改线": "Ground-aligned route",
    "清单": "Itemized",
    "局部变化": "Variable",
    "既有": "Original",
    "换位": "Relocated",
    "既有选址": "Original site",
    "换位选址": "Relocated site",
    "平岩精简": "Pingyan compact",
    "岜团精简": "Batuan compact",
    "均匀线 − 宽围护": "Uniform − wide",
    "改线 − 宽围护": "Rerouted − wide",
    "改线 − 均匀线": "Rerouted − uniform",
    "普通建筑混合构造": "Mixed construction with timber components",
    "瓦覆木构鼓楼": "Tile-roofed timber drum tower",
    "瓦覆木廊桥": "Tile-roofed timber gallery bridge",
    "瓦面木构民居": "Tile-roofed timber dwelling",
    "岜团田野类型归纳": "Field classification in Batuan",
    "平岩民居参考照片": "Reference photographs of Pingyan dwellings",
    "参照岜团同类构造": "Comparable construction in Batuan",
    "引燃阈值 (K)": "Ignition temperature (K)",
    "单位面积干燃料负荷 (kg m⁻²)": "Dry fuel load per unit area (kg m⁻²)",
    "可热解干燃料质量份额": "Pyrolysable mass fraction of dry fuel",
    "干燃料热值 (MJ kg⁻¹)": "Heat of combustion of dry fuel (MJ kg⁻¹)",
    "单位面积可释放能量 (MJ m⁻²)": "Available energy per unit area (MJ m⁻²)",
    "含水率（干基）": "Moisture content (dry basis)",
    "标称峰值热释放率／有效燃烧面 (kW m⁻²)": (
        "Nominal peak heat release rate per unit effective burning area (kW m⁻²)"
    ),
    "有效燃烧面积系数": "Effective burning area factor",
    "峰值热释放率／物理可燃面 (kW m⁻²)": (
        "Peak heat release rate per unit physical combustible area (kW m⁻²)"
    ),
    "有效发射源高度 (m)": "Effective emitter height (m)",
    "有效接收高度 (m)": "Effective receiver height (m)",
    "辐射份额": "Radiative fraction",
    "表面吸收率": "Surface absorptivity",
    "长波发射率": "Longwave emissivity",
    "等效干热质量 (kg m⁻²)": "Effective dry thermal mass (kg m⁻²)",
    "干燃料比热 (J kg⁻¹ K⁻¹)": "Specific heat of dry fuel (J kg⁻¹ K⁻¹)",
    "环境对流换热系数 (W m⁻² K⁻¹)": "Ambient convective heat transfer coefficient (W m⁻² K⁻¹)",
    "背衬传热系数 (W m⁻² K⁻¹)": "Backing heat transfer coefficient (W m⁻² K⁻¹)",
    "热释放率增长时长 (s)": "Heat release rate growth duration (s)",
    "热释放率恒定阶段时长 (s)": "Heat release rate plateau duration (s)",
    "热释放率衰减时长 (s)": "Heat release rate decay duration (s)",
    "五套标准布局": "Five standard layouts",
    "两条中档路线": "Two mid-budget routes",
    "主面板合计": "Total across the main experiment",
    "中档对照复用的宽围护": "Wide firebreak reused in the mid-budget comparison",
    "火情组数": "Matched fire scenarios",
    "精简布局迟发首燃（场）": "Compact layout, late first heritage ignition (scenarios)",
    "多点宽 · 普通屋面最大烧尽 (m²)": "Wide multipoint, maximum ordinary roof area burned out (m²)",
    "多点宽 · 平均烧尽屋面标签数": "Wide multipoint, mean number of roof labels burned out",
    "多点宽 · 地表平均烧尽 (m²)": "Wide multipoint, mean ground area burned out (m²)",
    "多点宽 · 触及原域边界（场）": "Wide multipoint, original domain boundary reached (scenarios)",
    "精简 · 普通屋面最大烧尽 (m²)": "Compact, maximum ordinary roof area burned out (m²)",
    "精简 · 平均烧尽屋面标签数": "Compact, mean number of roof labels burned out",
    "精简 · 地表平均烧尽 (m²)": "Compact, mean ground area burned out (m²)",
    "精简 · 触及原域边界（场）": "Compact, original domain boundary reached (scenarios)",
    "烧尽均差 (m²)": "Mean difference in area burned out (m²)",
    "烧尽均差的标准误 (m²)": "Standard error of mean difference in area burned out (m²)",
    "烧尽增加（组）": "Area burned out increased (pairs)",
    "烧尽减少（组）": "Area burned out decreased (pairs)",
    "总损失均差 (m²)": "Mean difference in total loss (m²)",
    "总损失均差的标准误 (m²)": "Standard error of mean difference in total loss (m²)",
    "总损失增加（组）": "Total loss increased (pairs)",
    "总损失减少（组）": "Total loss decreased (pairs)",
}


def read_block(key):
    source, widths, headers = BLOCKS[key]
    (path,) = TABLE_ROOT.glob(f"{source}_*.csv")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        source_rows = list(csv.reader(stream))
    if any(len(row) != len(headers) for row in source_rows) or len(widths) != len(headers):
        raise ValueError(f"Source columns differ for table block {key}")
    translated = [headers]
    for row in source_rows[1:]:
        translated.append(
            [TERMS[value] if re.search(r"[\u3400-\u9fff]", value) else value for value in row]
        )
    return source_rows, translated
