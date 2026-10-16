"""Start the prepared Batuan interactive fire simulation."""

import argparse
from pathlib import Path

from pyfireca.village.interactive_v3 import serve_interactive_v3

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepared", type=Path, default=ROOT / "runs/batuan-slope-contact-20261016"
    )
    parser.add_argument("--port", type=int, default=8851)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    serve_interactive_v3(
        args.prepared / "case",
        args.prepared / "run",
        args.prepared / "interactive",
        port=args.port,
        open_browser=not args.no_browser,
    )


if __name__ == "__main__":
    main()
