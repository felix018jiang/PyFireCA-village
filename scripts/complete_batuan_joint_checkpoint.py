"""Finish the already-running pilot: accept checks, summarize and archive locally.

Waits for confirmation/audit/rendering. No new simulations or research claims
are introduced. Refuses a version snapshot if the user changes HEAD meanwhile.
"""

# ruff: noqa: RUF001

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
import time
from pathlib import Path

from run_batuan_building_pairs import ROOT, dump, sha


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def finish(output, expected_head, finalizer_pid):
    started = time.monotonic()
    exited = None
    while True:
        ready = all(
            (output / name).exists() for name in ["report.json", "audit.json", "review/index.html"]
        )
        if (
            ready
            and (output / "review/index.html").stat().st_mtime
            >= (output / "audit.json").stat().st_mtime
        ):
            break
        try:
            os.kill(finalizer_pid, 0)
        except ProcessLookupError:
            exited = exited or time.monotonic()
        if (
            exited is not None and time.monotonic() - exited > 240
        ) or time.monotonic() - started > 21600:
            raise RuntimeError(
                "confirmation/audit/render did not finish; preserve all partial output"
            )
        time.sleep(5)
    audit = json.loads((output / "audit.json").read_text())
    report = json.loads((output / "report.json").read_text())
    if (
        not audit["all_protected_counts_match"]
        or audit["new_physical_solves_during_recovery"] != 0
        or not audit["training_prefetch_only"]
    ):
        raise RuntimeError("study checks do not support acceptance")
    if report["selection"] != json.loads((output / "selection.json").read_text()):
        raise RuntimeError("frozen selection changed")
    rows = []
    for block in report["confirmation"]:
        if not block["full_grid"]:
            continue
        records = block["records"]
        if len(records) != (8 if block["role"] == "training" else 4):
            raise RuntimeError("incomplete scenario block")
        for record in records:
            d = record["diagnostics"]
            if (
                not d["reached_requested_horizon"]
                or d["simulated_until_s"] < 5400 - 1e-8
                or record["metrics"]["burning_reached_domain_edge"]
            ):
                raise RuntimeError("full-domain comparison is incomplete or boundary affected")
        rows.append(
            {
                "label": block["label"],
                "role": block["role"],
                "pass_count": sum(x["metrics"]["ignited_structure_count"] == 0 for x in records),
                "scenario_count": len(records),
                "area_m2": records[0]["intervention"]["cleared_area_m2"],
                "zone_areas_m2": records[0]["intervention"]["zone_areas_m2"],
                "scenarios": [
                    {
                        "scenario_id": x["scenario_id"],
                        "house_count": x["metrics"]["ignited_house_count"],
                        "bridge_count": x["metrics"]["ignited_bridge_count"],
                        "actual_horizon_s": x["diagnostics"]["simulated_until_s"],
                    }
                    for x in records
                ],
            }
        )
    counts = {
        m: len(json.loads((output / (m + "-search.json")).read_text())["records"])
        for m in ["outer_proxy", "inner_proxy", "joint"]
    }
    summary = {
        "schema": "batuan-joint-pilot-summary-1",
        "rows": rows,
        "requests": counts,
        "protocol_sha256": sha(output / "protocol.json"),
        "selection_sha256": sha(output / "selection.json"),
        "audit_sha256": sha(output / "audit.json"),
        "actual_ca_solves": audit["actual_ca_solves"],
        "scope": "bounded fixed-material model study; not field calibration or global optimum",
    }
    data = ROOT / "docs/results/batuan-joint-pilot-20261017.json"
    prose = ROOT / "docs/BATUAN_JOINT_PILOT_RESULTS_20261017.md"
    if data.exists() or prose.exists():
        raise FileExistsError("result documents already exist")
    dump(data, summary)
    titles = {
        "baseline": "无新增清理",
        "outer_proxy": "仅村外",
        "inner_proxy": "仅村内",
        "joint": "联合搜索",
        "independent_sweep": "固定几何分配扫描",
        "historical_combined": "原始内外同时",
    }
    lines = [
        "# 岜团村联合清理原型：完整复算结果",
        "",
        "日期：2026-10-17。先冻结设计集方案，再完整复算8项设计和4项留出情景；未依据留出结果重选。",
        "",
        "| 方案 | 设计通过 | 留出通过 | 清理㎡ | 村外 / 近邻㎡ |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        if row["role"] != "training":
            continue
        held = next(x for x in rows if x["label"] == row["label"] and x["role"] == "held_out")
        zones = row["zone_areas_m2"]
        lines.append(
            f"| {titles[row['label']]} | {row['pass_count']}/8 | {held['pass_count']}/4 | "
            f"{row['area_m2']:.2f} | {zones['outer_proxy']:.2f} / {zones['inner_proxy']:.2f} |"
        )
    baseline = next(x for x in rows if x["label"] == "baseline" and x["role"] == "held_out")
    joint = next(x for x in rows if x["label"] == "joint" and x["role"] == "training")
    zones = joint["zone_areas_m2"]
    lines.extend(
        [
            "",
            f"实际候选请求：{counts}。这是有限预算内找到的方案，不是连续全局最优证明。",
            f"联合方案分配为村外{zones['outer_proxy']:.2f}㎡、近邻{zones['inner_proxy']:.2f}㎡。"
            "允许退化为单侧解，不预设混合投入占优；本轮条件交替搜索尚需候选束、重启和预算收敛检验。",
            "",
            f"无干预在留出集通过{baseline['pass_count']}/4。原本无损的情景不能作为措施新增保护的证据；上表保留全部预设情景。",
            "",
            "全部确认达到90分钟（使用求解器1e-8秒终点容差），未点燃计算边界；裁剪与完整场地的民居/桥引燃数一致。"
            "缓存恢复没有重新运行CA；实际清理范围未侵占人工建筑轮廓。审计记录保存在运行目录的audit.json。",
            "",
            "本轮固定等效合成木材与水分，区界是20米代理，地面处理不产生不透明墙。"
            "5米格网尚不能独立解析最窄街巷，不能将本轮输出解释为实地安全宽度或施工标准。",
            "",
            "下一步优先局部细网格和实测可用空间，再增加完整村落内部起火、等面积前沿、多种木材及跨村验证。",
            "",
            "研究方法与建筑对结果见 [首轮研究说明](BATUAN_JOINT_PILOT_20261017.md)。",
            "完整记录：`runs/research/batuan-joint-20261017-r2/report.json`；网页报告：`review/index.html`。",
        ]
    )
    prose.write_text("\n".join(lines) + "\n")
    if git("rev-parse", "HEAD") != expected_head:
        raise RuntimeError("HEAD changed; results saved; automatic version snapshot skipped")
    paths = [str(p.relative_to(ROOT)) for p in [data, prose]]
    subprocess.run(["git", "add", *paths], cwd=ROOT, check=True)
    subprocess.run(
        [
            "git",
            "commit",
            "--only",
            *paths,
            "-m",
            "research: accept bounded Batuan joint pilot and independent confirmation",
        ],
        cwd=ROOT,
        check=True,
    )
    tag = "batuan-joint-pilot-20261017"
    commit = git("rev-parse", "HEAD")
    backup = ROOT.parent / "PyFireCA-backups" / tag
    backup.mkdir(parents=True, exist_ok=False)
    with (backup / "bundle-validation.log").open("w") as stream:
        subprocess.run(
            ["git", "bundle", "create", str(backup / "repository.bundle"), "--all"],
            cwd=ROOT,
            check=True,
            stdout=stream,
            stderr=stream,
        )
        subprocess.run(
            ["git", "bundle", "verify", str(backup / "repository.bundle")],
            cwd=ROOT,
            check=True,
            stdout=stream,
            stderr=stream,
        )
    folders = [output, ROOT / "runs/research/batuan-pair-channels-20261017-r1"]
    files = [
        p
        for folder in folders
        for p in folder.rglob("*")
        if p.is_file()
        and p.suffix not in (".lock", ".tmp")
        and p.name not in ("completion.json", "checkpoint-error.json")
    ]
    manifest = {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)}
    archive = backup / "joint-and-channel-experiments.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for p in sorted(files):
            tar.add(p, arcname=str(p.relative_to(ROOT)), recursive=False)
    with tarfile.open(archive) as tar:
        for name, value in manifest.items():
            if hashlib.sha256(tar.extractfile(name).read()).hexdigest() != value:
                raise RuntimeError("archive verification failed")
    # Publish the completion tag only after validating the experiment archive.
    subprocess.run(
        [
            "git",
            "tag",
            "-a",
            tag,
            "-m",
            "Completed scoped fixed-material pilot; no field safety standard claimed",
        ],
        cwd=ROOT,
        check=True,
    )
    refreshed = backup / "repository.with-tag.bundle"
    with (backup / "bundle-validation.log").open("a") as stream:
        subprocess.run(
            ["git", "bundle", "create", str(refreshed), "--all"],
            cwd=ROOT,
            check=True,
            stdout=stream,
            stderr=stream,
        )
        subprocess.run(
            ["git", "bundle", "verify", str(refreshed)],
            cwd=ROOT,
            check=True,
            stdout=stream,
            stderr=stream,
        )
    refreshed.replace(backup / "repository.bundle")
    validation = {
        "commit": commit,
        "tag": tag,
        "archive_sha256": sha(archive),
        "repository_bundle_sha256": sha(backup / "repository.bundle"),
        "verified_files": len(manifest),
        "files": manifest,
        "building_pair_backup": str(
            ROOT.parent / "PyFireCA-backups/batuan-joint-prototype-runtime-20261017"
        ),
        "rollback_backup": str(
            ROOT.parent / "PyFireCA-backups/batuan-before-joint-firebreaks-20261017"
        ),
    }
    dump(backup / "backup-validation.json", validation)
    dump(
        output / "completion.json",
        {
            "status": "study_complete",
            "commit": commit,
            "tag": tag,
            "backup": str(backup),
            "archive_verified": True,
            "result_summary": str(data),
            "full_material_and_field_validation_claimed": False,
        },
    )
    print(
        json.dumps({"complete": str(output), "commit": commit, "backup": str(backup)}), flush=True
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--finalizer-pid", type=int, required=True)
    args = parser.parse_args()
    directory = args.output.resolve()
    try:
        finish(directory, args.expected_head, args.finalizer_pid)
    except Exception as error:
        dump(
            directory / "checkpoint-error.json", {"status": "needs_attention", "error": str(error)}
        )
        raise
