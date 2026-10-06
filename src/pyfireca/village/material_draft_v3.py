"""Reversible classification proposals, deliberately separate from solver inputs."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import numpy as np


class MaterialDraft:
    def __init__(self, case_dir: Path, output_root: Path):
        manifest = json.loads((case_dir / "manifest.json").read_text())
        self.material = np.load(case_dir / "material_index.npy", allow_pickle=False)
        self.domain = np.load(case_dir / "domain_mask.npy", allow_pickle=False)
        self.labels = manifest.get("material_labels", {})
        self.codes = set(int(v) for v in np.unique(self.material[self.domain]))
        digest = hashlib.sha256()
        for path in [*sorted(case_dir.glob("*.npy")), case_dir / "manifest.json"]:
            digest.update(path.name.encode())
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        self.base_hash = digest.hexdigest()
        self.path = output_root / "material-drafts" / f"{self.base_hash}.json"

    def read(self):
        record = {"schema": "material-draft-v1", "base_sha256": self.base_hash, "events": []}
        if self.path.exists():
            record = json.loads(self.path.read_text())
            if (
                record.get("base_sha256") != self.base_hash
                or record.get("schema") != "material-draft-v1"
            ):
                raise ValueError("修正草稿与基础案例不匹配")
        active = []
        for event in record["events"]:
            if event["action"] == "set":
                active.append(event)
            elif event["action"] == "undo" and active and active[-1]["id"] == event["target"]:
                active.pop()
            else:
                raise ValueError("修正记录损坏, 不能继续修改")
        return record, active

    def view(self):
        record, active = self.read()
        edits = {}
        for event in active:
            edits[(event["row"], event["col"])] = event
        return {
            "revision": len(record["events"]),
            "base_sha256": self.base_hash,
            "edits": [
                e for e in edits.values() if e["after"] != int(self.material[e["row"], e["col"]])
            ],
            "events": record["events"],
            "can_undo": bool(active),
            "materials": [
                {"code": c, "label": self.labels.get(str(c), str(c))} for c in sorted(self.codes)
            ],
            "applied_to_simulation": False,
        }

    def change(self, payload):
        if payload.keys() - {"revision", "action", "row", "col", "material", "reason"}:
            raise ValueError("修正请求包含不支持的字段")
        record, active = self.read()
        if type(payload.get("revision")) is not int or payload["revision"] != len(record["events"]):
            raise ValueError("草稿已更新, 请刷新后重试")
        action = payload.get("action")
        event = {
            "id": uuid.uuid4().hex,
            "action": action,
            "saved_at": datetime.now(UTC).isoformat(),
        }
        if action == "undo":
            if not active:
                raise ValueError("没有可撤销的修正")
            event["target"] = active[-1]["id"]
        elif action == "set":
            row, col, code = (payload.get(k) for k in ("row", "col", "material"))
            if any(type(v) is not int for v in (row, col, code)):
                raise ValueError("行列和材质编号必须是整数")
            if (
                not (0 <= row < self.domain.shape[0] and 0 <= col < self.domain.shape[1])
                or not self.domain[row, col]
            ):
                raise ValueError("只能修正计算域内的原胞")
            if code not in self.codes:
                raise ValueError("请选择案例中已有的材质")
            reason = payload.get("reason")
            if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
                raise ValueError("请填写1至500字的修正依据; 试验假设也需明确标注")
            before = int(self.material[row, col])
            for previous in active:
                if (previous["row"], previous["col"]) == (row, col):
                    before = previous["after"]
            if code == before:
                raise ValueError("所选材质与当前草稿相同")
            if len(active) >= 500:
                raise ValueError("单份草稿最多保留500次未撤销修正")
            event.update(
                row=row,
                col=col,
                before=before,
                after=code,
                reason=reason.strip(),
                evidence="user_proposal_not_field_verified",
            )
        else:
            raise ValueError("不支持的修正操作")
        record["events"].append(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return self.view()


def apply_material_edits(request, edits, transport, *, scenario=None):
    """Reuse verified homogeneous profiles; rebuild radiation, preserving the parent."""
    from dataclasses import replace

    from pyfireca.village.physics_v3 import V3CellPhysicsArrays
    from pyfireca.village.transport_v3 import build_v3_radiative_transport_edges

    if transport.wind_speed_m_s != 0 or transport.flame_tilt_coefficient_s_m != 0:
        raise ValueError("材质修正当前仅支持静风点辐射案例")
    extensive = {
        "covered_area_m2",
        "initial_dry_fuel_mass_kg",
        "live_dry_fuel_mass_kg",
        "dead_dry_fuel_mass_kg",
        "pyrolysable_dry_fuel_mass_kg",
        "live_water_mass_kg",
        "dead_water_mass_kg",
        "water_mass_kg",
        "peak_hrr_W",
        "effective_heated_dry_mass_kg",
        "receiver_area_m2",
        "active_surface_area_m2",
        "available_energy_J",
        "configured_curve_energy_J",
    }
    ground = request.ground_elevation_m
    if ground is None:
        ground = np.zeros(request.grid.shape)

    # Current core graph uses transparent paths and absolute point heights.
    def graph(physics, source, target):
        return build_v3_radiative_transport_edges(
            request.grid,
            source,
            target,
            physics.receiver_area_m2,
            physics.source_height_m + 2 * ground,
            physics.receiver_height_m + ground,
            np.ones(request.grid.shape),
            transport,
            max_edge_count=6_000_000,
            max_memory_mb=2048,
        )

    original = request.parameters
    verified = graph(original, request.source_mask, request.target_mask)
    for name in ("source", "target", "weight"):
        if not np.array_equal(getattr(verified, name), getattr(request.exposure_edges, name)):
            raise ValueError("传热连接与基础案例不一致, 不应用修正")
    del verified
    values = {
        name: getattr(original, name).copy() for name in V3CellPhysicsArrays.__dataclass_fields__
    }
    material = request.material_index.copy()
    landcover = request.landcover_index.copy()
    known = request.known_mask.copy()
    evidence = (
        request.material_evidence_index.copy()
        if request.material_evidence_index is not None
        else np.where(request.domain_mask, 0, 255).astype(np.uint8)
    )
    for edit in edits:
        row, col, code = edit["row"], edit["col"], edit["after"]
        if not request.domain_mask[row, col]:
            raise ValueError("修正原胞位于域外")
        mask = (
            (request.material_index == code)
            & request.domain_mask
            & (original.coverage_fraction > 0)
        )
        ids = np.flatnonzero(mask)
        if not ids.size:
            raise ValueError("目标材质没有可复用的现有参数")
        donor = int(ids[0])
        coverage = float(original.coverage_fraction[row, col])
        if coverage <= 0:
            raise ValueError("原胞覆盖率为零, 请先提供覆盖率依据")
        donor_coverage = float(original.coverage_fraction.ravel()[donor])
        for name, array in values.items():
            if name == "coverage_fraction":
                continue
            source_values = getattr(original, name)
            normalized = (
                source_values[mask] / original.coverage_fraction[mask]
                if name in extensive
                else source_values[mask]
            )
            if not np.allclose(normalized, normalized[0], rtol=1e-10, atol=1e-8):
                raise ValueError(f"材质{code}存在不同参数组({name}), 无法自动选定")
            array[row, col] = source_values.ravel()[donor] * (
                coverage / donor_coverage if name in extensive else 1
            )
        material[row, col] = code
        landcover[row, col] = request.landcover_index.ravel()[donor]
        known[row, col] = True
        evidence[row, col] = 3
    physics = V3CellPhysicsArrays(**values)
    burnable = (physics.pyrolysable_dry_fuel_mass_kg > 0) & request.domain_mask
    target = request.target_mask | burnable
    return replace(
        request,
        parameters=physics,
        material_index=material,
        landcover_index=landcover,
        known_mask=known,
        burnable_mask=burnable,
        source_mask=burnable,
        target_mask=target,
        piloted_ignition_mask=burnable,
        material_evidence_index=evidence,
        exposure_edges=graph(physics, burnable, target),
        scenario=scenario or request.scenario,
    )
