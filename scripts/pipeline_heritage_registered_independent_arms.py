"""Admit the next registered arm when earlier slots have started and worker capacity is free."""

import difflib
import fcntl
import importlib.util
import inspect
import os
import shutil
import signal
import time
from pathlib import Path

import continue_heritage_registered_corridor_arms as corridor
import schedule_heritage_registered_joint_families as first

AREA = corridor.AREA / "priority-preserving-overlap"
REG = AREA / "registration.json"
OLD_WAITER = 12034


def adopted_handler():
    """Keep the prior settlement implementation with two explicit administrative changes."""
    original = inspect.getsource(corridor.execute_arm)
    replacements = {
        "def execute_arm(number, native, exp, cfg, rows):": (
            "def execute_arm(number, native, exp, cfg, rows, *, already_admitted):"
        ),
        "jobs, results, witnesses, closed, interrupted = {}, {}, {}, {}, {}": (
            "jobs, results, witnesses, closed, interrupted = dict(already_admitted), {}, {}, {}, {}"
        ),
    }
    modified = original
    for old, new in replacements.items():
        if modified.count(old) != 1:
            raise ValueError(
                "the frozen base scheduler differs from the declared administrative patch"
            )
        modified = modified.replace(old, new)
    (AREA / "adoption.patch").write_text(
        "".join(
            difflib.unified_diff(
                original.splitlines(True),
                modified.splitlines(True),
                fromfile="registered-arm",
                tofile="adopt-previously-issued-jobs",
            )
        )
    )
    path = AREA / "adopt_registered_processes.py"
    header = (
        "from continue_heritage_registered_corridor_arms import "
        "AREA, admit, first, folder, os, shutil, signal, time\n\n"
    )
    path.write_text(header + modified)
    spec = importlib.util.spec_from_file_location("verified_original_arm_adoption", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.execute_arm, path


def joint_state(now):
    ledger = first.read(first.AREA / "ledger.json")
    if sorted(ledger["reserved_ordinals"]) != list(range(1, 9)) or ledger["interruptions"]:
        raise RuntimeError(
            "earlier scientific priority has incomplete admissions or an interruption"
        )
    open_jobs = {}
    charges = {}
    work_s = 0.0
    for i in range(1, 9):
        home = first.RUN if i == 1 else first.child_home(i)
        path = home / "execution-budget.json"
        before = first.AREA / "resident-budget-before-merge.json"
        if i == 1 and before.exists():
            path = before
        budget = first.read(path)
        if budget["started_checks"] != 1:
            raise RuntimeError(
                "each of the eight earlier native slots must already be charged once"
            )
        charges[i] = first.digest(path)
        if budget["active"] is not None:
            pid = budget["active"]["pid"]
            if (
                i == 1
                or str(first.SCRIPT) not in first.ps(pid, "command")
                or f"--slot {i}" not in first.ps(pid, "command")
            ):
                raise RuntimeError("an earlier live worker identity changed")
            open_jobs[i] = pid
            work_s += now - first.read(home / "reservation.json")["unix_before_launch_s"]
        else:
            work_s += budget["elapsed_s"]
    return open_jobs, work_s, charges


def main():
    first.verify_registration()
    corridor.verify()
    native = first.frozen_native()
    exp, cfg, rows = first.tables(native)
    science = first.CONFIG.parent / "execution-amendment.json"
    protocol = first.read(science)
    if (
        protocol["execution_order"] != cfg["execution_priority_plan_ids"]
        or cfg["execution_case_order"] != "plans_first"
        or not cfg["stop_plan_after_protection_failure"]
    ):
        raise RuntimeError("scientific priority or plan-local stopping contract changed")
    if (
        list(corridor.AREA.glob("slot-*"))
        or (first.AREA / "merged-registration-receipt.json").exists()
    ):
        raise RuntimeError("existing next-arm admission cannot be duplicated")
    going, work_s, hashes = joint_state(time.time())
    if (
        first.ps(OLD_WAITER, "command")
        != "/opt/anaconda3/bin/python scripts/continue_heritage_registered_corridor_arms.py"
    ):
        raise RuntimeError("the original passive waiting controller cannot be identified")
    AREA.mkdir(exist_ok=False)
    handle, source = adopted_handler()
    first.save(
        REG,
        dict(
            schema="heritage-overlap-after-earlier-arm-all-native-starts-1",
            registered_unix_s=time.time(),
            native_problem_sha256=first.PROBLEM,
            original_scientific_execution_amendment_sha256=first.digest(science),
            original_resource_registration_sha256=first.digest(corridor.REG),
            joint_administrative_registration_sha256=first.digest(first.REG),
            all_prior_eight_native_charge_evidence_at_freeze=hashes,
            preceding_active_at_registration=going,
            preceding_cumulative_wall_upper_at_registration_s=work_s,
            earlier_arms_required_to_be_admitted_before_later_arm=True,
            earlier_complete_arrival_barrier_for_new_private_work=False,
            earlier_complete_reconciliation_required_before_merging_any_next_arm=True,
            superseded_passive_controller={
                "pid": OLD_WAITER,
                "previous_scope": (
                    "waiting for first joint merge; no previous corridor native issue"
                ),
            },
            unchanged_checks_limit=24,
            unchanged_cumulative_worker_wall_s=cfg["max_wall_time_s"],
            combined_maximum_workers=3,
            strict_failure_stop_scope=(
                "per frozen candidate, native verified strict terminal core ignition only; "
                "previously issued work finishes"
            ),
            patch_sha256=first.digest(AREA / "adoption.patch"),
            adopted_handler_sha256=first.digest(source),
            supervisor_sha256=first.digest(Path(__file__)),
            inherited_frozen_native_worker_sha256=first.digest(corridor.SCRIPT),
        ),
    )
    os.kill(OLD_WAITER, signal.SIGTERM)
    lock = (corridor.AREA / "writer.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX)
    if list(corridor.AREA.glob("slot-*")):
        raise RuntimeError("an old controller issued before the exclusive handoff")
    first.save(
        AREA / "exclusive-handoff.json",
        dict(
            at_unix_s=time.time(),
            old_pid=OLD_WAITER,
            old_native_slots=0,
            exclusive_directory_lock_acquired=True,
            registered_sha256=first.digest(REG),
        ),
    )
    jobs, seen, closed, witnesses, paused = {}, {}, {}, {}, set()
    overlap_ledger = AREA / "ledger.json"
    merge = first.AREA / "merged-registration-receipt.json"
    while not merge.exists():
        if first.digest(source) != first.read(REG)["adopted_handler_sha256"]:
            raise RuntimeError("adoption implementation changed")
        now = time.time()
        live_joint, joint_wall, _ = joint_state(now)
        active = {i: p for i, p in jobs.items() if p.poll() is None}
        for i in jobs:
            if i not in seen:
                result, witness = first.strict_result(native, corridor.folder(i), rows[i - 1])
                if result:
                    seen[i] = result
                    if witness:
                        witnesses[i] = witness
            if i not in active and i not in closed:
                b = first.read(corridor.folder(i) / "execution-budget.json")
                if b["active"] is not None or b["started_checks"] != 1 or i not in seen:
                    raise RuntimeError(
                        "early admitted native work did not close with a verified observation"
                    )
                closed[i] = b
        work = (
            joint_wall
            + sum(b["elapsed_s"] for b in closed.values())
            + sum(
                now - first.read(corridor.folder(i) / "admission.json")["unix_before_launch_s"]
                for i in active
            )
        )
        if work >= cfg["max_wall_time_s"] - 300:
            for pid in [*live_joint.values(), *(p.pid for p in active.values())]:
                os.kill(pid, signal.SIGCONT)
                os.kill(pid, signal.SIGINT)
            raise RuntimeError(
                "joint cumulative registered wall limit; all previous starts remain charged"
            )
        ac, critical = first.power()
        for i, p in active.items():
            if not ac and critical and "T" not in first.ps(p.pid, "state"):
                os.kill(p.pid, signal.SIGSTOP)
                paused.add(i)
            elif ac and i in paused:
                os.kill(p.pid, signal.SIGCONT)
                paused.remove(i)
        if not witnesses and ac and first.memory_free() >= 18:
            for i in range(9, 17):
                if len(live_joint) + len(active) >= 3:
                    break
                if i in jobs or (
                    i % 2 == 0 and (i - 1 not in closed or any(j % 2 == 0 for j in active))
                ):
                    continue
                if shutil.disk_usage(first.RUN).free < cfg["minimum_free_disk_GB"] * 1e9:
                    raise RuntimeError("registered disk minimum reached")
                first.save(
                    AREA / f"early-admission-{i}.json",
                    dict(
                        ordinal=i,
                        at_unix_s=time.time(),
                        registration_sha256=first.digest(REG),
                        earlier_native_started_ordinals=list(range(1, 9)),
                        earlier_live=sorted(live_joint),
                        previous_corridor_ordinals=list(jobs),
                        earlier_cumulative_wall_plus_corridor_s=work,
                    ),
                )
                jobs[i] = corridor.admit(i, 8, len(jobs))
                active[i] = jobs[i]
        first.save(
            overlap_ledger,
            dict(
                updated_unix_s=time.time(),
                earlier_live=live_joint,
                earlier_charged=8,
                early_corridor_ordinals=list(jobs),
                early_active={i: p.pid for i, p in active.items()},
                verified_native_observations=seen,
                verified_failure_ordinals=sorted(witnesses),
                closed_corridor_budgets=closed,
                combined_charged_or_reserved=8 + len(jobs),
                combined_live_native_worker_count=len(live_joint) + len(active),
                combined_actual_or_conservative_worker_wall_s=work,
                previous_registration_sha256=first.digest(corridor.REG),
                this_administration_sha256=first.digest(REG),
            ),
        )
        time.sleep(4)
    joint = first.read(merge)
    expected = {
        "elapsed_s": joint["charged_cumulative_wall_s"],
        "started_checks": joint["native_total_started"],
        "active": None,
    }
    if (
        expected != first.read(first.RUN / "execution-budget.json")
        or joint["native_total_started"] != 8
    ):
        raise RuntimeError("reconciliation must include every earlier native slot")
    first.save(
        AREA / "adoption-receipt.json",
        dict(
            at_unix_s=time.time(),
            earlier_native_budget=expected,
            prior_admitted_ordinals=list(jobs),
            same_process_ids={i: p.pid for i, p in jobs.items()},
            earlier_merge_sha256=first.digest(merge),
        ),
    )
    handle(2, native, exp, cfg, rows, already_admitted=jobs)
    corridor.verify()
    corridor.execute_arm(3, native, exp, cfg, rows)
    consolidated = native.summarize(first.RUN)
    skips = native.read_execution_skips(first.RUN, exp) or {"skipped_cases": []}
    if consolidated["completed_checks"] + len(skips["skipped_cases"]) != 24:
        raise RuntimeError("final original 24-slot reconciliation failed")
    first.save(
        corridor.AREA / "complete-registration.json",
        dict(
            original_declared=24,
            completed_native=consolidated["completed_checks"],
            skipped_by_verified_core_failure=len(skips["skipped_cases"]),
            total_global_budget=first.read(first.RUN / "execution-budget.json"),
            native_summary_sha256=first.digest(first.RUN / "summary.json"),
            all_original_frozen_arms=True,
            every_declared_slot_is_an_actual_simulation=not skips["skipped_cases"],
            pipeline_registration_sha256=first.digest(REG),
        ),
    )


if __name__ == "__main__":
    main()
