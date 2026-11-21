"""Partition the registered Pingyan joint arm, preserving its resident first simulation."""

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs/research/heritage-pingyan-independent-three-stage-supplement-20261111"
CONFIG = (
    ROOT
    / "examples/research/heritage/confirmation-20261110/pingyan-three-fixed-plans-20261111"
    / "supplemental-stage-capacity-config.json"
)
AREA = RUN / "resource-scheduling/registered-joint-family-shards"
PID = 39628
PROBLEM = "e5f53405f9df6d139677cd2997f8468c21023eb8f5f7cfdd86b4cf9c9c143580"
SCRIPT = Path(__file__).resolve()
REG = AREA / "registration.json"


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    tmp.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_native():
    snapshot = RUN / "source-snapshot"
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(snapshot / "scripts"), str(snapshot / "src")]
    import run_heritage_joint_comparison as native

    return native


def ps(pid, field):
    p = subprocess.run(["ps", "-p", str(pid), "-o", field + "="], capture_output=True, text=True)
    return p.stdout.strip()


def power():
    value = subprocess.check_output(["pmset", "-g", "batt"], text=True)
    m = re.search(r"(\d+)%", value)
    return "'AC Power'" in value, bool((m and int(m[1]) <= 3) or "Final" in value)


def memory_free():
    value = subprocess.check_output(["memory_pressure", "-Q"], text=True)
    return int(re.search(r"free percentage: (\d+)%", value)[1])


def case_folder(native, index, owner, checks):
    return native.result_folder(owner, *checks[index - 1])


def child_home(index):
    return AREA / f"slot-{index:02d}"


def tables(native):
    exp = read(RUN / "experiment.json")
    cfg = read(CONFIG)
    if (
        exp["problem_sha256"] != PROBLEM
        or exp["config_sha256"] != digest(CONFIG)
        or cfg != exp["config"]
    ):
        raise ValueError("original physical problem or frozen inputs changed")
    rows = list(native.execution_checks(cfg))
    if len(rows) != 24 or [p.plan_id for p, _ in rows] != [
        p for p in cfg["execution_priority_plan_ids"] for _ in range(8)
    ]:
        raise ValueError("the original 3 x 4 x 2 declared sequence changed")
    if not cfg["terminal_fire_verification"] or not cfg["stop_plan_after_protection_failure"]:
        raise ValueError("strict full fire course or first-counterexample rule changed")
    if not all(
        rows[i][1].endswith("-h1") and rows[i + 1][1] == rows[i][1][:-1] + "0.5"
        for i in range(0, 24, 2)
    ):
        raise ValueError("declared family meshes do not pair")
    return exp, cfg, rows


def verify_registration():
    reg = read(REG)
    if (
        reg["driver_sha256"] != digest(SCRIPT)
        or reg["experiment_sha256"] != digest(RUN / "experiment.json")
        or reg["preflight_sha256"] != digest(RUN / "preflight.json")
        or reg["config_sha256"] != digest(CONFIG)
    ):
        raise RuntimeError("execution registration identity changed")
    return reg


def initialize(native, cfg, rows):
    budget = read(RUN / "execution-budget.json")
    command = ps(PID, "command")
    if (
        command
        != read(RUN / "resource-scheduling/1794402795767124000/pause-receipt.json")[
            "original_command"
        ]
        or budget["active"] != {"pid": PID}
        or budget["started_checks"] != 1
    ):
        raise RuntimeError("original exclusive first case is not live")
    guard = case_folder(native, 2, RUN, rows)
    if guard.exists() or list(RUN.glob("evaluations/*/complete.json")):
        raise RuntimeError("native registration already advanced")
    epoch = dt.datetime.strptime(ps(PID, "lstart"), "%a %b %d %H:%M:%S %Y").timestamp()
    reg = dict(
        schema="heritage-registered-family-execution-1",
        problem_sha256=PROBLEM,
        experiment_sha256=digest(RUN / "experiment.json"),
        preflight_sha256=digest(RUN / "preflight.json"),
        config_sha256=digest(CONFIG),
        driver_sha256=digest(SCRIPT),
        created_unix_s=time.time(),
        resident_pid=PID,
        resident_unix_start_lower_bound_s=epoch - 1,
        resident_elapsed_at_registration_s=budget["elapsed_s"],
        resident_command=command,
        max_concurrent_pingyan_workers=3,
        budget_total_started=cfg["max_new_checks"],
        budget_cumulative_worker_s=cfg["max_wall_time_s"],
        joint_ordinals=list(range(1, 9)),
        residual_original_ordinals=list(range(9, 25)),
        native_problem_or_inputs_changed=False,
        resident_fence=str(guard.relative_to(RUN)),
        native_resume_predicate=(
            "If evaluation folder exists, read_complete precedes all slot charges. A "
            "scheduler-only directory has no valid endpoints; it seals this resident budget "
            "after slot 1."
        ),
        worker_rule=(
            "Original first case retained. Launch a family coarse case before its fine case. "
            "Among eligible idle families use original ordinal. At most one active scale per "
            "family. One registered ordinal per private native invocation; no retry. Check "
            "strict native core counterexample before further starts; already started runs "
            "finish."
        ),
        resource_rule=(
            "Every legacy or projected native attempt counts against the same 24; count "
            "cumulative worker wall time including pauses with conservative live bounds. "
            "Stop before the shared 172800s cap. New arms wait until all joint-family jobs "
            "settle."
        ),
    )
    save(REG, reg)
    guard.mkdir(parents=True)
    save(
        guard / "scheduler-reservation.json",
        {
            "ordinal": 2,
            "kind": "resource scheduling only",
            "registration_sha256": digest(REG),
        },
    )
    return reg


def prepare_child(index):
    owner = child_home(index)
    owner.mkdir()
    for name in ("experiment.json", "preflight.json"):
        os.link(RUN / name, owner / name)
    (owner / "source-snapshot").symlink_to(RUN / "source-snapshot", target_is_directory=True)
    save(
        owner / "execution-budget.json",
        {"elapsed_s": 0.0, "started_checks": 0, "active": None},
    )
    return owner


def worker(index):
    native = frozen_native()
    reg = verify_registration()
    _, cfg, checks = tables(native)
    path = child_home(index)
    ticket = read(path / "reservation.json")
    if (
        ticket["registration_sha256"] != digest(REG)
        or ticket["ordinal"] != index
        or index not in reg["joint_ordinals"][1:]
    ):
        raise RuntimeError("worker has no exact admission")
    if read(path / "execution-budget.json") != {
        "elapsed_s": 0.0,
        "started_checks": 0,
        "active": None,
    }:
        raise RuntimeError("a charged slot cannot be reissued")
    native.execution_checks = lambda validated: (
        iter([checks[index - 1]]) if validated == cfg else iter(())
    )
    native.resume(CONFIG, path)


def strict_result(native, owner, row):
    plan, scenario = row
    folder = native.result_folder(owner, plan, scenario)
    if not (folder / "complete.json").is_file():
        return None, None
    check = native.read_complete(
        folder,
        PROBLEM,
        plan_id=plan.plan_id,
        scenario_id=scenario,
        demolished_ids=plan.demolished_ids,
        cleared_physical_cell_ids=plan.cleared_physical_cell_ids,
        terminal_required=True,
    )
    witness = None
    if check["status"] == "failed" and check["evidence"]["invalid_reasons"] == []:
        witness = native.native_protection_failure_witness(
            owner, PROBLEM, plan, scenario, terminal_required=True
        )
    return {
        "status": check["status"],
        "manifest_sha256": digest(folder / "complete.json"),
    }, witness


def supervise():
    native = frozen_native()
    exp, cfg, checks = tables(native)
    AREA.mkdir(parents=True, exist_ok=True)
    lock = (AREA / "writer.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if REG.exists():
        raise RuntimeError(
            "existing registration requires controlled recovery; no duplicate workers"
        )
    reg = initialize(native, cfg, checks)
    jobs = {}
    observed = {}
    failures = {}
    pauses = set()
    interruptions = {}
    last_signature = None
    settled = {}

    def owner(i):
        return RUN if i == 1 else child_home(i)

    while True:
        now = time.time()
        resident_budget = read(RUN / "execution-budget.json")
        resident_live = (
            resident_budget["active"] == {"pid": PID}
            and ps(PID, "command") == reg["resident_command"]
        )
        if not resident_live and resident_budget["active"] is not None:
            raise RuntimeError("resident identity or budget unexpectedly changed")
        living = {i: p for i, p in jobs.items() if p.poll() is None}
        pids = {i: p.pid for i, p in living.items()}
        if resident_live:
            pids[1] = PID
        # A terminal manifest becomes available atomically; no further launch before
        # validation of any strict counterexample newly committed by any family.
        for i in [1, *jobs]:
            if i not in observed:
                result, witness = strict_result(native, owner(i), checks[i - 1])
                if result:
                    observed[i] = result
                    if witness:
                        failures[i] = witness
            if i not in pids and i not in settled:
                b = read(owner(i) / "execution-budget.json")
                if b["active"] is not None:
                    raise RuntimeError("dead worker still has an open native budget")
                if b["started_checks"] != 1 or i not in observed:
                    interruptions[i] = (
                        "charged or failed worker did not produce a valid native manifest"
                    )
                settled[i] = b
        spent_s = 0.0
        for i in [1, *jobs]:
            if i in settled:
                spent_s += settled[i]["elapsed_s"]
            elif i == 1:
                spent_s += (
                    now
                    - reg["resident_unix_start_lower_bound_s"]
                    + reg["resident_elapsed_at_registration_s"]
                )
            else:
                spent_s += max(0, now - read(owner(i) / "reservation.json")["unix_before_launch_s"])
        # Stop no later than the shared cap even when all three running slots are charged.
        if spent_s >= cfg["max_wall_time_s"] - 300 and pids:
            for i, pid in pids.items():
                if i not in interruptions:
                    os.kill(pid, signal.SIGCONT)
                    os.kill(pid, signal.SIGINT)
                    interruptions[i] = "shared cumulative wall limit"
        ac, critical = power()
        for i, pid in pids.items():
            if not ac and critical and "T" not in ps(pid, "state"):
                os.kill(pid, signal.SIGSTOP)
                pauses.add(i)
            elif ac and i in pauses:
                os.kill(pid, signal.SIGCONT)
                pauses.discard(i)
        # Three total campaign workers, including the already resident PID.
        available = 3 - len(pids)
        free = memory_free()
        if (
            not failures
            and not interruptions
            and ac
            and free >= 18
            and spent_s < cfg["max_wall_time_s"] - 300
        ):
            for i in range(2, 9):
                if available <= 0:
                    break
                if i in jobs or (i % 2 == 0 and i - 1 not in settled):
                    continue
                # The successor to the resident is never dispatched until it has
                # released its own fenced native budget.
                if shutil.disk_usage(RUN).free < cfg["minimum_free_disk_GB"] * 1e9:
                    interruptions[i] = "native disk minimum"
                    break
                home = prepare_child(i)
                save(
                    home / "reservation.json",
                    {
                        "ordinal": i,
                        "unix_before_launch_s": time.time(),
                        "registration_sha256": digest(REG),
                    },
                )
                env = {
                    **os.environ,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "OPENBLAS_NUM_THREADS": "1",
                    "OMP_NUM_THREADS": "1",
                    "VECLIB_MAXIMUM_THREADS": "1",
                }
                with (home / "resume.log").open("a") as stream:
                    jobs[i] = subprocess.Popen(
                        [sys.executable, str(SCRIPT), "--slot", str(i)],
                        stdout=stream,
                        stderr=stream,
                        env=env,
                    )
                available -= 1
        sig = (
            tuple(sorted(jobs)),
            tuple(sorted(settled)),
            tuple(sorted(observed)),
            tuple(sorted(failures)),
            tuple(sorted(pauses)),
            tuple(sorted(interruptions)),
        )
        if sig != last_signature or now - int(read(AREA / "ledger.json")["updated_unix_s"]) >= 30:
            save(
                AREA / "ledger.json",
                dict(
                    registration_sha256=digest(REG),
                    updated_unix_s=now,
                    reserved_ordinals=[1, *jobs],
                    published_ordinals=sorted(observed),
                    closed_native_worker_budgets=settled,
                    child_pids={i: p.pid for i, p in jobs.items()},
                    resident_pid=PID,
                    observations=observed,
                    strict_native_counterexample_ordinals=sorted(failures),
                    interruptions=interruptions,
                    paused_by_scheduler=sorted(pauses),
                    conservative_cumulative_live_and_settled_worker_s=spent_s,
                    total_declared_slots=24,
                    other_plans_not_issued=16,
                ),
            )
            last_signature = sig
        if (
            not resident_live
            and all(p.poll() is not None for p in jobs.values())
            and (interruptions or failures or len(settled) == 8)
        ):
            break
        time.sleep(4)

    if interruptions:
        raise RuntimeError(
            "registered family native interruption; see immutable per-job charges and ledger"
        )
    # Canonical root has exactly one former writer and is now closed. Verify every
    # private record and its native input identity before moving its native output.
    guard = case_folder(native, 2, RUN, checks)
    if read(guard / "scheduler-reservation.json")["registration_sha256"] != digest(REG) or {
        p.name for p in guard.iterdir()
    } != {"scheduler-reservation.json"}:
        raise RuntimeError("scheduler-only fence changed")
    (guard / "scheduler-reservation.json").rename(AREA / "retired-fence.json")
    guard.rmdir()
    for i in sorted(jobs):
        src = case_folder(native, i, child_home(i), checks)
        dest = case_folder(native, i, RUN, checks)
        if dest.exists() or digest(src / "complete.json") != observed[i]["manifest_sha256"]:
            raise RuntimeError("ambiguous native output provenance")
        # Keep the independently written shard as a byte-for-byte audit source.
        staging = dest.with_name(dest.name + ".importing")
        shutil.copytree(src, staging, copy_function=os.link)
        native.read_complete(
            staging,
            PROBLEM,
            plan_id=checks[i - 1][0].plan_id,
            scenario_id=checks[i - 1][1],
            demolished_ids=checks[i - 1][0].demolished_ids,
            cleared_physical_cell_ids=checks[i - 1][0].cleared_physical_cell_ids,
            terminal_required=True,
        )
        staging.rename(dest)
    orig = read(RUN / "execution-budget.json")
    total = sum(b["started_checks"] for b in settled.values())
    wall = sum(b["elapsed_s"] for b in settled.values())
    if (
        total != len(observed)
        or total > 8
        or wall > cfg["max_wall_time_s"]
        or orig["active"] is not None
    ):
        raise RuntimeError("native cumulative budget cannot be certified")
    save(AREA / "resident-budget-before-merge.json", orig)
    save(
        RUN / "execution-budget.json",
        {"elapsed_s": wall, "started_checks": total, "active": None},
    )
    if failures:
        i = min(failures)
        witness = native.native_protection_failure_witness(
            RUN, PROBLEM, *checks[i - 1], terminal_required=True
        )
        for j in range(1, 9):
            if j not in observed:
                native.persist_execution_skip(RUN, exp, *checks[j - 1], witness)
    result = native.summarize(RUN)
    if result["completed_checks"] != len(observed):
        raise RuntimeError("merged domain revalidation failed")
    save(
        AREA / "merged-registration-receipt.json",
        dict(
            problem_sha256=PROBLEM,
            original_observed_cases_sorted=sorted(observed),
            native_total_started=total,
            charged_cumulative_wall_s=wall,
            strict_counterexamples_sorted=sorted(failures),
            remaining_global_checks=24 - total,
            individual_manifests=observed,
            registration_sha256=digest(REG),
        ),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slot", type=int)
    args = parser.parse_args()
    if args.slot is None:
        supervise()
    else:
        worker(args.slot)
