"""Restore a component experiment for local, bounded ignition selection."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from pyfireca.village.component_curves_v3 import load_component_curve_configuration
from pyfireca.village.component_outputs_v3 import (
    check_component_viewer_memory,
    write_component_viewer,
)
from pyfireca.village.component_radiation_v3 import load_internal_radiation_parameters
from pyfireca.village.component_surfaces_v3 import load_receiver_surface_configuration
from pyfireca.village.components_v3 import (
    load_component_configuration,
    resolve_components,
    run_component_simulation,
    write_component_run,
)
from pyfireca.village.finite_radiation_v3 import load_finite_radiation_configuration
from pyfireca.village.flame_contact_v3 import load_flame_contact_parameters
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.workflow_v3 import V3CaseBundle, load_v3_case_bundle


@dataclass(frozen=True)
class ComponentInteractiveContext:
    run_root: Path
    bundle: V3CaseBundle
    configuration: object
    options: dict
    viewer_metadata: dict
    material_labels: dict
    landcover_labels: dict
    building_cells_bytes: bytes
    max_nodes: int
    max_radiation_edges: int
    max_viewer_memory_mib: int
    chunk_frames: int


def load_component_interactive_context(
    run_root: str | Path,
    *,
    max_nodes: int = 20_000,
    max_radiation_edges: int = 500_000,
    max_viewer_memory_mib: int = 256,
    chunk_frames: int = 4,
) -> ComponentInteractiveContext:
    for limit in (max_nodes, max_radiation_edges, max_viewer_memory_mib, chunk_frames):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("component interactive resource limits must be positive integers")
    root = Path(run_root).resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    viewer = json.loads((root / "viewer/manifest.json").read_text())
    if (
        manifest.get("schema_version") != "village-components-run-1"
        or viewer.get("schema_version") != "village-component-viewer-1"
    ):
        raise ValueError("component interactive simulation requires a component run and viewer")
    bundle = load_v3_case_bundle(root / "base-case")
    grid = asdict(bundle.request.grid)
    view_grid = json.loads((root / "viewer/grid.json").read_text())
    if manifest["physical_grid"] != grid or any(view_grid.get(k) != v for k, v in grid.items()):
        raise ValueError("component initial viewer must match the physical case grid")
    configuration = load_component_configuration(root / "components.resolved.yml")
    if configuration.to_mapping() != manifest["component_configuration"]:
        raise ValueError("component configuration does not match run manifest")
    options = {}
    for argument, key, filename, loader in (
        ("contact", "flame_contact", "flame-contact.resolved.yml", load_flame_contact_parameters),
        (
            "curves",
            "component_curves",
            "component-curves.resolved.yml",
            load_component_curve_configuration,
        ),
        (
            "internal_radiation",
            "internal_radiation",
            "internal-radiation.resolved.yml",
            load_internal_radiation_parameters,
        ),
        (
            "surfaces",
            "receiver_surfaces",
            "receiver-surfaces.resolved.yml",
            load_receiver_surface_configuration,
        ),
        (
            "finite_radiation",
            "finite_radiation",
            "finite-radiation.resolved.yml",
            load_finite_radiation_configuration,
        ),
    ):
        expected = manifest[key]
        path = root / filename
        if expected is None:
            if path.exists():
                raise ValueError(f"unexpected component configuration file: {filename}")
            options[argument] = None
        else:
            value = loader(path)
            if value.to_mapping() != expected:
                raise ValueError(f"component configuration does not match run: {filename}")
            options[argument] = value
    contact_mapping = (
        bundle.request.flame_contact.to_mapping() if bundle.request.flame_contact else None
    )
    if contact_mapping != manifest["flame_contact"]:
        raise ValueError("base-case contact must match component run")
    if manifest["node_count"] > max_nodes or manifest["radiation_edge_count"] > max_radiation_edges:
        raise ValueError("RESOURCE_LIMIT: initial component experiment exceeds interactive limits")
    cells_bytes = (root / "viewer/building_cells.json").read_bytes()
    groups = json.loads(cells_bytes)["buildings"]
    count = bundle.request.grid.height * bundle.request.grid.width
    if any(type(c) is not int or not 0 <= c < count for group in groups.values() for c in group):
        raise ValueError("component building mapping must match physical grid")
    return ComponentInteractiveContext(
        root,
        bundle,
        configuration,
        options,
        dict(viewer["scenario"]),
        {int(k): v for k, v in viewer["material"]["labels"].items()},
        {int(k): v for k, v in viewer["landcover"]["labels"].items()},
        cells_bytes,
        max_nodes,
        max_radiation_edges,
        max_viewer_memory_mib,
        chunk_frames,
    )


def run_component_selection(
    context: ComponentInteractiveContext,
    output_dir: str | Path,
    *,
    row: int,
    col: int,
    end_time_s: float,
) -> Path:
    original = context.bundle.request
    if type(row) is not int or type(col) is not int:
        raise ValueError("component ignition row and column must be integers")
    if not (0 <= row < original.grid.height and 0 <= col < original.grid.width):
        raise ValueError("component ignition is outside the physical grid")
    if not original.burnable_mask[row, col]:
        raise ValueError("component ignition requires a known burnable physical cell")
    if (
        isinstance(end_time_s, bool)
        or not isinstance(end_time_s, (int, float))
        or not math.isfinite(end_time_s)
        or not 1 <= end_time_s <= 30_000
    ):
        raise ValueError("component duration must be between 1 and 30000 seconds")
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(output)
    request = replace(
        original,
        scenario=replace(
            original.scenario,
            end_time_s=float(end_time_s),
            ignitions=(VillageIgnitionEvent(0, row, col),),
        ),
        sample_interval_s=max(1.0, math.ceil(end_time_s / 100)),
    )
    resolved = resolve_components(
        request,
        context.configuration,
        **context.options,
        max_nodes=context.max_nodes,
        max_radiation_edges=context.max_radiation_edges,
    )
    check_component_viewer_memory(resolved, context.max_viewer_memory_mib)
    result = run_component_simulation(resolved, retain_snapshots=True)
    metadata = {
        **context.viewer_metadata,
        "scenario_id": f"component-selected-{output.name}",
        "parent_component_run": str(context.run_root),
        "selection_method": "user_selected_physical_cell_lowest_receiver_only",
        "end_time_s": request.scenario.end_time_s,
        "sample_interval_s": request.sample_interval_s,
        "ignition_events": [{"row": row, "col": col, "time_s": 0}],
    }
    run_dir = write_component_run(
        resolved,
        result,
        output,
        material_labels=context.material_labels,
        landcover_labels=context.landcover_labels,
        scenario_metadata=metadata,
    )
    viewer = write_component_viewer(
        resolved,
        result,
        run_dir,
        prepared_dir=context.run_root / "viewer",
        material_labels=context.material_labels,
        landcover_labels=context.landcover_labels,
        scenario_metadata=metadata,
        chunk_frames=context.chunk_frames,
        max_memory_mib=context.max_viewer_memory_mib,
    )
    # The physical grid is unchanged. Retain original polygon IDs and overlaps;
    # a self-contained viewer does not include the prepared overlap CSV.
    (viewer / "building_cells.json").write_bytes(context.building_cells_bytes)
    return run_dir
