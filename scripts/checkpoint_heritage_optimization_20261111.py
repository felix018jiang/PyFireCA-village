"""Capture the state of the evening's registered runs without altering their native budgets."""

import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from pyfireca.planning.strategy_io import file_sha256, read_json

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs/research"
RESULTS = ROOT / "docs/results"


def snapshot():
    names = (
        "heritage-joint-loss-native-batuan-20261111",
        "heritage-joint-loss-matched-reference-batuan-20261111",
        "heritage-joint-loss-native-pingyan-20261111",
        "heritage-pingyan-observed-clearance-subset-20261111",
        "heritage-pingyan-observed-clearance-last-scale-shard-20261111",
        "heritage-pingyan-independent-three-stage-supplement-20261111",
        "heritage-pingyan-pareto-design-witnesses-20261111",
        "heritage-protected-union-pingyan-20261111",
        "heritage-pingyan-zero-core-heat-and-design-loss-20261111",
        "heritage-pingyan-zero-core-only-coarse-control-20261111",
        "heritage-pingyan-zero-heat-minimum-native-20261111",
    )
    configs = {
        file_sha256(path): path
        for path in sorted((ROOT / "examples/research/heritage").rglob("*.json"))
    }
    rows = []
    for name in names:
        run = RUNS / name
        exp, budget_path = run / "experiment.json", run / "execution-budget.json"
        if not exp.exists() or not budget_path.exists():
            rows.append(dict(run=name, state="preparing" if run.exists() else "not_started"))
            continue
        experiment, budget = read_json(exp), read_json(budget_path)
        pid = (budget["active"] or {}).get("pid")
        live = ""
        if pid:
            p = subprocess.run(
                ["ps", "-p", str(pid), "-o", "stat=,command="],
                text=True,
                capture_output=True,
                check=False,
            )
            live = p.stdout.strip()
        config = configs.get(experiment["config_sha256"])
        if config is None:
            raise ValueError("original execution configuration not found")
        state = "no_live_budget"
        if pid:
            expected = name in live or (
                name == "heritage-protected-union-pingyan-20261111"
                and "heritage-pingyan-protected-union-narrow-first-schedule-20261111" in live
            )
            if live and name.endswith("last-scale-shard-20261111"):
                handles = subprocess.run(
                    ["/usr/sbin/lsof", "-p", str(pid), "-Fn"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                expected = "n" + str(run / "resume.log") in handles.stdout.splitlines()
            if live and name == "heritage-pingyan-pareto-design-witnesses-20261111":
                expected = "scripts/run_heritage_pareto_witnesses.py resume" in live
            if live and name == "heritage-pingyan-zero-core-only-coarse-control-20261111":
                expected = "run_heritage_pingyan_zero_core_control_witness.py resume" in live
            state = (
                ("paused_process" if live.split()[0].startswith("T") else "running")
                if expected
                else "verify_orphan_budget"
            )
        folders = list(run.glob("evaluations/*/complete.json"))
        unknown = list(run.glob("evaluations/*/unknown-manifest.json"))
        checks = [read_json(p.parent / "check.json") for p in folders]
        rows.append(
            dict(
                run=name,
                registered_config=str(config.relative_to(ROOT)),
                experiment_sha256=file_sha256(exp),
                config_sha256=experiment["config_sha256"],
                native_problem_sha256=experiment["problem_sha256"],
                state=state,
                pid=pid,
                slots_charged=budget["started_checks"],
                recorded_manifests=len(folders),
                terminal_manifests=sum(
                    c["evidence"].get("terminal_verification", {}).get("terminal_complete") is True
                    for c in checks
                ),
                unknown_manifests=len(unknown),
                strict_passed=sum(c["status"] == "passed" for c in checks),
                native_budget_sha256=file_sha256(budget_path),
            )
        )
        family_schedule = run / "resource-scheduling/registered-joint-family-shards"
        if (family_schedule / "ledger.json").is_file():
            ledger = read_json(family_schedule / "ledger.json")
            schedule_sha = file_sha256(family_schedule / "registration.json")
            if ledger["registration_sha256"] != schedule_sha:
                raise ValueError("independent family ledger has a different registration")
            rows[-1]["registered_family_allocation"] = {
                "registered_slots": ledger["total_declared_slots"],
                "reserved_ordinals": ledger["reserved_ordinals"],
                "published_ordinals": ledger["published_ordinals"],
                "observations": ledger["observations"],
                "registration_sha256": schedule_sha,
                "ledger_updated_unix_s": ledger["updated_unix_s"],
            }
        overlap = (
            run
            / "resource-scheduling/registered-corridor-family-shards"
            / "priority-preserving-overlap"
        )
        if (overlap / "ledger.json").is_file():
            ledger_bytes = (overlap / "ledger.json").read_bytes()
            ledger = json.loads(ledger_bytes)
            signed = file_sha256(overlap / "registration.json")
            if ledger["this_administration_sha256"] != signed:
                raise ValueError("Corridor allocation ledger does not match its registration")
            rows[-1]["registered_overlapping_allocation"] = dict(
                registration_sha256=signed,
                ledger_sha256=hashlib.sha256(ledger_bytes).hexdigest(),
                earlier_slots_charged=ledger["earlier_charged"],
                corridor_ordinals=ledger["early_corridor_ordinals"],
                corridor_terminal_verified_ordinals=sorted(
                    map(int, ledger["verified_native_observations"])
                ),
                strict_failure_ordinals=ledger["verified_failure_ordinals"],
                ledger_updated_unix_s=ledger["updated_unix_s"],
            )
        capacity = overlap.parent / "registered-adaptive-capacity"
        if (capacity / "ledger.json").is_file():
            raw = (capacity / "ledger.json").read_bytes()
            ledger = json.loads(raw)
            registered = read_json(capacity / "registration.json")
            if registered["total_original_ordinal_budget"] != 24:
                raise ValueError("Adaptive allocation does not preserve the original 24 slots")
            rows[-1]["registered_adaptive_allocation"] = dict(
                registration_sha256=file_sha256(capacity / "registration.json"),
                ledger_sha256=hashlib.sha256(raw).hexdigest(),
                original_charged_or_reserved=ledger["original_total_charged_or_reserved"],
                observed=ledger["all_corridor_observed"],
                strict_failure_ordinals=ledger["strict_failure_ordinals"],
                settled_arms=ledger["merged_arms"],
                ledger_updated_unix_s=ledger["at_unix_s"],
            )
    paired = RUNS / "heritage-pingyan-eight-design-matched-two-1m-native-20261111"
    if (paired / "execution-budget.json").is_file():
        paired_budget = read_json(paired / "execution-budget.json")
        counts = [
            read_json(p.parent / "check.json") for p in paired.glob("evaluations/*/complete.json")
        ]
        rows.append(
            dict(
                run=paired.name,
                experiment_sha256=file_sha256(paired / "experiment.json"),
                registered_slots=paired_budget["slots"],
                slots_charged=len(paired_budget["charged"]),
                recorded_manifests=len(counts),
                strict_passed=sum(c["status"] == "passed" for c in counts),
                active_budget=paired_budget["active"],
                native_budget_sha256=file_sha256(paired / "execution-budget.json"),
            )
        )
    refs = sorted(
        {
            p
            for pattern in (
                "heritage-joint-loss-*-native-audit-20261111.json",
                "heritage-joint-loss-*-native-pairs-20261111.json",
                "heritage-total-protection-and-loss-*-independent-20261111.json",
                "heritage-pingyan-w2.5-vs-w15.01-complete8-paired-20261111.json",
                "heritage-pingyan-three-policies-*-20261111.json",
                "heritage-pingyan-six-options-loss-capped-minimum-20261111.json",
                "heritage-pingyan-w0.01-first-ignition-prefix-20261111.json",
                "heritage-protected-thermal-exposure-two-villages-20261111.json",
                "heritage-pingyan-registered-all-exterior-heritage-energy-audit-20261111.json",
                "heritage-pingyan-compact-new-source-extrapolation-20261111.json",
                "heritage-pingyan-six-options-native-pareto-witness-audit-20261111.json",
                "heritage-pingyan-zero-heat-and-design-loss*-20261111.json",
                "heritage-two-villages-entire-burned-roof-footprints-20261111.json",
                "heritage-all-ordinary-roofs-tight-cap-*-20261111.json",
                "heritage-pingyan-independent-three-arm-protection-and-loss-20261111.json",
                "heritage-pingyan-independent-confirmatory-pareto-20261111.json",
                "heritage-pingyan-zero-heat-first-extensions-*-20261111.json",
                "heritage-pingyan-eight-known-zero-heat-*-20261111.json",
                "heritage-pingyan-zero-core-only-coarse-control-witness-20261111.json",
                "heritage-pingyan-two-matched-1m-original-vs-eight-design-cap-20261111.json",
                "heritage-ordinary-roof-retention-two-villages-20261111.json",
                "heritage-entire-roof-loss-with-construction-20261111.json",
                "heritage-cleared-and-burned-union-area-20261111.json",
                "heritage-pingyan-eight-design-matched-*-20261111.json",
                "heritage-pingyan-two-matched-1m-full-native-pair-audit-20261111.json",
                "heritage-pingyan-four-fixed-placements-eight-coverage-20261111.json",
                "heritage-pingyan-*-independent-strict-counterexample-20261111.json",
                "heritage-pingyan-three-tied-minimum-firebreaks-20261111.geojson",
                "heritage-pingyan-zero-heat-*-native-*-20261111.json",
                "heritage-pingyan-two-zero-heat-*-20261111.json",
            )
            for p in RESULTS.glob(pattern)
        }
    )
    return dict(
        local_time=datetime.now().astimezone().isoformat(),
        runs=rows,
        validated_results={p.name: file_sha256(p) for p in refs},
        continuation=(
            "A paused process retains its unfinished trajectory. Verify its full command "
            "before SIGCONT. If a stored PID is gone, preserve the original budget and manifests. "
            "Reconcile spent work with a recovery receipt before native resume."
        ),
    )


if __name__ == "__main__":
    value = snapshot()
    path = RESULTS / "heritage-optimization-current-checkpoint-20261111.json"
    with NamedTemporaryFile("w", dir=path.parent, delete=False) as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write("\n")
    os.chmod(f.name, 0o644)
    os.replace(f.name, path)
