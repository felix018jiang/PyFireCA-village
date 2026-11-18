"""Open the completed default-model validation replays, without changing the candidate server."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pyfireca.village.interactive_v3 import serve_interactive_v3

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--validation", type=Path, default=ROOT / "runs/gaoyou-core-validation-20261006"
    )
    parser.add_argument("--port", type=int, default=8854)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    folder = args.validation.resolve()
    report = json.loads((folder / "report.json").read_text())
    if not report.get("all_stages_completed"):
        parser.error("验收计算未全部完成")
    initial = report["stages"]["full_village"][-1]
    serve_interactive_v3(
        initial["case"],
        initial["run"],
        folder / "full",
        port=args.port,
        open_browser=not args.no_browser,
    )


if __name__ == "__main__":
    main()
