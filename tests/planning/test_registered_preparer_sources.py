"""Check the source copies named in the original experiment registrations."""

import hashlib
import json
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[2]
REGISTRATIONS = Path("examples/research/heritage/area-20261110")


@pytest.mark.parametrize(
    ("registration", "source"),
    [
        (
            "batuan-direct-action-ablation-longcap-20261111.json",
            REGISTRATIONS / "batuan-direct-action-ablation-longcap-20261111.preparer.py",
        ),
        (
            "pingyan-zero-core-only-coarse-control-20261111.json",
            Path("scripts/prepare_heritage_pingyan_zero_heat_base_control.py"),
        ),
        (
            "pingyan-zero-core-heat-and-design-loss-native-20261111.json",
            Path("scripts/prepare_heritage_pingyan_zero_heat_full_fire.py"),
        ),
        (
            "pingyan-zero-heat-minimum-native-20261111.json",
            Path("scripts/prepare_heritage_pingyan_zero_heat_minimum_native.py"),
        ),
    ],
)
def test_registered_preparer_source(registration, source):
    record = json.loads((ROOT / REGISTRATIONS / registration).read_bytes())
    hashes = record["additional_frozen_inputs_sha256"]
    # The registration records absolute paths in the original research workspace.
    (expected,) = [
        digest for name, digest in hashes.items() if PurePosixPath(name).match(source.as_posix())
    ]
    assert hashlib.sha256((ROOT / source).read_bytes()).hexdigest() == expected
