"""Independent roof demolition plus whole-parent-cell vegetation clearance.

This opt-in engine keeps the original roof engine unchanged. Vegetation actions
remove existing covered fuel from complete 5 m parent cells, never roof fragments
or partial homogeneous fractions. Construction area charges the full selected
parent footprint; covered-fuel loss is recorded on its separate physical basis.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from numbers import Integral
from typing import ClassVar

import numpy as np

from pyfireca.planning.contracts import require_id
from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.interventions.clearing import EXTENSIVE, InterventionConflict
from pyfireca.village.domain import ExposureEdges
from pyfireca.village.physics_v3 import V3CellPhysicsArrays


@dataclass(frozen=True, slots=True)
class RoofJointPlan(BuildingDemolitionPlan):
    """Whole buildings and unique complete physical-parent vegetation cells.

    Repeated cell IDs denote the same intervention and are canonicalized once.
    IDs are row-major indices on the original geographic grid, not indices on
    the 1 x N thermal storage grid. Clearing is hypothetical inert ground.
    """

    cleared_physical_cell_ids: tuple[int, ...] = ()

    def __post_init__(self):
        BuildingDemolitionPlan.__post_init__(self)
        if not isinstance(self.cleared_physical_cell_ids, tuple) or any(
            isinstance(i, bool) or not isinstance(i, Integral) or i < 0
            for i in self.cleared_physical_cell_ids
        ):
            raise ValueError("cleared_physical_cell_ids must be a tuple of nonnegative integers")
        cells = tuple(sorted({int(i) for i in self.cleared_physical_cell_ids}))
        object.__setattr__(self, "cleared_physical_cell_ids", cells)
        if cells:
            require_id("ground_evidence", self.ground_evidence)


class RoofJointEvaluationEngine(RoofHeritageEvaluationEngine):
    """Run joint interventions on the same roof layout and physical clocks."""

    capabilities: ClassVar[dict] = {
        **RoofHeritageEvaluationEngine.capabilities,
        "engine": "heritage_roof_joint_whole_parent_vegetation_ca_v1",
        "whole_parent_vegetation_cells": True,
        "vegetation_parent_cell_size_m": 5.0,
        "partial_vegetation_cell_clearing": False,
    }

    def _vegetation_nodes(self, scenario, plan):
        parent = self.bundle.request
        selected = plan.cleared_physical_cell_ids
        if not selected:
            return np.array([], dtype=int)
        if parent.grid.cell_size_m != 5.0:
            raise InterventionConflict("joint vegetation clearance requires the original 5 m grid")
        total_cells = parent.grid.width * parent.grid.height
        if any(i >= total_cells for i in selected):
            raise InterventionConflict("vegetation cell is missing from the physical parent grid")
        roof_cells = {
            int(c)
            for c, owners in zip(self.layout.cell_id, self.layout.owners, strict=True)
            if owners
        }
        source_cells = set()
        for ignition in scenario.ignitions:
            row, col = parent.grid.locate(*ignition.point_m)
            source_cells.add(parent.grid.cell_id(row, col))
        if source_cells.intersection(selected):
            raise InterventionConflict("vegetation clearance cannot remove an actual ignition cell")
        if roof_cells.intersection(selected):
            raise InterventionConflict("vegetation clearance cannot include any roof or core cell")
        eligible = (
            parent.domain_mask
            & parent.known_mask
            & parent.burnable_mask
            & (parent.building_index < 0)
            & (parent.parameters.covered_area_m2 > 0)
        ).ravel()
        if any(not eligible[i] for i in selected):
            raise InterventionConflict(
                "vegetation cells must be known burnable nonroof parent cells"
            )
        nodes = np.flatnonzero(np.isin(self.layout.cell_id, selected))
        # The current nonroof contract is exactly one unchanged parent node.
        # Reject an incompatible future partition rather than clearing a piece.
        if len(nodes) != len(selected) or any(self.layout.owners[n] for n in nodes):
            raise InterventionConflict(
                "vegetation clearance requires complete unsplit parent cells"
            )
        return nodes

    def prepare(self, scenario, plan):
        if not isinstance(scenario, EvaluationScenario) or not isinstance(plan, RoofJointPlan):
            raise TypeError("joint evaluation requires EvaluationScenario and RoofJointPlan")
        nodes = self._vegetation_nodes(scenario, plan)
        layout, contact, ledger = super().prepare(scenario, plan)
        request = layout.node_request
        baseline = layout.node_inputs.parameters
        roof_clear = (np.asarray(baseline.covered_area_m2).ravel() > 0) & (
            request.parameters.covered_area_m2.ravel() == 0
        )
        roof_area = float(baseline.covered_area_m2.ravel()[roof_clear].sum())
        roof_dry = float(ledger["removed_dry_fuel_kg"])
        roof_pyro = float(baseline.pyrolysable_dry_fuel_mass_kg.ravel()[roof_clear].sum())
        roof_energy = float(ledger["removed_available_energy_J"])
        grass_area = float(request.parameters.covered_area_m2.ravel()[nodes].sum())
        grass_dry = float(request.parameters.initial_dry_fuel_mass_kg.ravel()[nodes].sum())
        grass_pyro = float(request.parameters.pyrolysable_dry_fuel_mass_kg.ravel()[nodes].sum())
        grass_energy = float(request.parameters.available_energy_J.ravel()[nodes].sum())
        parent = self.bundle.request
        ground_area = len(plan.cleared_physical_cell_ids) * parent.grid.cell_area_m2
        roof_geometry_area = float(ledger["cleared_roof_union_area_m2"])
        proof = []
        for node in nodes:
            cell = int(layout.cell_id[node])
            row, col = divmod(cell, parent.grid.width)
            x, y = parent.grid.center(row, col)
            half = parent.grid.cell_size_m / 2
            proof.append(
                {
                    "physical_cell_id": cell,
                    "row": row,
                    "col": col,
                    "baseline_node_id": int(node),
                    "ground_bounds_m": [x - half, y - half, x + half, y + half],
                    "action_ground_footprint_area_m2": parent.grid.cell_area_m2,
                    "covered_fuel_area_m2": float(request.parameters.covered_area_m2.ravel()[node]),
                    "domain": bool(parent.domain_mask[row, col]),
                    "known": bool(parent.known_mask[row, col]),
                    "burnable": bool(parent.burnable_mask[row, col]),
                    "parent_building_index": int(parent.building_index[row, col]),
                    "roof_owners": list(self.layout.owners[node]),
                    "landcover_index": int(parent.landcover_index[row, col]),
                    "covered_fuel_subcell_geometry_resolved": False,
                }
            )
        if len(nodes):
            fields = {
                name: getattr(request.parameters, name).copy()
                for name in V3CellPhysicsArrays.__dataclass_fields__
            }
            for name in EXTENSIVE:
                fields[name].ravel()[nodes] = 0
            masks = {
                name: getattr(request, name).copy()
                for name in ("burnable_mask", "source_mask", "target_mask", "piloted_ignition_mask")
            }
            for mask in masks.values():
                mask.ravel()[nodes] = False
            # The old graph still touches newly inert nodes. Bind a valid empty
            # placeholder, then rebuild before any solver can see this request.
            empty_edges = ExposureEdges(
                np.array([], np.int32),
                np.array([], np.int32),
                np.array([], float),
                len(layout.owners),
            )
            request = replace(
                request,
                parameters=V3CellPhysicsArrays(**fields),
                exposure_edges=empty_edges,
                **masks,
            )
            layout = replace(layout, node_request=request)
            graph = self.radiation_builder(
                layout, self.transport, max_edges=self.max_radiation_edges
            )
            layout = replace(layout, node_request=replace(request, exposure_edges=graph))
            contact = self.contact_builder(layout)
        ledger.update(
            kind="whole_building_and_parent_vegetation_clearance",
            cleared_physical_cell_ids=list(plan.cleared_physical_cell_ids),
            cleared_vegetation_parent_cell_count=len(plan.cleared_physical_cell_ids),
            vegetation_parent_cell_footprint_area_m2=ground_area,
            cleared_vegetation_ground_footprint_area_m2=ground_area,
            cleared_roof_geometry_union_area_m2=roof_geometry_area,
            cleared_action_geometry_union_area_m2=roof_geometry_area + ground_area,
            cleared_roof_covered_area_m2=roof_area,
            cleared_vegetation_covered_area_m2=grass_area,
            cleared_total_union_area_m2=roof_area + grass_area,
            cleared_total_covered_area_m2=roof_area + grass_area,
            cleared_total_union_area_m2_basis="covered_fuel_not_construction_footprint",
            removed_roof_dry_fuel_kg=roof_dry,
            removed_vegetation_dry_fuel_kg=grass_dry,
            removed_dry_fuel_kg=roof_dry + grass_dry,
            removed_roof_pyrolysable_fuel_kg=roof_pyro,
            removed_vegetation_pyrolysable_fuel_kg=grass_pyro,
            removed_pyrolysable_fuel_kg=roof_pyro + grass_pyro,
            removed_roof_available_energy_J=roof_energy,
            removed_vegetation_available_energy_J=grass_energy,
            removed_available_energy_J=roof_energy + grass_energy,
            joint_area_basis="original_physical_covered_area;disjoint_roof_and_nonroof_nodes",
            action_cost_area_basis=(
                "full_selected_parent_ground_footprints_plus_removed_roof_geometry_union;"
                "disjoint_actions;never_add_covered_fuel_to_ground_footprint"
            ),
            vegetation_parent_cell_eligibility_proof=proof,
            parent_case_sha256=self.case_sha256,
            vegetation_post_clearance="inert;original_landcover_label_retained_as_provenance",
            vegetation_area_policy=(
                "construction_cost_uses_whole_parent_footprint;fire_loss_uses_covered_fuel;"
                "no_contiguous_subcell_grass_polygon_inferred"
            ),
            vegetation_partial_cell_policy="whole_original_parent_cells_only",
        )
        return layout, contact, ledger
