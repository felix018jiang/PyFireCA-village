"""Transfer already-issued independent campaign workers at the joint reconciliation boundary."""

import builtins
import dis
import fcntl
import importlib.util
import os
import signal
import subprocess
import time
import types
from pathlib import Path

import continue_heritage_registered_corridor_arms as corridor
import pipeline_heritage_registered_independent_arms as pipeline
import schedule_heritage_registered_joint_families as first

AREA = pipeline.AREA / "settlement-binding"
PIPE_PID = 52050
PIPE_COMMAND = "/opt/anaconda3/bin/python scripts/pipeline_heritage_registered_independent_arms.py"


def resolve_handler():
    prior = first.read(pipeline.REG)
    path = pipeline.AREA / "adopt_registered_processes.py"
    if (
        first.digest(path) != prior["adopted_handler_sha256"]
        or first.digest(corridor.REG) != prior["original_resource_registration_sha256"]
    ):
        raise RuntimeError("native settlement lineage changed")
    spec = importlib.util.spec_from_file_location("registered_worker_handoff", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def missing(code):
        names = {
            inst.argval
            for inst in dis.get_instructions(code)
            if inst.opname == "LOAD_GLOBAL"
            and inst.argval not in mod.__dict__
            and not hasattr(builtins, inst.argval)
        }
        for sub in (v for v in code.co_consts if isinstance(v, types.CodeType)):
            names |= missing(sub)
        return names

    if missing(mod.execute_arm.__code__) != {"REG"}:
        raise ValueError("unexpected unresolved frozen administrative names")
    mod.REG = corridor.REG
    if missing(mod.execute_arm.__code__):
        raise ValueError("the administrative adoption procedure still has unresolved names")
    return mod.execute_arm


class ExistingWorker:
    """The new coordinator can poll but cannot re-create a native process."""

    def __init__(self, ordinal, pid):
        self.ordinal = ordinal
        self.pid = pid
        self.command = f"/opt/anaconda3/bin/python {corridor.SCRIPT} --slot {ordinal}"

    def poll(self):
        if self.pid and first.ps(self.pid, "command") == self.command:
            return None
        b = first.read(corridor.folder(self.ordinal) / "execution-budget.json")
        if b["active"] is None:
            return 0 if b["started_checks"] == 1 else 1
        raise RuntimeError("adopted worker lost its identity before its native budget was closed")


def identify():
    active = {}
    for line in subprocess.check_output(["ps", "-Ao", "pid=,command="], text=True).splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2:
            continue
        for i in range(9, 17):
            if fields[1] == f"/opt/anaconda3/bin/python {corridor.SCRIPT} --slot {i}":
                if i in active:
                    raise RuntimeError("one ordinal has multiple native workers")
                active[i] = int(fields[0])
    jobs = {}
    for path in corridor.AREA.glob("slot-*"):
        i = int(path.name.split("-")[1])
        if not 9 <= i <= 16:
            raise RuntimeError("unanticipated next-arm admission")
        ticket = path / "admission.json"
        budget = path / "execution-budget.json"
        if not ticket.exists() or not budget.exists():
            return None
        if first.read(ticket)["registration_sha256"] != first.digest(corridor.REG):
            raise ValueError("native case ticket unbound")
        b = first.read(budget)
        if i in active:
            if b["active"] is not None and b["active"] != {"pid": active[i]}:
                raise RuntimeError("native budget and process identity disagree")
        elif (
            b["active"] is not None
            or b["started_checks"] != 1
            or not list(path.glob("evaluations/*/complete.json"))
        ):
            return None
        jobs[i] = ExistingWorker(i, active.get(i))
    if not set(active) <= set(jobs):
        return None
    if len(active) > 3:
        raise RuntimeError("campaign aggregate worker limit exceeded")
    return jobs


def main():
    corridor.verify()
    settle = resolve_handler()
    if first.ps(PIPE_PID, "command") != PIPE_COMMAND:
        raise RuntimeError("original overlap coordinator is not identified")
    AREA.mkdir(exist_ok=False)
    first.save(
        AREA / "registration.json",
        dict(
            at_unix_s=time.time(),
            prior_amendment_sha256=first.digest(pipeline.REG),
            versioned_successor_sha256=first.digest(Path(__file__)),
            original_unbound_settlement_name="REG",
            corrected_binding=str(corridor.REG),
            corrected_binding_sha256=first.digest(corridor.REG),
            settlement_source_unchanged=True,
            original_worker_pids_retained=True,
            additional_CA_slots=0,
            takeover_trigger=(
                "All 8 earlier native terminal artifacts exist, then transfer the single "
                "administrative writer lock. All inherited actual narrow workers are "
                "recognized by exact command, native budget and ticket."
            ),
        ),
    )
    native = first.frozen_native()
    exp, cfg, rows = first.tables(native)
    merge = first.AREA / "merged-registration-receipt.json"
    while not merge.exists():
        earlier = first.read(first.AREA / "ledger.json")
        if earlier["interruptions"]:
            raise RuntimeError("earlier arm interrupted; old schedule preserved")
        if len(earlier["published_ordinals"]) == 8:
            break
        time.sleep(2)
    prior_live = first.ps(PIPE_PID, "command") == PIPE_COMMAND
    if prior_live:
        os.kill(PIPE_PID, signal.SIGSTOP)
    for _ in range(12):
        jobs = identify()
        if jobs is not None:
            break
        if first.ps(PIPE_PID, "command") == PIPE_COMMAND:
            os.kill(PIPE_PID, signal.SIGCONT)
            time.sleep(1)
            os.kill(PIPE_PID, signal.SIGSTOP)
        time.sleep(0.1)
    if jobs is None:
        if prior_live and first.ps(PIPE_PID, "command") == PIPE_COMMAND:
            os.kill(PIPE_PID, signal.SIGCONT)
        raise RuntimeError("native issuance transition was not safe to hand over")
    if prior_live and first.ps(PIPE_PID, "command") == PIPE_COMMAND:
        os.kill(PIPE_PID, signal.SIGTERM)
        os.kill(PIPE_PID, signal.SIGCONT)
    lock = (corridor.AREA / "writer.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX)
    refreshed = identify()
    if refreshed is None or set(jobs) != set(refreshed):
        raise RuntimeError("native ordinal ownership changed during exclusive handoff")
    jobs = refreshed
    first.save(
        AREA / "exclusive-handoff.json",
        dict(
            at_unix_s=time.time(),
            old_controller=PIPE_PID,
            old_live_before_transfer=prior_live,
            actual_inherited_ordinals=sorted(jobs),
            actual_live_pids={i: p.pid for i, p in jobs.items() if p.poll() is None},
            previous_archives_unmodified=True,
            added_or_retried_existing_ordinal=False,
        ),
    )
    while not merge.exists():
        open_prior, wall, _ = pipeline.joint_state(time.time())
        for i, p in jobs.items():
            b = first.read(corridor.folder(i) / "execution-budget.json")
            wall += (
                b["elapsed_s"]
                if p.poll() is not None
                else time.time()
                - first.read(corridor.folder(i) / "admission.json")["unix_before_launch_s"]
            )
        if wall >= cfg["max_wall_time_s"] - 300:
            for pid in [
                *open_prior.values(),
                *(p.pid for p in jobs.values() if p.poll() is None),
            ]:
                os.kill(pid, signal.SIGCONT)
                os.kill(pid, signal.SIGINT)
            raise RuntimeError("aggregate wall guard")
        time.sleep(1)
    receipt = first.read(merge)
    if first.read(first.RUN / "execution-budget.json") != {
        "elapsed_s": receipt["charged_cumulative_wall_s"],
        "started_checks": 8,
        "active": None,
    }:
        raise RuntimeError("earlier joint global ledger has not reconciled")
    settle(2, native, exp, cfg, rows, already_admitted=jobs)
    corridor.verify()
    corridor.execute_arm(3, native, exp, cfg, rows)
    summary = native.summarize(first.RUN)
    skips = native.read_execution_skips(first.RUN, exp) or {"skipped_cases": []}
    if summary["completed_checks"] + len(skips["skipped_cases"]) != 24:
        raise RuntimeError("final original registration failed reconciliation")
    first.save(
        corridor.AREA / "complete-registration.json",
        dict(
            original_declared=24,
            completed_native=summary["completed_checks"],
            skipped_by_verified_core_failure=len(skips["skipped_cases"]),
            total_global_budget=first.read(first.RUN / "execution-budget.json"),
            native_summary_sha256=first.digest(first.RUN / "summary.json"),
            all_original_frozen_arms=True,
            every_declared_slot_is_an_actual_simulation=not skips["skipped_cases"],
            pipeline_registration_sha256=first.digest(pipeline.REG),
            settlement_binding_sha256=first.digest(AREA / "registration.json"),
        ),
    )


if __name__ == "__main__":
    main()
