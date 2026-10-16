"""Prepare and serve the Batuan satellite background in the village viewer."""

from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import json
import shutil
from importlib.resources import files
from pathlib import Path
import webbrowser

import rasterio
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def prepare_viewer(imagery: Path, output: Path, place_name: str, classification: Path | None = None) -> None:
    """Write an imagery-only viewer, without any simulation or old village data."""
    with rasterio.open(imagery) as source:
        if source.crs is None or not source.crs.is_projected:
            raise ValueError("底图需要米制投影坐标系")
        if source.crs.linear_units.lower() not in {"metre", "meter", "metres", "meters"}:
            raise ValueError("底图坐标单位必须为米")
        if source.count < 3 or source.dtypes[:3] != ("uint8",) * 3:
            raise ValueError("底图需要 uint8 RGB 波段")
        transform = source.transform
        if transform.b or transform.d or transform.a <= 0 or transform.e >= 0:
            raise ValueError("底图需要北向上的栅格")
        if abs(transform.a + transform.e) > 1e-8:
            raise ValueError("底图需要正方形像元")
        output.mkdir(parents=True, exist_ok=True)
        bands = [1, 2, 3]
        if source.count >= 4 and source.colorinterp[3] == rasterio.enums.ColorInterp.alpha:
            bands.append(4)
        Image.fromarray(source.read(bands).transpose(1, 2, 0)).save(output / "background.png")
        grid = dict(width=source.width, height=source.height, cell_size_m=transform.a,
                    x_min_m=transform.c, y_max_m=transform.f,
                    coordinate_mode="projected", crs=source.crs.to_string())
        reference = dict(schema_version="image-ref-1", width_px=source.width,
                         height_px=source.height, coordinate_mode="projected",
                         affine_pixel_to_world=list(transform)[:6], world_unit="m",
                         north_reference="crs_grid_north")
    documents = {
        "manifest.json": dict(schema_version="village-background-1", place_name=place_name,
                              background="background.png?imagery=" + hashlib.sha256(imagery.read_bytes()).hexdigest()[:16], imagery_path=str(imagery.resolve()),
                              simulation_ready=False),
        "grid.json": grid,
        "image_ref.json": reference,
        "buildings.json": dict(schema_version="village-buildings-1", buildings=[]),
    }
    if classification is not None:
        report = json.loads((classification / "classification_report.json").read_text(encoding="utf-8"))
        if Path(report["source_imagery"]).resolve() != imagery.resolve():
            raise ValueError("分类源影像与底图不一致，请重新生成分类")
        if report["source_sha256"] != hashlib.sha256(imagery.read_bytes()).hexdigest():
            raise ValueError("底图内容已变化，请重新生成分类")
        documents["grid.json"] = report["grid"]
        documents["manifest.json"]["landcover_preview"] = dict(
            path="landcover.u8", labels=report["labels"], colors=report["colors"],
            class_counts=report["class_counts"], valid_cell_count=report["valid_cell_count"],
            evidence="rgb_hypothesis", report="classification_report.json")
        shutil.copy2(classification / "landcover.u8", output / "landcover.u8")
        shutil.copy2(classification / "classification_report.json", output / "classification_report.json")
    mosaic_report = imagery.parent / "mosaic_report.json"
    if mosaic_report.is_file():
        documents["manifest.json"]["mosaic"] = json.loads(mosaic_report.read_text(encoding="utf-8"))
    for name, document in documents.items():
        (output / name).write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    resources = files("pyfireca.village.web")
    assets = output / "assets"
    assets.mkdir(exist_ok=True)
    for name in ("index.html", "styles.css", "app.js", "core-metrics.mjs", "chunk-replay.mjs"):
        target = output / name if name == "index.html" else assets / name
        target.write_bytes(resources.joinpath(name).read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--imagery", type=Path, default=ROOT / "data/local/imagery/batuan-mosaic-20261014/batuan-composite-utm49n.tif")
    parser.add_argument("--classification", type=Path, default=ROOT / "runs/batuan-landcover-20261014")
    parser.add_argument("--imagery-only", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "runs/batuan-background-20261014/viewer")
    parser.add_argument("--place-name", default="岜团村")
    parser.add_argument("--port", type=int, default=8851)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    classification = None if args.imagery_only else args.classification.resolve()
    prepare_viewer(args.imagery.resolve(), args.output.resolve(), args.place_name, classification)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(args.output.resolve()))
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        url = f"http://127.0.0.1:{args.port}/"
        print(f"{args.place_name}底图：{url}", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
