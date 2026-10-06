"""Browser replay must preserve the meaning of independent fuel components."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is needed for replay checks")
MODULE = Path(__file__).resolve().parents[2] / "src/pyfireca/village/web/component-view.mjs"

# Two tree components share cell 0; cell 1 has one fuel node and cell 2 has none.
# Deliberately mark a burning/burned node's temperature as valid: the overview
# still must only include temperatures of unburned fuel parts.
FIXTURE = """
const raw = {
  ids: ['surface_dead', 'upper_fuel', 'whole'],
  cellId: new Int32Array([0, 0, 1]),
  burnable: new Uint8Array([1, 1, 1]),
  initialFuel: new Float64Array([2, 8, 3]),
  initialDry: new Float64Array([3, 9, 4]),
  initialMoisture: new Float32Array([0.1, 0.7, 0.2]),
  ignitionTime: new Float64Array([2, 20, 4]),
  burnoutTime: new Float64Array([10, NaN, 12]),
  ignitionCause: new Uint8Array([1, 3, 2]),
  states: new Uint8Array([3, 1, 2, 4, 1, 4]),
  fuel: new Float32Array([0.5, 1, 0.75, 0, 1, 0]),
  temperature: new Float32Array([1200, 310, 900, 1500, 330, 999]),
  temperatureValid: new Uint8Array([1, 1, 0, 1, 1, 1]),
  hrr: new Float32Array([100, 20, 200, 0, 0, 0]),
  surface_water: new Float32Array([0.1, 1, 0.2, 0, 0.8, 0]),
};
const close = (actual, expected) =>
  assert.ok(Math.abs(actual - expected) < 1e-6, `${actual} != ${expected}`);
"""


def run_js(body: str) -> None:
    source = (
        "import assert from 'node:assert/strict';\n"
        "import {validateReplay, aggregateReplay, combinedState, occurredBy} from "
        f"{json.dumps(MODULE.as_uri())};\n" + FIXTURE + body
    )
    result = subprocess.run(
        [NODE, "--input-type=module", "-"],
        input=source,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_overview_preserves_partial_burn_and_mass_weighting():
    run_js("""
      assert.equal(validateReplay(raw, 3, 2, [5, 15]), raw);
      const result = aggregateReplay(raw, 3, 2);
      assert.deepEqual([...result.states], [3, 2, 255, 5, 4, 255]);
      assert.deepEqual([...result.presence], [1, 1, 0]);
      close(result.fuel[0], 0.9);
      close(result.fuel[3], 0.8);
      close(result.fuel[1], 0.75);
      close(result.initialMoisture[0], (3 * 0.1 + 9 * 0.7) / 12);
      close(result.initialMoisture[1], 0.2);
      assert.ok(Number.isNaN(result.initialMoisture[2]));
      close(result.hrr[0], 120);
      close(result.surface_water[0], 1.1);
      close(result.surface_water[3], 0.8);
      assert.equal(result.ignitionTime[0], 2);
      assert.equal(result.ignitionCause[0], 1);
      assert.ok(Number.isNaN(result.burnoutTime[0]));
      assert.equal(result.burnoutTime[1], 12);
      // Aggregation leaves scientific node data untouched.
      assert.deepEqual([...raw.states], [3, 1, 2, 4, 1, 4]);
      assert.deepEqual([...raw.fuel], [0.5, 1, 0.75, 0, 1, 0]);
    """)


def test_temperature_only_uses_valid_unburned_components():
    run_js("""
      const result = aggregateReplay(raw, 3, 2);
      assert.equal(result.temperature[0], 310);
      assert.equal(result.temperature[3], 330);
      assert.deepEqual([...result.temperatureValid], [1, 0, 0, 1, 0, 0]);
      for (const index of [1, 2, 4, 5])
        assert.ok(Number.isNaN(result.temperature[index]));
      // When both components are unburned, choose their valid maximum.
      raw.states[0] = 2;
      const warmed = aggregateReplay(raw, 3, 2);
      assert.equal(warmed.temperature[0], 1200);
      assert.equal(warmed.states[0], 2);
    """)


def test_selected_component_does_not_inherit_other_part_fire():
    run_js("""
      const upper = aggregateReplay(raw, 3, 2, 'upper_fuel');
      assert.deepEqual([...upper.states], [1, 255, 255, 1, 255, 255]);
      assert.deepEqual([...upper.presence], [1, 0, 0]);
      assert.equal(upper.fuel[3], 1);
      close(upper.initialMoisture[0], 0.7);
      assert.equal(upper.ignitionTime[0], 20);
      assert.equal(upper.ignitionCause[0], 3);
      assert.equal(occurredBy(upper.ignitionTime[0], 15), false);
      assert.ok(Number.isNaN(upper.ignitionTime[1]));
      assert.ok(Number.isNaN(upper.initialMoisture[1]));
      const lower = aggregateReplay(raw, 3, 2, 'surface_dead');
      assert.deepEqual([...lower.states], [3, 255, 255, 4, 255, 255]);
      assert.equal(lower.burnoutTime[0], 10);
      assert.equal(occurredBy(lower.ignitionTime[0], 15), true);
      assert.throws(() => aggregateReplay(raw, 3, 2, 'missing'));
    """)


def test_component_named_all_remains_individually_selectable():
    run_js("""
      raw.ids[0] = 'all';
      const overview = aggregateReplay(raw, 3, 2, null);
      assert.deepEqual([...overview.states], [3, 2, 255, 5, 4, 255]);
      const named = aggregateReplay(raw, 3, 2, 'all');
      assert.deepEqual([...named.states], [3, 255, 255, 4, 255, 255]);
      close(named.initialMoisture[0], 0.1);
      assert.equal(named.fuel[3], 0);
    """)


def test_excluded_node_retains_unknown_state_without_contributing_mass_or_fire():
    run_js("""
      // A masked cell may retain source material masses but has no active fuel.
      raw.burnable[2] = 0;
      raw.initialMoisture[2] = NaN;
      raw.states[2] = 254;
      raw.states[5] = 254;
      assert.equal(validateReplay(raw, 3, 2, [5, 15]), raw);
      const result = aggregateReplay(raw, 3, 2);
      assert.equal(result.presence[1], 1);
      assert.equal(result.states[1], 254);
      assert.equal(result.states[4], 254);
      assert.ok(Number.isNaN(result.initialMoisture[1]));
      assert.ok(Number.isNaN(result.ignitionTime[1]));
      assert.ok(Number.isNaN(result.burnoutTime[1]));
      for (const index of [1, 4]) {
        assert.equal(result.fuel[index], 0);
        assert.equal(result.hrr[index], 0);
        assert.equal(result.surface_water[index], 0);
        assert.equal(result.temperatureValid[index], 0);
        assert.ok(Number.isNaN(result.temperature[index]));
      }
    """)


def test_state_and_time_semantics():
    run_js("""
      for (const [states, expected] of [
        [[4, 1], 5], [[5, 1], 5], [[4, 4], 4], [[4, 2], 5],
        [[4, 3], 3], [[2, 3], 3], [[1, 1], 1], [[0], 0],
        [[254], 254], [[255], 255], [[], 255],
      ]) assert.equal(combinedState(states), expected);
      assert.equal(occurredBy(20, 19.999), false);
      assert.equal(occurredBy(20, 20), true);
      assert.equal(occurredBy(0, 0), true);
      assert.equal(occurredBy(NaN, 100), false);
      assert.equal(occurredBy(Infinity, 100), false);
      assert.equal(occurredBy(-Infinity, 100), false);
    """)


@pytest.mark.parametrize(
    "mutation",
    [
        "raw.cellId = new Int32Array([0, 0]);",
        "delete raw.burnable;",
        "raw.states = new Uint8Array(5);",
        "raw.fuel = new Float32Array(7);",
        "delete raw.temperatureValid;",
        "raw.cellId[2] = 3;",
        "raw.cellId[2] = -1;",
        "raw.cellId = [0, 0, 0.5];",
        "raw.ids[1] = raw.ids[0];",
        "raw.ids[1] = '';",
        "raw.initialFuel[0] = NaN;",
        "raw.initialDry[0] = -1;",
        "raw.initialMoisture[0] = NaN;",
    ],
)
def test_invalid_component_shapes_and_indices_rejected(mutation):
    run_js(mutation + "assert.throws(() => validateReplay(raw, 3, 2, [5, 15]));")


@pytest.mark.parametrize(
    "arguments",
    [
        "0, 2, [5, 15]",
        "3, 0, []",
        "3, 2, [5]",
        "3, 2, [5, 5]",
        "3, 2, [15, 5]",
        "3, 2, [-1, 5]",
        "3, 2, [5, NaN]",
    ],
)
def test_invalid_grid_or_sampling_rejected(arguments):
    run_js(f"assert.throws(() => validateReplay(raw, {arguments}));")
