"""Project a registered TIFF and its LabelMe coordinates together; preserve originals."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.warp import Resampling, calculate_default_transform, reproject

from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.village.vegetation_prepare import choose_local_metric_crs


def prepare(annotations, output):
    annotations, output = Path(annotations).resolve(), Path(output).resolve()
    data = read_json(annotations)
    imagery = annotations.parent / data["imagePath"]
    output.mkdir(parents=True, exist_ok=False)
    with rasterio.open(imagery) as src:
        if (src.width, src.height) != (data["imageWidth"], data["imageHeight"]):
            raise ValueError("annotation/TIFF dimensions differ")
        crs, _ = choose_local_metric_crs(src.crs, src.bounds)
        affine, width, height = calculate_default_transform(
            src.crs, crs, src.width, src.height, *src.bounds
        )
        projector = Transformer.from_crs(src.crs, crs, always_xy=True)
        inverse = ~affine
        transformed = []
        max_roundtrip_px = 0.0
        back = Transformer.from_crs(crs, src.crs, always_xy=True)
        source_inverse = ~src.transform
        for row in data["shapes"]:
            if row["shape_type"] != "polygon":
                raise ValueError("projection entry currently requires polygon annotations")
            points = []
            for x, y in row["points"]:
                wx, wy = src.transform * (x, y)
                px, py = projector.transform(wx, wy)
                u, v = inverse * (px, py)
                if not (-1e-7 <= u <= width + 1e-7 and -1e-7 <= v <= height + 1e-7):
                    raise ValueError("reprojected annotation outside TIFF")
                points.append([float(u), float(v)])
                rx, ry = source_inverse * back.transform(px, py)
                max_roundtrip_px = max(max_roundtrip_px, float(np.hypot(rx - x, ry - y)))
            transformed.append({**row, "points": points})
        profile = src.profile.copy()
        profile.update(
            crs=crs,
            transform=affine,
            width=width,
            height=height,
            count=3,
            compress="deflate",
            tiled=True,
            nodata=0,
        )
        tiff = output / "imagery-metric.tif"
        with rasterio.open(tiff, "w", **profile) as dst:
            for b in range(1, 4):
                reproject(
                    rasterio.band(src, b),
                    rasterio.band(dst, b),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=affine,
                    dst_crs=crs,
                    resampling=Resampling.bilinear,
                )
    write_json(
        output / "annotations.labelme.json",
        {
            **data,
            "shapes": transformed,
            "imageData": None,
            "imagePath": tiff.name,
            "imageWidth": width,
            "imageHeight": height,
        },
    )
    write_json(
        output / "image_ref.json",
        {
            "width_px": width,
            "height_px": height,
            "affine_pixel_to_world": list(affine)[:6],
        },
    )
    write_json(
        output / "projection.json",
        {
            "source_imagery": str(imagery),
            "source_annotations": str(annotations),
            "source_sha256": {str(p): file_sha256(p) for p in (imagery, annotations)},
            "output_sha256": {p.name: file_sha256(p) for p in output.iterdir() if p.is_file()},
            "source_crs": str(src.crs),
            "target_crs": str(crs),
            "shape_count": len(transformed),
            "max_roundtrip_pixel_error": max_roundtrip_px,
            "thermal_simulations": 0,
        },
    )
    print({"shapes": len(transformed), "crs": str(crs), "roundtrip_px": max_roundtrip_px})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.annotations, args.output)
