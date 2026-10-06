"""Fuel topology must distinguish diagonals and never wrap across map edges."""

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).parents[2]


def test_four_and_eight_connectivity_keep_islands_and_diagonals_distinct():
    spec = importlib.util.spec_from_file_location(
        "topology_audit", ROOT / "scripts/audit_core_propagation.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    mask = np.array([[True, False, False, True], [False, True, False, False]])
    four, sizes4 = module.label_components(mask, diagonals=False)
    eight, sizes8 = module.label_components(mask, diagonals=True)
    assert sorted(sizes4) == [1, 1, 1]
    assert sorted(sizes8) == [1, 2]
    assert eight[0, 0] == eight[1, 1] != eight[0, 3]
    assert four[0, 0] != four[1, 1]
    assert np.all(eight[~mask] == -1)
    assert module.label_components(np.zeros((2, 4), dtype=bool), diagonals=True)[1] == []


def test_browser_neighbor_count_uses_initial_fuel_without_edge_wrap():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    uri = (ROOT / "src/pyfireca/village/web/core-metrics.mjs").as_uri()
    script = f"""
import {{adjacentFuelCounts}} from {json.dumps(uri)};
import assert from 'node:assert/strict';
assert.deepEqual(adjacentFuelCounts([1,1,1,1,1,1,1,1,1],3,3,1,1),{{face:4,diagonal:4}});
assert.deepEqual(adjacentFuelCounts([1,0,1,0,0,0],3,2,0,0),{{face:0,diagonal:0}});
assert.deepEqual(adjacentFuelCounts([1,0,0,0,1,0],3,2,0,0),{{face:0,diagonal:1}});
assert.deepEqual(adjacentFuelCounts([1,1,0,1,0,0],3,2,0,0),{{face:2,diagonal:0}});
"""
    subprocess.run([node, "--input-type=module", "-e", script], check=True, capture_output=True)
