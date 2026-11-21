"""Move released worker capacity to the original 24 independent slots without resetting them."""

import fcntl
import os
import re
import shutil
import signal
import subprocess
import time
from collections import deque
from pathlib import Path

import adopt_heritage_registered_corridor_workers as handoff
import continue_heritage_registered_corridor_arms as corridor
import pipeline_heritage_registered_independent_arms as old
import schedule_heritage_registered_joint_families as first

AREA = corridor.AREA / "registered-adaptive-capacity"
BASE = first.RUN / "resource-scheduling/resource-baseline-five-live.json"
REFERENCE = {
    "heritage-pingyan-zero-core-heat-and-design-loss-20261111": 79425,
    "heritage-pingyan-zero-heat-minimum-native-20261111": 54346,
}
PREVIOUS = {
    52050: handoff.PIPE_COMMAND,
    56033: "/opt/anaconda3/bin/python scripts/adopt_heritage_registered_corridor_workers.py",
}


def released(verified):
    for name, pid in REFERENCE.items():
        if name in verified:
            continue
        run = first.RUN.parent / name
        budget = first.read(run / "execution-budget.json")
        if (
            budget["active"] is not None
            or first.ps(pid, "command")
            or budget["started_checks"] != 8
        ):
            continue
        summary = first.read(run / "summary.json")
        if summary["completed_checks"] != 8 or summary["planned_checks"] != 8:
            continue
        manifests = list(run.glob("evaluations/*/complete.json"))
        exp = first.read(run / "experiment.json")
        if (
            len(manifests) != 8
            or len({(first.read(p)["plan_id"], first.read(p)["scenario_id"]) for p in manifests})
            != 8
            or not all(first.read(p)["problem_sha256"] == exp["problem_sha256"] for p in manifests)
        ):
            raise RuntimeError(
                "design completion does not agree with its actual eight native files"
            )
        verified[name] = {
            "previous_pid": pid,
            "native_budget_sha256": first.digest(run / "execution-budget.json"),
            "native_experiment_sha256": first.digest(run / "experiment.json"),
            "manifests_sha256": sorted(first.digest(p) for p in manifests),
            "closed_and_verified_unix_s": time.time(),
        }


def resources(history):
    vm = subprocess.check_output(["vm_stat"], text=True)
    page = int(re.search(r"page size of (\d+) bytes", vm)[1])
    total = sum(int(re.search(k + r":\s+(\d+)", vm)[1]) for k in ("Swapins", "Swapouts"))
    now = time.monotonic()
    history.append((now, total))
    while len(history) > 2 and history[1][0] < now - 45:
        history.popleft()
    delta = now - history[0][0]
    rate = (total - history[0][1]) * page / 1048576 / delta if delta else None
    cpu = sum(float(v) for v in subprocess.check_output(["ps", "-Ao", "%cpu="], text=True).split())
    return {
        "free_pct": first.memory_free(),
        "swap_MiB_per_s": rate,
        "window_s": delta,
        "all_process_cpu_pct": cpu,
    }


def target_slots(limit, metrics):
    good = metrics["window_s"] >= 25 and metrics["swap_MiB_per_s"] is not None
    if not good:
        return 3
    if (
        limit == 2
        and metrics["free_pct"] >= 40
        and metrics["swap_MiB_per_s"] <= 0.5
        and metrics["all_process_cpu_pct"] <= 790
    ):
        return 5
    if (
        limit >= 1
        and metrics["free_pct"] >= 35
        and metrics["swap_MiB_per_s"] <= 1.0
        and metrics["all_process_cpu_pct"] <= 820
    ):
        return 4
    return 3


def cleanup_old():
    for pid, cmd in PREVIOUS.items():
        if first.ps(pid, "command") != cmd:
            raise RuntimeError(
                "an earlier continuation moved; leave it running for manual reconciliation"
            )
    # Its original only duty was a future handoff. No scientific process belongs to it.
    os.kill(56033, signal.SIGTERM)
    os.kill(52050, signal.SIGSTOP)
    for _ in range(15):
        inherited = handoff.identify()
        if inherited is not None:
            break
        os.kill(52050, signal.SIGCONT)
        time.sleep(0.5)
        os.kill(52050, signal.SIGSTOP)
    if inherited is None:
        os.kill(52050, signal.SIGCONT)
        raise RuntimeError("a native process could not be identified at the stable handoff")
    os.kill(52050, signal.SIGTERM)
    os.kill(52050, signal.SIGCONT)
    lock = (corridor.AREA / "writer.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX)
    check = handoff.identify()
    if check is None or set(check) != set(inherited):
        raise RuntimeError("ordinal ownership changed across the exclusive handoff")
    return lock, check


def main():
    corridor.verify()
    settle = handoff.resolve_handler()
    native = first.frozen_native()
    exp, cfg, rows = first.tables(native)
    base = first.read(BASE)
    if (
        max(base["swap_delta_combined_MiB_per_s"]) >= 1
        or min(r["free_pct"] for r in base["samples"]) < 35
    ):
        raise RuntimeError("five-process live baseline does not support additional capacity")
    AREA.mkdir(exist_ok=False)
    reg = AREA / "registration.json"
    first.save(
        reg,
        dict(
            registered_unix_s=time.time(),
            native_problem=first.PROBLEM,
            candidate_sha256=cfg["final_candidates_sha256"],
            config_sha256=first.digest(first.CONFIG),
            prior_schedule_sha256=first.digest(old.REG),
            prior_passive_capacity_driver_sha256=first.digest(
                corridor.AREA / "released-design-capacity/original-passive-waiter.py"
            ),
            baseline_vacancy_rule=(
                "A strict same-arm stop may free one of the original three positions for the "
                "next previously frozen arm before any design release."
            ),
            fifth_seat_requires_stable_four_running_seconds=60,
            same_final_settlement_sha256=first.digest(handoff.AREA / "registration.json"),
            fixed_worker_sha256=first.digest(corridor.SCRIPT),
            resource_driver_sha256=first.digest(Path(__file__)),
            witnessed_five_process_baseline_sha256=first.digest(BASE),
            frozen_reference_design_producers=REFERENCE,
            total_original_ordinal_budget=24,
            aggregate_registered_work_s=cfg["max_wall_time_s"],
            admission=(
                "A design producer must have eight original native manifests, a closed "
                "budget and its original PID gone. After >=25s of low page churn allow a "
                "fourth or fifth independent process. Prior arms' actual native starts or "
                "their validated strict same-arm stop precede a later arm."
            ),
            thresholds={
                "four": {
                    "min_free_pct": 35,
                    "max_swap_MiB_s": 1,
                    "max_cpu_percent_of_1000": 820,
                },
                "five": {
                    "min_free_pct": 40,
                    "max_swap_MiB_s": 0.5,
                    "max_cpu_percent_of_1000": 790,
                },
                "pause_one_extra": {"free_pct_at_most": 18, "or_swap_MiB_s_above": 8},
            },
            fine_rule=(
                "Extra capacity takes ready coarse cases first. A second same-arm fine mesh "
                "requires one released design producer, >40% available memory and <=0.5 "
                "MiB/s swap traffic; at most two same-arm fine meshes."
            ),
            physical_limits_unchanged=[cfg["max_nodes"], cfg["max_radiation_edges"]],
            same_source_order=True,
            no_new_research_candidates=True,
        ),
    )
    verified = {}
    history = deque()
    # No old controller is interrupted before released capacity is actually usable.
    while True:
        released(verified)
        metrics = resources(history)
        old_status = first.read(old.AREA / "ledger.json")
        eligible_next_arm = (
            bool(old_status["verified_failure_ordinals"])
            and old_status["combined_live_native_worker_count"] < 3
        )
        if (
            eligible_next_arm or (verified and target_slots(len(verified), metrics) > 3)
        ) and first.power()[0]:
            break
        time.sleep(4)
    # Keep the exclusive writer lock open until this supervisor finishes.
    _writer_lock, jobs = cleanup_old()
    first.save(
        AREA / "exclusive-handoff.json",
        dict(
            at_unix_s=time.time(),
            inherited_ordinals=sorted(jobs),
            all_inherited_pids={i: p.pid for i, p in jobs.items()},
            preceding_group="original joint controller retained",
            design_producers_already_closed=verified,
            resources=metrics,
            old_controller_ids=PREVIOUS,
        ),
    )
    seen, closed, witnesses, paused, extras, merged = {}, {}, {}, set(), [], set()
    last_sig = None
    last_write = 0
    fourth_running_since = None
    while True:
        now = time.time()
        released(verified)
        metrics = resources(history)
        available = target_slots(len(verified), metrics)
        earlier, earlier_wall, _ = old.joint_state(now)
        active = {i: p for i, p in jobs.items() if p.poll() is None}
        if (
            len(earlier) + len(active) - len(paused & active.keys()) >= 4
            and metrics["free_pct"] >= 40
            and metrics["swap_MiB_per_s"] is not None
            and metrics["swap_MiB_per_s"] <= 0.5
        ):
            if fourth_running_since is None:
                fourth_running_since = now
        else:
            fourth_running_since = None
        if available == 5 and (fourth_running_since is None or now - fourth_running_since < 60):
            available = 4

        for i in jobs:
            if i not in seen:
                result, wit = first.strict_result(native, corridor.folder(i), rows[i - 1])
                if result:
                    seen[i] = result
                    if wit:
                        witnesses[i] = wit
            if i not in active and i not in closed:
                b = first.read(corridor.folder(i) / "execution-budget.json")
                if i not in seen or b["active"] is not None or b["started_checks"] != 1:
                    first.save(
                        AREA / "interrupt.json",
                        dict(ordinal=i, budget=b, observed=i in seen),
                    )
                    raise RuntimeError(
                        "registered original native slot exited without a verified closed result"
                    )
                closed[i] = b
        work = earlier_wall + sum(b["elapsed_s"] for b in closed.values())
        work += sum(
            now - first.read(corridor.folder(i) / "admission.json")["unix_before_launch_s"]
            for i in active
        )
        if work >= cfg["max_wall_time_s"] - 300:
            for pid in [*earlier.values(), *(p.pid for p in active.values())]:
                os.kill(pid, signal.SIGCONT)
                os.kill(pid, signal.SIGINT)
            first.save(
                AREA / "wall-limit.json",
                dict(at_unix_s=now, cumulative_work_s=work, reserved=sorted(jobs)),
            )
            raise RuntimeError("shared original work limit")
        ac, crit = first.power()
        for i, p in active.items():
            if not ac and crit and "T" not in first.ps(p.pid, "state"):
                os.kill(p.pid, signal.SIGSTOP)
                paused.add(i)
        count = len(earlier) + len(active) - len(paused & active.keys())
        if ac and count > 3 and (metrics["free_pct"] <= 18 or metrics["swap_MiB_per_s"] > 8):
            candidates = [i for i in reversed(extras) if i in active and i not in paused]
            if candidates:
                i = candidates[0]
                os.kill(active[i].pid, signal.SIGSTOP)
                paused.add(i)
        if ac and count < available:
            for i in sorted(paused & active.keys()):
                if count >= available:
                    break
                os.kill(active[i].pid, signal.SIGCONT)
                paused.remove(i)
                count += 1
        narrow_failed = any(9 <= i <= 16 for i in witnesses)
        narrow_charged = all(
            i in jobs
            and first.read(corridor.folder(i) / "execution-budget.json")["started_checks"] == 1
            for i in range(9, 17)
        )
        phase = 3 if narrow_failed or narrow_charged else 2
        within = list(range(8 * (phase - 1) + 1, 8 * phase + 1))
        have_failure = any(i in witnesses for i in within)
        if ac and not have_failure and metrics["free_pct"] >= 18 and not (paused & active.keys()):
            while len(earlier) + len(active) < available:
                ready = [i for i in within if i not in jobs and (i % 2 == 1 or i - 1 in closed)]
                fine_running = sum(i % 2 == 0 for i in active if i in within)
                allow_two = (
                    bool(verified)
                    and metrics["free_pct"] >= 40
                    and metrics["swap_MiB_per_s"] <= 0.5
                )
                if fine_running >= 1 + int(allow_two):
                    ready = [i for i in ready if i % 2 == 1]
                if not ready:
                    break
                if len(earlier) + len(active) >= 3:
                    ready.sort(key=lambda i: (i % 2 == 0, i))
                i = ready[0]
                if shutil.disk_usage(first.RUN).free < cfg["minimum_free_disk_GB"] * 1e9:
                    raise RuntimeError("original free-disk minimum")
                if phase == 3 and not (narrow_failed or narrow_charged):
                    raise RuntimeError("next arm cannot precede original earlier eligible starts")
                first.save(
                    AREA / f"admit-{i:02d}.json",
                    dict(
                        at_unix_s=time.time(),
                        ordinal=i,
                        all_earlier_joint_ordinals_charged=True,
                        earlier_narrow_all_charged=narrow_charged,
                        earlier_narrow_verified_stop=narrow_failed,
                        live_before=len(earlier) + len(active),
                        available_now=available,
                        resources=metrics,
                        release_receipt_shas={
                            k: v["native_budget_sha256"] for k, v in verified.items()
                        },
                        driver_registration_sha256=first.digest(reg),
                    ),
                )
                if len(earlier) + len(active) >= 3:
                    extras.append(i)
                jobs[i] = corridor.admit(i, 8, len(jobs))
                active[i] = jobs[i]
                # A new large graph can allocate in stages; expand one extra at a time.
                break
        for arm in (2, 3):
            if arm in merged:
                continue
            prior = (
                (first.AREA / "merged-registration-receipt.json")
                if arm == 2
                else (corridor.AREA / "arm-2-merged.json")
            )
            if not prior.exists():
                continue
            ordinals = set(range(8 * (arm - 1) + 1, 8 * arm + 1))
            issued = ordinals & set(jobs)
            if (
                issued
                and issued <= set(closed)
                and (len(issued) == 8 or bool(issued & set(witnesses)))
            ):
                settle(
                    arm,
                    native,
                    exp,
                    cfg,
                    rows,
                    already_admitted={i: jobs[i] for i in sorted(issued)},
                )
                merged.add(arm)
        sig = (
            tuple(sorted(jobs)),
            tuple(sorted(closed)),
            tuple(sorted(paused)),
            tuple(sorted(verified)),
            tuple(sorted(merged)),
            available,
        )
        if sig != last_sig or now - last_write >= 20:
            first.save(
                AREA / "ledger.json",
                dict(
                    at_unix_s=now,
                    joint_active=earlier,
                    all_corridor_reservations=sorted(jobs),
                    all_corridor_observed=seen,
                    all_corridor_native_closed=closed,
                    strict_failure_ordinals=sorted(witnesses),
                    actual_corridor_live={i: p.pid for i, p in active.items()},
                    paused_original_ordinals=sorted(paused),
                    released_design_producers=verified,
                    actual_or_conservative_total_work_s=work,
                    original_total_charged_or_reserved=8 + len(jobs),
                    eligible_capacity=available,
                    total_processes_including_paused=len(earlier) + len(active),
                    resources=metrics,
                    merged_arms=sorted(merged),
                ),
            )
            last_sig = sig
            last_write = now
        if merged == {2, 3}:
            break
        time.sleep(4)
    final = native.summarize(first.RUN)
    skips = native.read_execution_skips(first.RUN, exp) or {"skipped_cases": []}
    if final["completed_checks"] + len(skips["skipped_cases"]) != 24:
        raise RuntimeError("original 24 slots failed final reconciliation")
    first.save(
        corridor.AREA / "complete-registration.json",
        dict(
            original_declared=24,
            completed_native=final["completed_checks"],
            skipped_by_verified_core_failure=len(skips["skipped_cases"]),
            total_global_budget=first.read(first.RUN / "execution-budget.json"),
            native_summary_sha256=first.digest(first.RUN / "summary.json"),
            all_original_frozen_arms=True,
            every_declared_slot_is_an_actual_simulation=not skips["skipped_cases"],
            dynamic_capacity_registration_sha256=first.digest(reg),
        ),
    )


if __name__ == "__main__":
    main()
