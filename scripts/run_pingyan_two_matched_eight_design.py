"""Two separate-budget new-action replays on the original matched 1 m meshes."""

# ruff: noqa: E402 -- select the registered physics path before any pyfireca imports.

import argparse
import fcntl
import gc
import json
import os
import resource
import shutil
import sys
import time
from pathlib import Path

ROOT = next(
    p
    for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src/pyfireca").is_dir()
)
RES = ROOT / "docs/results"
RUNS = ROOT / "runs/research"
OLD = RUNS / "heritage-pingyan-independent-three-stage-supplement-20261111"
SNAP = OLD / "source-snapshot"
CONFIG = (
    ROOT
    / "examples/research/heritage/confirmation-20261110/pingyan-three-fixed-plans-20261111"
    / "supplemental-stage-capacity-config.json"
)
RUN = RUNS / "heritage-pingyan-eight-design-matched-two-1m-native-20261111"
REG = RES / "heritage-pingyan-eight-design-matched-two-1m-preflight-20261111.json"
MATCH = RES / "heritage-pingyan-two-matched-1m-original-vs-eight-design-cap-20261111.json"
EIGHT = RES / "heritage-pingyan-eight-known-zero-heat-and-design-loss-20261111.json"

# Every simulation module and all native persistence/closure code come from the earlier run.
sys.path[:0] = [str(SNAP / "scripts"), str(SNAP / "src")]
import numpy as np
import run_heritage_joint_comparison as native
from heritage_confirmation_stage_capacity import stage_counts
from shapely.ops import unary_union

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json


def verify(ok, reason):
    if not ok:
        raise ValueError(reason)


def sealed(path):
    d = read_json(path)
    verify(d.pop("receipt_sha256") == content_hash(d), "changed registered receipt")
    return d


def save(path, value):
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write("\n")
    os.replace(tmp, path)


def context():
    exp = read_json(OLD / "experiment.json")
    verify(
        read_json(CONFIG) == exp["config"] and file_sha256(CONFIG) == exp["config_sha256"],
        "different original protocol",
    )
    verify(
        Path(native.__file__).resolve() == SNAP / "scripts/run_heritage_joint_comparison.py",
        "runner outside physical snapshot",
    )
    for k, h in exp["source_sha256"].items():
        verify(file_sha256(SNAP / k) == h, "native physical code changed")
    for k, h in exp["references_sha256"].items():
        verify(file_sha256(k) == h, "original physical input changed")
    prior, _ = native.make_oracle(CONFIG, exp["config"], content_hash(exp["source_sha256"]))
    verify(prior.problem_sha256 == exp["problem_sha256"], "different original mesh oracle")
    analysis = sealed(EIGHT)
    matched = sealed(MATCH)
    p = analysis["model_action"]
    new = native.plan_from_mapping(
        {k: p[k] for k in ("plan_id", "demolished_ids", "cleared_physical_cell_ids")}
        | {"ground_evidence": "eight known designs, first-escape and zero-heritage-heat model"}
    )
    original = prior.plans["fixed-core-and-first-escape-compartment"]
    catalog = {}
    for manifest in OLD.glob("**/evaluations/*/complete.json"):
        m = read_json(manifest)
        if m["plan_id"] != original.plan_id or not m["scenario_id"].endswith("-h1"):
            continue
        verify(
            m["problem_sha256"] == exp["problem_sha256"]
            and file_sha256(manifest.with_name("check.json")) == m["files_sha256"]["check.json"],
            "original case binding",
        )
        sc = m["scenario_id"]
        fire = prior.fires[prior.scenarios[sc][0]]
        r = read_json(manifest.with_name("check.json"))
        e = r["evidence"]
        actual = next(
            i for i in fire["protocol"]["draws"][0]["priority_ids"] if i not in new.demolished_ids
        )
        if (
            actual == prior.actual_source_for(original, sc) == e["source_id"]
            and fire["footprints"][actual].sha256 == e["source_footprint_sha256"]
        ):
            verify(
                r["status"] == "passed" and e["terminal_verification"]["terminal_complete"],
                "original case not closed",
            )
            catalog[sc] = (manifest, e)
    verify(
        len(catalog) == 2
        and content_hash(sorted(file_sha256(m) for m, _ in catalog.values()))
        == matched["matched_manifest_commitment"],
        "different matched source pair",
    )
    return exp, prior, new, catalog


class Replay:
    # Only the intervention declaration, scenario list, and research problem ID change.
    # All four bound native methods operate directly on the original physical oracle data.
    actual_source_for = native.JointComparisonOracle.actual_source_for
    engine_for_scenario = native.JointComparisonOracle.engine_for_scenario
    scenario_for = native.JointComparisonOracle.scenario_for
    evaluate = native.JointComparisonOracle.evaluate

    def __init__(self, prior, plan, selected, experiment):
        self.prior = prior
        self.plans = {plan.plan_id: plan}
        self.scenarios = {s: prior.scenarios[s] for s in selected}
        self.fires = {f: prior.fires[f] for f, _ in self.scenarios.values()}
        self._engine = None
        self._engine_key = None
        self.integration_mask_sha256 = None
        self.reference_problem_sha256 = prior.problem_sha256
        self.problem = experiment["problem"]
        self.problem_sha256 = content_hash(self.problem)
        self.finite_source_certificate = None

    def __getattr__(self, name):
        return getattr(self.prior, name)

    def validate_plan(self, plan):
        if isinstance(plan, str):
            plan = self.plans[plan]
        verify(
            self.plans.get(plan.plan_id) == plan
            and set(plan.demolished_ids) <= set(self.inventory.candidate_ids),
            "undeclared or illegal new physical action",
        )
        return plan


def direct_budget(L, source, fp, old, D):
    ii = np.flatnonzero([o == source for o in L.ignition_owner])
    q = L.node_inputs.parameters
    verify(
        len(ii) > 0 and all(L.owners[j] == (source,) and source not in D for j in ii),
        "new action changed source atoms",
    )
    footprint = unary_union([L.geometry[i] for i in ii])
    verify(
        fp.geometry.symmetric_difference(footprint).area < 1e-8, "source material polygon differs"
    )
    vals = {
        k: float(getattr(q, k).ravel()[ii].sum())
        for k in ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J")
    }
    verify(
        abs(vals["covered_area_m2"] - 25) < 1e-6
        and all(v > 0 and np.isclose(v, old[k], atol=1e-7, rtol=1e-9) for k, v in vals.items()),
        "original source fuel budget differs",
    )
    return content_hash(vals)


def prepare():
    exp, prior, plan, selected = context()
    verify(not RUN.exists() and not REG.exists(), "registered two-slot batch already exists")
    protocol = exp["problem"]["evaluation_temporal_protocol"].copy()
    protocol["reference_problem_sha256"] = exp["problem_sha256"]
    problem = dict(
        schema_version="matched-two-known-design-native-comparison-1",
        parent_original_experiment_sha256=file_sha256(OLD / "experiment.json"),
        source_data_receipt_sha256=file_sha256(MATCH),
        analytical_result_sha256=file_sha256(EIGHT),
        plan=plan.to_mapping(),
        scenario_ids=sorted(selected),
        original_mesh_problem_sha256=exp["problem_sha256"],
        source_provider=(
            "Original family, footprint set, refinement, quadrature and physics "
            "from the original frozen oracle."
        ),
        evaluation_temporal_protocol=protocol,
        new_independent_draws=0,
    )
    experiment = dict(
        problem=problem,
        problem_sha256=content_hash(problem),
        driver_sha256=file_sha256(__file__),
        original_snapshot_sha256=content_hash(exp["source_sha256"]),
        slots=2,
        coarse_scale_m=1.0,
        clock_budget_s=exp["config"]["max_wall_time_s"],
        execution_order=sorted(selected),
        minimum_free_disk_GB=exp["config"]["minimum_free_disk_GB"],
    )
    wrapper = Replay(prior, plan, selected, experiment)
    rows = []
    for sc in sorted(selected):
        p, ev = selected[sc]
        eng = wrapper.engine_for_scenario(sc)
        L = eng.layout
        direct = wrapper.actual_source_for(plan, sc)
        f = wrapper.fires[ev["fire_id"]]
        fp = f["footprints"][direct]
        verify(
            L.sha256 == ev["common_layout_sha256"]
            and wrapper.integration_mask_sha256 == ev["frozen_integrated_node_mask_sha256"],
            "old physical mesh or integrated subset differs",
        )
        scenario = wrapper.scenario_for(sc, direct)
        eng._vegetation_nodes(scenario, plan)
        support = direct_budget(L, direct, fp, ev["source_budget"], set(plan.demolished_ids))
        _, bystage = stage_counts(L, prior.transport, f["refined_ids"], [plan])
        stages = bystage[plan.plan_id]
        peak = max(n for d in stages.values() for n in d.values())
        verify(
            len(L.owners) <= exp["config"]["max_nodes"]
            and peak <= exp["config"]["max_radiation_edges"],
            "stage or node cap exceeded",
        )
        rows.append(
            dict(
                original_manifest_sha256=file_sha256(p),
                exact_old_mesh_and_integration_mask=True,
                common_layout_sha256=L.sha256,
                integrated_node_mask_sha256=wrapper.integration_mask_sha256,
                identical_source_material_sha256=support,
                original_compact_budget_fingerprint=content_hash(ev["source_budget"]),
                source_footprint_sha256=fp.sha256,
                actual_size_covered_and_fuel_match=True,
                nodes=len(L.owners),
                peak_stage_edge_bound=peak,
                actual_stage_upper_bounds=stages,
            )
        )
        print("verified old 1 m mesh, integration mask and source material", len(rows), flush=True)
        del eng, L
        wrapper._engine = None
        wrapper._engine_key = None
        gc.collect()
    RUN.mkdir(parents=True, exist_ok=False)
    shutil.copy2(Path(__file__).resolve(), RUN / "supplement-driver.py")
    save(RUN / "experiment.json", experiment)
    save(RUN / "execution-budget.json", dict(slots=2, charged=[], active=None, elapsed_s=0))
    ram = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    d = dict(
        scope=(
            "Two separate, newly registered 1 m slots for known physical fires. "
            "The prior trial retains its own 24-slot ledger."
        ),
        preflight_passed=len(rows) == 2,
        geometry_rows=rows,
        source_registration_sha256=file_sha256(MATCH),
        new_action_sha256=file_sha256(EIGHT),
        local_batch_experiment_sha256=file_sha256(RUN / "experiment.json"),
        frozen_physics_experiment_sha256=file_sha256(OLD / "experiment.json"),
        driver_sha256=file_sha256(__file__),
        max_nodes=exp["config"]["max_nodes"],
        maximum_edges_per_actual_stage=exp["config"]["max_radiation_edges"],
        preflight_peak_RSS_bytes=ram if sys.platform == "darwin" else 1024 * ram,
        elapsed_memory_scope="Peak of the one preflight process. Solver peak remains unmeasured.",
        free_disk_GB_at_preflight=shutil.disk_usage(RUN).free / 1e9,
        new_fire_trajectories=0,
        new_draws=0,
    )
    d["receipt_sha256"] = content_hash(d)
    save(REG, d)
    print("separate two-slot batch ready", flush=True)


def checked(folder, view, plan, sc):
    return native.read_complete(
        folder,
        view.problem_sha256,
        plan_id=plan.plan_id,
        scenario_id=sc,
        demolished_ids=plan.demolished_ids,
        cleared_physical_cell_ids=plan.cleared_physical_cell_ids,
        terminal_required=True,
    )


def resume():
    r = sealed(REG)
    exp = read_json(RUN / "experiment.json")
    verify(
        file_sha256(RUN / "experiment.json") == r["local_batch_experiment_sha256"]
        and file_sha256(__file__) == exp["driver_sha256"] == r["driver_sha256"],
        "batch declaration or driver changed",
    )
    verify(
        Path(__file__).resolve() == RUN / "supplement-driver.py",
        "resume via the frozen supplemental driver",
    )
    original, prior, plan, selection = context()
    view = Replay(prior, plan, selection, exp)
    verify(
        exp["execution_order"] == sorted(selection)
        and exp["slots"] == 2
        and view.problem_sha256 == exp["problem_sha256"],
        "different work allocation",
    )
    budgetfile = RUN / "execution-budget.json"
    with (RUN / "execution.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = read_json(budgetfile)
        verify(
            not ledger["active"], "unresolved previous charge; inspect saved state before advancing"
        )
        for sc in exp["execution_order"]:
            dest = native.result_folder(RUN, plan, sc)
            if (dest / "complete.json").is_file():
                checked(dest, view, plan, sc)
                continue
            verify(sc not in ledger["charged"] and len(ledger["charged"]) < 2, "slot already used")
            verify(
                shutil.disk_usage(RUN).free > exp["minimum_free_disk_GB"] * 1e9
                and ledger["elapsed_s"] < exp["clock_budget_s"],
                "disk or time limit reached",
            )
            start = time.monotonic()
            ledger["charged"].append(sc)
            ledger["active"] = {"pid": os.getpid(), "scenario": sc}
            save(budgetfile, ledger)
            try:
                check, rec, L, result = view.evaluate(plan, sc)
                ev = check.evidence
                expected = selection[sc][1]
                verify(
                    ev["common_layout_sha256"] == expected["common_layout_sha256"]
                    and ev["frozen_integrated_node_mask_sha256"]
                    == expected["frozen_integrated_node_mask_sha256"]
                    and ev["source_id"] == expected["source_id"]
                    and ev["source_footprint_sha256"] == expected["source_footprint_sha256"]
                    and all(
                        np.isclose(v, expected["source_budget"][k], atol=1e-7, rtol=1e-9)
                        for k, v in ev["source_budget"].items()
                    ),
                    "simulated physical source or grid changed",
                )
                native.persist(RUN, original["config"], view, plan, sc, check, rec, L, result)
                checked(dest, view, plan, sc)
            finally:
                ledger["elapsed_s"] += time.monotonic() - start
                ledger["active"] = None
                save(budgetfile, ledger)
        save(
            RUN / "summary.json",
            dict(
                completed_native_manifests=sum(
                    (native.result_folder(RUN, plan, sc) / "complete.json").is_file()
                    for sc in exp["execution_order"]
                ),
                charged_slots=len(ledger["charged"]),
                registered_slots=2,
                prior_24_slot_budget_updates=0,
                new_draws=0,
            ),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "resume"])
    action = parser.parse_args().mode
    prepare() if action == "prepare" else resume()
