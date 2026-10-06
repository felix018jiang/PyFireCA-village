"""Local V3 replay server with user-selected ignition and duration."""

from __future__ import annotations

import contextlib
import http.server
import json
import math
import re
import threading
import uuid
import webbrowser
from dataclasses import asdict, dataclass, field, replace
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np

from pyfireca.village.flame_contact_v3 import FlameContactParameters, load_flame_contact_parameters
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.interactive_components_v3 import (
    ComponentInteractiveContext,
    load_component_interactive_context,
    run_component_selection,
)
from pyfireca.village.material_draft_v3 import MaterialDraft, apply_material_edits
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import (
    load_v3_case_bundle,
    run_v3_case_bundle,
    write_v3_case_bundle,
)


@dataclass
class InteractiveV3State:
    case_dir: Path
    initial_viewer: Path
    output_root: Path
    burnable: np.ndarray
    grid: dict
    ignition: dict
    end_time_s: float
    flame_contact: FlameContactParameters | None = None
    component_context: ComponentInteractiveContext | None = None
    model: str = "effective_exposure_ca_v3"
    jobs: dict[str, dict] = field(default_factory=dict)
    active_job: str | None = None
    material_draft: MaterialDraft | None = None
    material_transport: V3RadiativeTransportParameters | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def _duration_and_cell(state: InteractiveV3State, payload: dict) -> tuple[int, int, float]:
    unknown = payload.keys() - {"row", "col", "end_time_s"}
    if unknown:
        raise ValueError(f"当前接口不支持这些参数: {', '.join(sorted(unknown))}")
    row, col = payload.get("row"), payload.get("col")
    duration = payload.get("end_time_s")
    if type(row) is not int or type(col) is not int:
        raise ValueError("起火点行列必须是整数")
    if not (0 <= row < state.burnable.shape[0] and 0 <= col < state.burnable.shape[1]):
        raise ValueError("起火点超出地图范围")
    if not state.burnable[row, col]:
        raise ValueError("起火点必须选在可燃格上。请选择建筑或植被区域")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise ValueError("模拟时长必须是数字")
    duration = float(duration)
    if not math.isfinite(duration) or not 1 <= duration <= 30000:
        raise ValueError("模拟时长须在 1 秒至 500 分钟之间")
    return row, col, duration


def _run_job(
    state: InteractiveV3State, job_id: str, row: int, col: int, duration: float, draft=None
) -> None:
    try:
        if state.component_context is not None:
            run_dir = run_component_selection(
                state.component_context,
                state.output_root / job_id / "run",
                row=row,
                col=col,
                end_time_s=duration,
            )
        else:
            case = load_v3_case_bundle(state.case_dir)
            scenario = replace(
                case.request.scenario,
                end_time_s=duration,
                ignitions=(VillageIgnitionEvent(0.0, row, col),),
            )
            if draft is not None:
                case = replace(
                    case,
                    request=apply_material_edits(
                        case.request, draft["edits"], state.material_transport, scenario=scenario
                    ),
                )
            interval = max(1.0, math.ceil(duration / 100))
            request = replace(
                case.request,
                scenario=scenario,
                sample_interval_s=interval,
                flame_contact=state.flame_contact or case.request.flame_contact,
            )
            metadata = dict(case.metadata)
            metadata["scenario_id"] = f"{metadata.get('scenario_id', 'v3')}-user-{job_id[:8]}"
            metadata["ignition_source"] = {
                "selection_method": "user_selected_cell",
                "row": row,
                "col": col,
                "cell_id": row * request.grid.width + col,
            }
            metadata["parent_case"] = str(state.case_dir)
            if draft is not None:
                metadata["material_corrections"] = {**draft, "applied_to_simulation": True}
                metadata["material_correction_policy"] = (
                    "reuse homogeneous parent profiles and coverage; "
                    "rebuild verified static point radiation; user proposals not field measurements"
                )
                metadata["hypothetically_filled_unknown_cell_count"] = int(
                    np.sum(request.material_evidence_index == 0)
                )
                metadata["classified_burnable_cell_count"] = int(request.burnable_mask.sum())
            job_root = state.output_root / job_id
            write_v3_case_bundle(
                request,
                job_root / "case",
                material_labels=case.material_labels,
                landcover_labels=case.landcover_labels,
                metadata=metadata,
            )

            def progress(time_s: float, end_s: float) -> None:
                with state.lock:
                    state.jobs[job_id] = {
                        "status": "running",
                        "simulated_time_s": time_s,
                        "end_time_s": end_s,
                        "progress_fraction": time_s / end_s,
                        "phase": "writing_results" if time_s >= end_s else "simulating",
                    }

            _, run_dir = run_v3_case_bundle(
                job_root / "case",
                job_root / "run",
                chunk_frames=4,
                progress_callback=progress,
            )
        with state.lock:
            state.jobs[job_id] = {
                "status": "completed",
                "url": f"/result/{job_id}/",
                "run_dir": str(run_dir),
            }
    except Exception as exc:
        with state.lock:
            state.jobs[job_id] = {"status": "failed", "error": str(exc)}
    finally:
        with state.lock:
            state.active_job = None


def _saved_runs(state: InteractiveV3State) -> list[dict]:
    """Discover completed replay metadata without reading simulation arrays."""
    with state.lock:
        statuses = {key: value["status"] for key, value in state.jobs.items()}
    candidates = [("initial", state.initial_viewer, "/")]
    if state.output_root.is_dir():
        candidates.extend(
            (p.name, p / "run/viewer", f"/result/{p.name}/")
            for p in state.output_root.iterdir()
            if re.fullmatch(r"[0-9a-f]{32}", p.name)
            and statuses.get(p.name, "completed") == "completed"
        )
    records = []
    for run_id, viewer, url in candidates:
        try:
            manifest_path = viewer / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            grid = json.loads((viewer / "grid.json").read_text(encoding="utf-8"))
            if manifest.get("run_status") != "completed" or manifest.get("model") != state.model:
                continue
            if any(grid.get(key) != value for key, value in state.grid.items()):
                continue
            scenario = manifest.get("scenario", {})
            ignition = scenario.get("ignition_source") or (
                scenario.get("ignition_events", [{}])[0] if scenario.get("ignition_events") else {}
            )
            records.append(
                {
                    "id": run_id,
                    "url": url,
                    "row": ignition.get("row", state.ignition["row"]),
                    "col": ignition.get("col", state.ignition["col"]),
                    "end_time_s": scenario.get("end_time_s", state.end_time_s),
                    "saved_at": manifest_path.stat().st_mtime,
                    "initial": run_id == "initial",
                    "material_correction_count": len(
                        (scenario.get("material_corrections") or {}).get("edits", [])
                    ),
                }
            )
        except (OSError, ValueError, TypeError, AttributeError, IndexError):
            continue
    initial = [record for record in records if record["initial"]]
    recent = sorted(
        (record for record in records if not record["initial"]),
        key=lambda record: record["saved_at"],
        reverse=True,
    )[:50]
    return initial + recent


def _handler(state: InteractiveV3State):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(state.initial_viewer), **kwargs)

        def _json(self, value: dict, status: int = 200) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/api/material-draft":
                if state.material_draft is None:
                    self._json({"error": "当前案例不支持材质草稿"}, 404)
                    return
                try:
                    with state.lock:
                        draft = state.material_draft.view()
                    self._json(draft)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    self._json({"error": str(exc)}, 400)
                return
            if path == "/api/runs":
                self._json({"runs": _saved_runs(state)})
                return
            if path == "/api/config":
                self._json(
                    {
                        "model": state.model,
                        "component_ignition_selection": state.component_context is not None,
                        "grid": state.grid,
                        "ignition": state.ignition,
                        "end_time_s": state.end_time_s,
                        "max_end_time_s": 30000,
                        "material_draft_application": state.material_transport is not None,
                    }
                )
                return
            if path.startswith("/api/jobs/"):
                job_id = path.removeprefix("/api/jobs/")
                with state.lock:
                    job = state.jobs.get(job_id)
                self._json(job or {"error": "作业不存在"}, 200 if job else 404)
                return
            parts = path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] == "result":
                job_id = parts[1]
                with state.lock:
                    job = state.jobs.get(job_id)
                viewer_dir = state.output_root / job_id / "run" / "viewer"
                if (
                    re.fullmatch(r"[0-9a-f]{32}", job_id) is None
                    or (job is not None and job["status"] != "completed")
                    or not (viewer_dir / "manifest.json").is_file()
                ):
                    self.send_error(404)
                    return
                self.directory = str(viewer_dir)
                relative = "/".join(parts[2:])
            else:
                self.directory = str(state.initial_viewer)
                relative = path.lstrip("/")
            if relative in {
                "",
                "index.html",
                "assets/app.js",
                "assets/styles.css",
                "assets/component-view.mjs",
                "assets/core-metrics.mjs",
                "assets/chunk-replay.mjs",
            }:
                name = (
                    "index.html" if relative in {"", "index.html"} else relative.rsplit("/", 1)[1]
                )
                resource = files("pyfireca.village.web").joinpath(name)
                content = resource.read_bytes()
                self.send_response(200)
                content_type = {
                    "index.html": "text/html",
                    "app.js": "text/javascript",
                    "styles.css": "text/css",
                    "component-view.mjs": "text/javascript",
                    "core-metrics.mjs": "text/javascript",
                    "chunk-replay.mjs": "text/javascript",
                }[name]
                self.send_header("Content-Type", f"{content_type}; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)
                return
            self.path = "/" + relative
            super().do_GET()

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            if path not in {"/api/run", "/api/material-draft"}:
                self.send_error(404)
                return
            origin = self.headers.get("Origin")
            if origin and origin != f"http://127.0.0.1:{self.server.server_port}":
                self._json({"error": "请求来源不匹配"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("请求内容过大或为空")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("请求格式错误")
                if path == "/api/material-draft":
                    if state.material_draft is None:
                        raise ValueError("当前案例不支持材质草稿")
                    with state.lock:
                        if state.active_job is not None:
                            self._json({"error": "请等待当前模拟完成后修改草稿"}, 409)
                            return
                        draft = state.material_draft.change(payload)
                    self._json(draft)
                    return
                draft = None
                use_draft = payload.pop("use_material_draft", False)
                revision = payload.pop("draft_revision", None)
                if type(use_draft) is not bool:
                    raise ValueError("草稿应用选项必须是布尔值")
                validation_state = state
                if use_draft:
                    if state.material_draft is None or state.material_transport is None:
                        raise ValueError("当前案例不支持应用材质修正")
                    with state.lock:
                        draft = state.material_draft.view()
                    if type(revision) is not int or revision != draft["revision"]:
                        raise ValueError("草稿版本已变化, 请刷新后重试")
                    if not draft["edits"]:
                        raise ValueError("草稿没有待应用修正")
                    burnable = state.burnable.copy()
                    for edit in draft["edits"]:
                        code = edit["after"]
                        mask = state.material_draft.material == code
                        burnable[edit["row"], edit["col"]] = bool(np.any(state.burnable[mask]))
                    validation_state = replace(state, burnable=burnable)
                elif revision is not None:
                    raise ValueError("未应用草稿时不能指定草稿版本")
                row, col, duration = _duration_and_cell(validation_state, payload)
            except (ValueError, json.JSONDecodeError) as exc:
                self._json({"error": str(exc)}, 400)
                return
            with state.lock:
                if state.active_job is not None:
                    self._json({"error": "已有模拟正在运行"}, 409)
                    return
                if (
                    draft is not None
                    and state.material_draft.view()["revision"] != draft["revision"]
                ):
                    self._json({"error": "草稿已变化, 请刷新后重试"}, 409)
                    return
                job_id = uuid.uuid4().hex
                state.active_job = job_id
                state.jobs[job_id] = {"status": "running"}
            threading.Thread(
                target=_run_job, args=(state, job_id, row, col, duration, draft), daemon=True
            ).start()
            self._json({"job_id": job_id}, 202)

    return Handler


def serve_interactive_v3(
    case_dir: str | Path,
    initial_run: str | Path,
    output_root: str | Path,
    *,
    port: int = 8812,
    open_browser: bool = True,
    flame_contact_config: str | Path | None = None,
    material_transport: V3RadiativeTransportParameters | None = None,
) -> None:
    case_dir = Path(case_dir).resolve()
    initial_viewer = Path(initial_run).resolve() / "viewer"
    if not (initial_viewer / "manifest.json").is_file():
        raise FileNotFoundError(f"回放结果不存在: {initial_viewer}")
    manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
    burnable = np.load(case_dir / "burnable_mask.npy", allow_pickle=False)
    if burnable.dtype != np.bool_ or burnable.shape != (
        manifest["grid"]["height"],
        manifest["grid"]["width"],
    ):
        raise ValueError("案例可燃格与网格不匹配")
    state = InteractiveV3State(
        case_dir=case_dir,
        initial_viewer=initial_viewer,
        output_root=Path(output_root).resolve(),
        burnable=burnable,
        grid=manifest["grid"],
        ignition=manifest["scenario"]["ignitions"][0],
        end_time_s=float(manifest["scenario"]["end_time_s"]),
        flame_contact=(
            load_flame_contact_parameters(flame_contact_config)
            if flame_contact_config is not None
            else None
        ),
        material_transport=material_transport,
    )
    state.output_root.mkdir(parents=True, exist_ok=True)
    state.material_draft = MaterialDraft(case_dir, state.output_root)
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), _handler(state)) as server:
        url = f"http://127.0.0.1:{port}/"
        print(f"Interactive V3 simulator: {url}", flush=True)
        if open_browser:
            webbrowser.open(url)
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()


def serve_interactive_components_v3(
    run_root: str | Path,
    output_root: str | Path,
    *,
    port: int = 8813,
    open_browser: bool = True,
    max_nodes: int = 20_000,
    max_radiation_edges: int = 500_000,
    max_viewer_memory_mib: int = 256,
) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("port must lie in [1, 65535]")
    context = load_component_interactive_context(
        run_root,
        max_nodes=max_nodes,
        max_radiation_edges=max_radiation_edges,
        max_viewer_memory_mib=max_viewer_memory_mib,
    )
    request = context.bundle.request
    state = InteractiveV3State(
        case_dir=context.run_root / "base-case",
        initial_viewer=context.run_root / "viewer",
        output_root=Path(output_root).resolve(),
        burnable=request.burnable_mask,
        grid=asdict(request.grid),
        ignition=asdict(request.scenario.ignitions[0]),
        end_time_s=request.scenario.end_time_s,
        component_context=context,
        model="component_effective_exposure_ca_v3",
    )
    state.output_root.mkdir(parents=True, exist_ok=True)
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), _handler(state)) as server:
        url = f"http://127.0.0.1:{port}/"
        print(f"Interactive component simulator: {url}", flush=True)
        if open_browser:
            webbrowser.open(url)
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()


__all__ = ["serve_interactive_components_v3", "serve_interactive_v3"]
