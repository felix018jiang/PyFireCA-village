"""Mosaic Batuan GeoTIFF exports, retaining alpha and source-resolution provenance."""

import hashlib
import json
from contextlib import ExitStack
from pathlib import Path

import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.warp import Resampling, calculate_default_transform, reproject

ROOT = Path(__file__).resolve().parents[1]


def main():
    input_dir = Path("/Users/tone/Desktop/new_satellite")
    output = ROOT / "data/local/imagery/batuan-mosaic-20261014"
    output.mkdir(parents=True, exist_ok=True)
    sources, duplicates, seen = [], [], {}
    for path in sorted(input_dir.rglob("*")):
        if path.suffix.lower() not in {".tif", ".tiff"}:
            continue
        with rasterio.open(path) as source:
            if (
                source.crs != rasterio.crs.CRS.from_epsg(3857)
                or source.count != 4
                or source.dtypes != ("uint8",) * 4
            ):
                raise ValueError(f"Unexpected source format: {path}")
            if not np.allclose(source.res, (0.59716428348,) * 2):
                raise ValueError(f"Unexpected source resolution: {path}")
            digest = hashlib.sha256(source.read().tobytes()).hexdigest()
            key = (tuple(source.bounds), digest)
            if key in seen:
                duplicates.append(dict(path=str(path), duplicate_of=str(seen[key])))
                continue
            seen[key] = path
            sources.append(path)
    if not sources:
        raise ValueError("No TIFF inputs")
    with ExitStack() as stack:
        datasets = [stack.enter_context(rasterio.open(path)) for path in sources]
        mosaic, transform = merge(datasets, target_aligned_pixels=True, method="first")
        profile = datasets[0].profile.copy()
        profile.update(
            width=mosaic.shape[2],
            height=mosaic.shape[1],
            transform=transform,
            compress="deflate",
            nodata=None,
        )
        native = output / "batuan-highres-3857.tif"
        with rasterio.open(native, "w", **profile) as destination:
            destination.colorinterp = datasets[0].colorinterp
            destination.write(mosaic)
    with rasterio.open(native) as source:
        target_transform, width, height = calculate_default_transform(
            source.crs, "EPSG:32649", source.width, source.height, *source.bounds
        )
        high = np.zeros((4, height, width), dtype=np.uint8)
        for band in range(4):
            reproject(
                rasterio.band(source, band + 1),
                high[band],
                src_transform=source.transform,
                src_crs=source.crs,
                dst_transform=target_transform,
                dst_crs="EPSG:32649",
                resampling=Resampling.nearest if band == 3 else Resampling.bilinear,
            )
        profile.update(width=width, height=height, transform=target_transform, crs="EPSG:32649")
        colors = source.colorinterp
    high_path = output / "batuan-highres-utm49n.tif"
    with rasterio.open(high_path, "w", **profile) as destination:
        destination.colorinterp = colors
        destination.write(high)
    fallback_path = ROOT / "data/local/imagery/batuan-20261014/batuan-utm49n.tif"
    low = np.zeros_like(high)
    with rasterio.open(fallback_path) as source:
        for band in range(4):
            reproject(
                rasterio.band(source, band + 1),
                low[band],
                src_transform=source.transform,
                src_crs=source.crs,
                dst_transform=target_transform,
                dst_crs="EPSG:32649",
                resampling=Resampling.nearest if band == 3 else Resampling.bilinear,
            )
    high_mask = high[3] > 0
    fallback_mask = ~high_mask & (low[3] > 0)
    composite = high.copy()
    composite[:, fallback_mask] = low[:, fallback_mask]
    composite_path = output / "batuan-composite-utm49n.tif"
    with rasterio.open(composite_path, "w", **profile) as destination:
        destination.colorinterp = colors
        destination.write(composite)
    quality = np.where(high_mask, 2, np.where(fallback_mask, 1, 0)).astype(np.uint8)
    mask_profile = profile.copy()
    mask_profile.update(count=1, dtype="uint8", nodata=0)
    with rasterio.open(output / "source_resolution.tif", "w", **mask_profile) as destination:
        destination.write(quality, 1)
    report = dict(
        sources=[
            dict(path=str(path), file_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            for path in sources
        ],
        duplicates=duplicates,
        source_crs="EPSG:3857",
        source_pixel_size_m=0.59716428348,
        output_crs="EPSG:32649",
        output_pixel_size_m=target_transform.a,
        width=width,
        height=height,
        high_resolution_fraction=float(high_mask.mean()),
        fallback_fraction=float(fallback_mask.mean()),
        missing_fraction=float((quality == 0).mean()),
        fallback_source=str(fallback_path),
        source_resolution_codes={
            "0": "no imagery",
            "1": "original lower-resolution imagery",
            "2": "high-resolution exports",
        },
        overlap_policy="first valid source in sorted path order; duplicate content excluded",
        highres_native=str(native),
        highres_metric=str(high_path),
        composite=str(composite_path),
    )
    (output / "mosaic_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "width",
                    "height",
                    "high_resolution_fraction",
                    "fallback_fraction",
                    "missing_fraction",
                    "composite",
                )
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
