"""Start the prepared full-village Gaoyou CA simulator on localhost."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml

from pyfireca.village.interactive_v3 import serve_interactive_v3
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, default=ROOT / "runs/gaoyou-core-full-20261006")
    parser.add_argument("--port", type=int, default=8851)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--output-root", type=Path, help="保存/读取交互模拟结果的目录")
    args = parser.parse_args()
    prepared = args.prepared.resolve()
    existing_results = ROOT / "runs/gaoyou-core-full-interactive-20261006"
    output_root = args.output_root or (
        existing_results
        if prepared == ROOT / "runs/gaoyou-core-full-20261006" and existing_results.is_dir()
        else prepared / "interactive"
    )
    if not (prepared / "case/manifest.json").is_file():
        parser.error(f"完整村落案例不存在: {prepared / 'case'}; 请先运行 run_gaoyou_core.py")
    manifest = json.loads((prepared / "case/manifest.json").read_text())
    snapshot = ROOT / "runs/gaoyou-vegetation-v3/v03-physics-synthetic-final/case.snapshot.yml"
    transport = None
    expected = manifest.get("metadata", {}).get("sources_sha256", {}).get("case_snapshot_sha256")
    if snapshot.is_file() and expected == hashlib.sha256(snapshot.read_bytes()).hexdigest():
        source = yaml.safe_load(snapshot.read_text())
        geometry = source["transport"]
        weather = source["weather"]
        transport = V3RadiativeTransportParameters(
            **{
                name: (weather if name in {"wind_speed_m_s", "wind_from_deg"} else geometry)[name][
                    "value"
                ]
                for name in V3RadiativeTransportParameters.__dataclass_fields__
            }
        )
    serve_interactive_v3(
        prepared / "case",
        prepared / "run",
        output_root,
        port=args.port,
        open_browser=not args.no_browser,
        material_transport=transport,
    )


if __name__ == "__main__":
    main()
