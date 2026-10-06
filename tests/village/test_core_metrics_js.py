"""Verify unequal material masses are not averaged as fuel fractions."""

import importlib.util
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.village.outputs_v3 import write_v3_run_outputs
from pyfireca.village.solver_v3 import run_v3_simulation


@pytest.mark.parametrize("after_burnout_s", [0, 1])
def test_hrr_meaning_at_burnout_boundary_is_preserved(tmp_path, after_burnout_s):
    fixture_path = Path(__file__).parents[2] / "examples/village/vegetation_v3/audit_spread.py"
    spec = importlib.util.spec_from_file_location("burnout_boundary_fixture", fixture_path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    request = fixture.make_request(("grass",))
    physics = request.parameters
    duration = float((physics.growth_time_s + physics.steady_time_s + physics.decay_time_s)[0, 0])
    request = replace(
        request, scenario=replace(request.scenario, end_time_s=duration + after_burnout_s)
    )
    result = run_v3_simulation(request)
    assert result.final_state.state[0, 0] == 4
    assert result.final_state.fuel_remaining_kg[0, 0] == 0
    assert result.final_state.burnout_time_s[0, 0] == pytest.approx(duration)
    mean = result.final_state.interval_mean_hrr_W[0, 0]
    assert mean > 0 if after_burnout_s == 0 else mean == 0
    write_v3_run_outputs(result, request, tmp_path / "run")
    viewer = tmp_path / "run/viewer"
    manifest = json.loads((viewer / "manifest.json").read_text())
    assert manifest["hrr"]["temporal_meaning"] == "mean_over_last_solver_step_ending_at_sample"
    exported = np.fromfile(viewer / manifest["hrr"]["path"], dtype="<f4")
    assert exported[-1] == pytest.approx(mean)


@pytest.mark.parametrize("chunk_frames", [None, 2])
def test_initial_fuel_export_matches_solver_for_chunked_and_flat_viewers(tmp_path, chunk_frames):
    fixture_path = Path(__file__).parents[2] / "examples/village/vegetation_v3/audit_spread.py"
    spec = importlib.util.spec_from_file_location("core_metrics_fixture", fixture_path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    request = fixture.make_request(("grass", "shrub", "tree_equivalent"), end_time_s=120)
    result = run_v3_simulation(request)
    write_v3_run_outputs(result, request, tmp_path / "run", chunk_frames=chunk_frames)
    viewer = tmp_path / "run/viewer"
    manifest = json.loads((viewer / "manifest.json").read_text())
    assert manifest["initial_fuel"]["unit"] == "kg"
    exported = np.fromfile(viewer / manifest["initial_fuel"]["path"], dtype="<f8")
    np.testing.assert_array_equal(exported, result.initial_fuel_kg.ravel())
    assert (viewer / "assets/core-metrics.mjs").is_file()


def test_core_frame_counts_and_mass_weighting():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required")
    module = Path(__file__).parents[2] / "src/pyfireca/village/web/core-metrics.mjs"
    source = (
        "import assert from 'node:assert/strict';\n"
        f"import {{eventOccurredBy,summarizeCoreFrame}} from {json.dumps(module.as_uri())};\n"
        """
        const states = new Uint8Array([3,2,1,254,255, 4,3,2,254,255]);
        const fuel = new Float32Array([0.5,1,1,1,1, 0,0.25,1,1,1]);
        const initial = new Float64Array([2,20,3,100,100]);
        assert.deepEqual(summarizeCoreFrame(states,fuel,initial,0,5),
          {heating:1,burnedOut:0,remainingFuelKg:24});
        assert.deepEqual(summarizeCoreFrame(states,fuel,initial,1,5),
          {heating:1,burnedOut:1,remainingFuelKg:8});
        assert.equal(summarizeCoreFrame(states,fuel,null,1,5).remainingFuelKg,null);
        assert.equal(eventOccurredBy(120,0),false);
        assert.equal(eventOccurredBy(120,119),false);
        assert.equal(eventOccurredBy(120,120),true);
        assert.equal(eventOccurredBy(0,0),true);
        assert.equal(eventOccurredBy(NaN,120),false);
        assert.equal(eventOccurredBy(-1,120),false);
        """
    )
    result = subprocess.run(
        [node, "--input-type=module", "-"],
        input=source,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_chunk_loader_reads_requested_frame_and_bounds_cache():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required")
    module = Path(__file__).parents[2] / "src/pyfireca/village/web/chunk-replay.mjs"
    source = (
        "import assert from 'node:assert/strict';\n"
        f"import {{createChunkFrameLoader}} from {json.dumps(module.as_uri())};\n"
        """
        const store = new Map(), specs = {}, calls = [];
        const types=[['state','uint8',Uint8Array],['fuel','float32-le',Float32Array]];
        for (const [name,dtype,Type] of types) {
          const chunks = [];
          for (let start=0;start<12;start+=2) {
            const path=`${name}-${start}`;
            const values=new Type([start,start+1,start+2,start+3]);
            store.set(path,values.buffer);
            chunks.push({path,frame_start:start,frame_count:2,byte_length:values.byteLength});
          }
          specs[name]={dtype,chunks};
        }
        const fetchBuffer=async path=>{calls.push(path);return store.get(path);};
        const loader=createChunkFrameLoader(specs,2,fetchBuffer);
        const first=await loader.load(0);
        assert.deepEqual([...new Uint8Array(first.state)],[0,1]);
        const next=await loader.load(1);
        assert.deepEqual([...new Float32Array(next.fuel)],[2,3]);
        assert.equal(calls.length,2); // No refetch inside the same chunk.
        new Uint8Array(first.state)[0]=99;
        assert.equal(new Uint8Array((await loader.load(0)).state)[0],0);
        for (const frame of [2,4,6,8,10]) {
          const result=await loader.load(frame);
          assert.deepEqual([...new Uint8Array(result.state)],[frame,frame+1]);
          assert.ok(loader.cachedChunkCount<=4);
        }
        await assert.rejects(loader.load(12),/缺失/);
        const broken=createChunkFrameLoader(specs,2,async()=>new ArrayBuffer(1));
        await assert.rejects(broken.load(0),/长度/);
        let fail=true;
        const retry=createChunkFrameLoader({state:specs.state},2,async path=>{
          if(fail){fail=false;throw new Error('temporary failure');}
          return store.get(path);
        });
        await assert.rejects(retry.load(0),/temporary/);
        assert.equal(new Uint8Array((await retry.load(0)).state)[0],0);
        """
    )
    result = subprocess.run(
        [node, "--input-type=module", "-"],
        input=source,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
