"""Select two budget-matched routes per village using only mapped materials and geometry."""

import json
import math
import os
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np
import shapely as sh
from prepare_heritage_wind_strategies import feature, ground_action
from run_heritage_geometry_validation import DEST as COMPACT_ARCHIVE
from run_heritage_geometry_validation import PARENT, ROOT, WIDE, file_binding, read_json
from run_heritage_wind_strategies import digest
from shapely.affinity import translate
from shapely.geometry import shape
from shapely.ops import polygonize

from pyfireca.planning.heritage import content_hash
from pyfireca.village.domain import VillageGridSpec

DEST = ROOT / "output/heritage-midbudget-20261120"
UNIFORM, ALIGNED = "uniform-mid", "open-ground-mid"
SEED = 20261120
SEARCH_SIZE = 80_000
SHORTLIST = 80
TOL = 1e-6


def freeze(path, obj):
    """Exclusive publication; a repeat run may reproduce but never alter a frozen design."""
    data = (json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent) as temporary:
        temporary.write(data)
        temporary.flush()
        try:
            os.link(temporary.name, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError(f"Existing frozen file differs: {path}") from None


def bits(mask):
    return sum(1 << int(i) for i in np.flatnonzero(mask))


class Terrain:
    def __init__(self, setup, wide):
        path = (PARENT / setup["geojson_path"]).resolve()
        if digest(path) != setup["geojson_sha256"]:
            raise ValueError("Mapped buildings differ from the signed input")
        self.buildings = {
            f["properties"]["building_id"]: shape(f["geometry"])
            for f in read_json(path)["features"]
            if f["properties"]["layer"] in ("building", "heritage")
        }
        self.ids = sorted(wide["demolished_ids"])
        self.roofs = np.array([self.buildings[k] for k in self.ids])
        self.anchor_ids = sorted(wide["anchor_ids"])
        self.anchors = [self.buildings[k] for k in self.anchor_ids]
        self.inside = sh.union_all(self.anchors)
        self.envelope = self.inside.buffer(wide["width_m"], quad_segs=64)
        outside = [g for k, g in self.buildings.items() if k not in self.ids]
        self.retain = sh.union_all(outside)
        permanent_clear = sh.union_all(self.roofs).difference(self.retain)
        # A shared physical roof patch clears only when every tagged owner is selected.
        lines = sh.union_all([permanent_clear.boundary, *sh.boundary(self.roofs)])
        atoms = {}
        for p in polygonize(lines):
            point = p.representative_point()
            if permanent_clear.covers(point):
                owner = bits(sh.covers(self.roofs, point))
                if not owner:
                    raise ValueError("Unassigned roof atom")
                atoms[owner] = atoms.get(owner, 0.0) + p.area
        self.atom_costs = atoms
        self.roof_cost = lru_cache(maxsize=None)(self._roof_cost)
        self.grid = VillageGridSpec(**setup["grid"])
        self.cell_ids = np.array(sorted(wide["cleared_physical_cell_ids"]))
        rows, cols = np.divmod(self.cell_ids, self.grid.width)
        self.cells = self.boxes(rows, cols)
        study = Path(setup["study_path"])
        case = (study.parent / read_json(study)["case_dir"]).resolve()

        def arr(name):
            return np.load(case / f"{name}.npy", mmap_mode="r")

        self.cover = arr("landcover_index")
        base = arr("domain_mask") & arr("known_mask") & (arr("building_index") < 0)
        self.eligible = base & arr("burnable_mask") & (arr("covered_area_m2") > 0)
        open_mask = base & ~arr("burnable_mask") & (self.cover == 0)
        rr, cc = np.nonzero(open_mask)
        xx = self.grid.x_min_m + cc * self.grid.cell_size_m
        yy = self.grid.y_max_m - rr * self.grid.cell_size_m
        x0, y0, x1, y1 = self.envelope.bounds
        local = (xx >= x0 - 10) & (xx <= x1 + 10) & (yy >= y0 - 10) & (yy <= y1 + 10)
        squares = self.boxes(rr[local], cc[local])
        squares = squares[sh.intersects(squares, self.envelope.buffer(1))]
        all_roofs = sh.union_all([self.retain, *self.roofs])
        self.open_ground = sh.union_all(squares).difference(all_roofs)
        if self.open_ground.is_empty or abs(permanent_clear.area - wide["roof_cleared_m2"]) > TOL:
            raise ValueError("Open terrain or physical roof accounting fails reference check")

    def boxes(self, rows, cols):
        sz = self.grid.cell_size_m
        x, y = self.grid.x_min_m + cols * sz, self.grid.y_max_m - rows * sz
        return sh.box(x, y - sz, x + sz, y)

    def _roof_cost(self, mask):
        return sum(area for owners, area in self.atom_costs.items() if mask & owners == owners)

    def masks(self, outer):
        band = outer.difference(self.inside)
        return (
            bits(sh.area(sh.intersection(self.roofs, band)) > 1e-8),
            bits(sh.intersects(self.cells, band)),
        )

    def amounts(self, roof, ground):
        return self.roof_cost(roof), ground.bit_count() * self.grid.cell_area_m2

    def perimeter(self, outer):
        edge = outer.boundary
        return edge.intersection(self.open_ground).length, edge.length

    def verify_action(self, outer, roofmask, groundmask):
        band = outer.difference(self.inside)
        if self.inside.difference(outer).area > TOL or outer.difference(self.envelope).area > TOL:
            raise ValueError("Moved route violates anchor or reference envelope")
        removed = sorted(
            k
            for k, g in self.buildings.items()
            if k not in self.anchor_ids and g.intersects(band) and g.intersection(band).area > 1e-8
        )
        cells, ground, _ = ground_action(self.grid, band, self.eligible, self.cover)
        selected = set(removed)
        roofs = sh.union_all([self.buildings[k] for k in removed]).difference(
            sh.union_all([g for k, g in self.buildings.items() if k not in selected])
        )
        expected = self.amounts(roofmask, groundmask)
        if (
            removed != [k for i, k in enumerate(self.ids) if roofmask & 1 << i]
            or set(cells) != {int(k) for i, k in enumerate(self.cell_ids) if groundmask & 1 << i}
            or abs(roofs.area - expected[0]) > TOL
            or abs(ground.area - expected[1]) > TOL
            or roofs.intersection(ground).area > TOL
        ):
            raise ValueError("The band and exact material selections do not agree")
        return removed, sorted(cells), roofs, ground


def select_uniform(terrain, compact, wide, cap):
    choices = []
    for width in np.arange(2.5, 15.01, 0.25):
        outer = terrain.inside.buffer(width, quad_segs=64)
        roof, ground = terrain.masks(outer)
        r, g = terrain.amounts(roof, ground)
        if (
            compact["roof_cleared_m2"] < r < wide["roof_cleared_m2"]
            and compact["ground_cleared_m2"] < g < wide["ground_cleared_m2"]
            and r + g <= cap
        ):
            choices.append((r + g, -width, roof, ground))
    if not choices:
        raise ValueError("No uniform width fits the middle budget")
    _, negative_width, roof, ground = max(choices)
    width = float(-negative_width)
    return width, terrain.inside.buffer(width, quad_segs=64), roof, ground


def option_catalog(terrain, width):
    catalogs = []
    for geom in terrain.anchors:
        options = []
        for delta in (0, -1.25, 1.25):
            w = width + delta
            for step in (0, 2.5, 5):
                if w - step < 2.5 or w + step > 15.01:
                    continue
                for direction in range(8 if step else 1):
                    angle = math.pi * direction / 4
                    dx, dy = step * math.cos(angle), step * math.sin(angle)
                    outer = translate(geom.buffer(w, quad_segs=64), dx, dy)
                    roof, ground = terrain.masks(outer)
                    hit, length = terrain.perimeter(outer)
                    options.append((roof, ground, hit, length, outer, (w, dx, dy)))
        catalogs.append(options)
    return catalogs


def select_aligned(terrain, width, roof0, ground0, cap, seed):
    catalogs = option_catalog(terrain, width)
    rng = np.random.default_rng(seed)
    r0, g0 = terrain.amounts(roof0, ground0)
    tol_r, tol_g = r0 * 0.02, max(terrain.grid.cell_area_m2, g0 * 0.02)
    # Draw before fire simulation. Single-anchor alternatives are also included exhaustively.
    rows = np.column_stack([rng.integers(len(c), size=SEARCH_SIZE) for c in catalogs])
    single = [
        tuple(i if a == j else 0 for a in range(len(catalogs)))
        for j, c in enumerate(catalogs)
        for i in range(1, len(c))
    ]
    draws = set(map(tuple, rows.tolist())) | set(single)
    feasible = []
    for choices in draws:
        rm = gm = 0
        h = length = 0.0
        for c, i in zip(catalogs, choices, strict=True):
            roof, ground, hit, edge = c[i][:4]
            rm |= roof
            gm |= ground
            h += hit
            length += edge
        r, g = terrain.amounts(rm, gm)
        if (
            rm != roof0
            and gm != ground0
            and r + g <= cap
            and abs(r - r0) <= tol_r + TOL
            and abs(g - g0) <= tol_g + TOL
        ):
            feasible.append((-h / length, choices, rm, gm))
    if not feasible:
        raise ValueError("No distinct route meets both registered material tolerances")
    ranked = []
    for _, choices, rm, gm in sorted(feasible)[:SHORTLIST]:
        options = [c[i] for c, i in zip(catalogs, choices, strict=True)]
        outer = sh.union_all([o[4] for o in options])
        hit, edge = terrain.perimeter(outer)
        r, g = terrain.amounts(rm, gm)
        ranked.append(
            (
                (-hit / edge, abs(r - r0) / r0 + abs(g - g0) / g0, choices),
                outer,
                rm,
                gm,
                [o[5] for o in options],
            )
        )
    _, outer, rm, gm, parameters = min(ranked, key=lambda x: x[0])
    return (
        outer,
        rm,
        gm,
        {
            "seed": seed,
            "unique_draws": len(draws),
            "matched_candidates": len(feasible),
            "finalists": len(ranked),
            "roof_tolerance_m2": tol_r,
            "ground_tolerance_m2": tol_g,
            "local_parameters_width_east_north_m": parameters,
        },
    )


def build_village(village, setup, compact, seed):
    wide = next(p for p in setup["plans"] if p["plan_id"] == WIDE)
    cap = (wide["construction_m2"] + compact["construction_m2"]) / 2
    terrain = Terrain(setup, wide)
    width, uniform, ur, ug = select_uniform(terrain, compact, wide, cap)
    aligned, ar, ag, search = select_aligned(terrain, width, ur, ug, cap, seed)
    plans, features = [], []
    for name, outer, rm, gm in ((UNIFORM, uniform, ur, ug), (ALIGNED, aligned, ar, ag)):
        ids, cells, roofs, ground = terrain.verify_action(outer, rm, gm)
        hit, length = terrain.perimeter(outer)
        plans.append(
            dict(
                plan_id=name,
                width_m=width if name == UNIFORM else None,
                anchor_ids=terrain.anchor_ids,
                demolished_ids=ids,
                cleared_physical_cell_ids=cells,
                roof_cleared_m2=roofs.area,
                ground_cleared_m2=ground.area,
                construction_m2=roofs.area + ground.area,
                perimeter_m=length,
                open_perimeter_m=hit,
                open_perimeter_fraction=hit / length,
            )
        )
        features.extend(
            feature(g, layer, plan_id=name)
            for layer, g in (
                ("outer_edge", outer.boundary),
                ("cleared_roof", roofs),
                ("cleared_ground", ground),
            )
        )
    if plans[1]["open_perimeter_fraction"] <= plans[0]["open_perimeter_fraction"]:
        raise ValueError("Proposed route does not increase alignment with mapped open ground")
    route_path = DEST / f"{village}-routes.geojson"
    freeze(
        route_path,
        {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": setup["crs"]}},
            "features": features,
        },
    )
    # The held-out roofs are checked only after both routes have been selected.
    removed = set(plans[0]["demolished_ids"]) | set(plans[1]["demolished_ids"])
    if removed.intersection(f["source_id"] for f in setup["fires"]):
        raise ValueError("A held-out footprint loses support")
    print(
        village,
        "budget",
        round(cap, 2),
        "width",
        width,
        [
            (
                p["plan_id"],
                round(p["roof_cleared_m2"], 2),
                p["ground_cleared_m2"],
                round(100 * p["open_perimeter_fraction"], 2),
            )
            for p in plans
        ],
        flush=True,
    )
    return {
        **setup,
        "plans": plans,
        "wide_reference": wide,
        "budget_cap_m2": cap,
        "compact_reference_areas_m2": {
            k: compact[k] for k in ("roof_cleared_m2", "ground_cleared_m2", "construction_m2")
        },
        "search": search,
        "route_map": file_binding(route_path),
    }


def main():
    p = PARENT / "registration.json"
    q = COMPACT_ARCHIVE / "registration.json"
    parent, compact = read_json(p), read_json(q)
    for record in (parent, compact):
        if (
            content_hash({k: v for k, v in record.items() if k != "registration_sha256"})
            != record["registration_sha256"]
        ):
            raise ValueError("Archive registration seal does not match")
    villages = {}
    for idx, (name, setup) in enumerate(sorted(parent["villages"].items())):
        for filename, checksum in setup["input_files_sha256"].items():
            if digest(ROOT / filename) != checksum:
                raise ValueError(f"Changed building, material or spatial input: {filename}")
        villages[name] = build_village(
            name, setup, compact["villages"][name]["plans"][0], SEED + idx
        )
    trials = sum(len(v["fires"]) * len(parent["winds"]) for v in villages.values())
    reg = {
        "schema_version": "heritage-geometry-matched-midbudget-1",
        "villages": villages,
        "original_solver_sha256": parent["original_solver_sha256"],
        "winds": parent["winds"],
        "primary": {
            **parent["primary"],
            "planned_triplets": trials,
            "planned_new_runs": 2 * trials,
        },
        "terminal_endpoint": parent["terminal_endpoint"],
        "wide_registration": file_binding(p),
        "compact_budget_registration": file_binding(q),
        "design_program": file_binding(Path(__file__).resolve()),
        "readout_program": file_binding(
            Path(__file__).with_name("run_heritage_geometry_validation.py").resolve()
        ),
        "run_program": file_binding(
            Path(__file__).with_name("run_heritage_midbudget_routes.py").resolve()
        ),
        "backend_verification": file_binding(PARENT / "backend-verification.json"),
        "domain_reuse_verification": file_binding(
            COMPACT_ARCHIVE / "geometry-reuse-verification.json"
        ),
        "protocol": file_binding(DEST / "实验方案.md"),
    }
    reg["registration_sha256"] = content_hash(reg)
    freeze(DEST / "registration.json", reg)
    print(f"Registered {trials} three-arm comparisons", flush=True)


if __name__ == "__main__":
    main()
