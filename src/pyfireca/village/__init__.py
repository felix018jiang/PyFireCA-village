"""Two-dimensional village-fire cellular automata."""

from pyfireca.village.assembly_presets import (
    AssemblyPreset,
    AssemblyPresetLibrary,
    load_assembly_preset_library,
)
from pyfireca.village.domain import (
    CellParameterArrays,
    ExposureEdges,
    PreparedVillageDomain,
    VillageGridSpec,
)
from pyfireca.village.gaoyou_v3 import (
    LANDCOVER_LABELS,
    RGB_HYPOTHESIS_LANDCOVER_LABELS,
    build_gaoyou_v3_case,
)
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.inspection_v3 import inspect_v3_run
from pyfireca.village.material_cards import (
    MaterialCard,
    MaterialCardLibrary,
    MaterialCardSource,
    load_material_card_library,
    parse_material_card_library,
)
from pyfireca.village.material_resolution import (
    ResolutionIssue,
    ResolvedMaterialRecord,
    ResolvedSurveyCase,
    resolve_material_selections,
)
from pyfireca.village.moisture import (
    MoistureInitialization,
    MoistureState,
    load_moisture_initialization,
)
from pyfireca.village.physics_v2 import V2CellPhysicsArrays
from pyfireca.village.preflight_v3 import preflight_v3_case
from pyfireca.village.prepared_workflow_v2 import (
    PreparedV2Case,
    load_prepared_v2_case,
    run_prepared_v2_case,
)
from pyfireca.village.resolution_workflow_v3 import load_geometry_areas, resolve_survey_v3
from pyfireca.village.resource_sensitivity_v3 import build_resource_sensitivity_report
from pyfireca.village.rgb_hypothesis_v3 import complete_rgb_hypothesis_spatial
from pyfireca.village.solver import (
    VillageScenario,
    VillageSimulationRequest,
    VillageSimulationResult,
    run_village_simulation,
)
from pyfireca.village.solver_v2 import (
    V2Scenario,
    V2SimulationRequest,
    V2SimulationResult,
    run_v2_simulation,
)
from pyfireca.village.solver_v3 import (
    V3EnergyBudget,
    V3Scenario,
    V3SimulationRequest,
    V3SimulationResult,
    V3Snapshot,
    V3State,
    run_v3_simulation,
)
from pyfireca.village.state import VillageCellState, VillageState
from pyfireca.village.survey import (
    FieldSurvey,
    FieldSurveyRecord,
    load_field_survey,
    parse_field_survey,
)
from pyfireca.village.survey_adapter import (
    M03SpatialAdapterResult,
    adapt_resolved_survey_to_v03,
)
from pyfireca.village.survey_mapping_v3 import validate_unknown_mapping_v3
from pyfireca.village.transport_v2 import (
    V2RadiativeTransportParameters,
    build_v2_radiative_transport_edges,
)
from pyfireca.village.unknown_review_v3 import review_unknown_v3
from pyfireca.village.workflow_v2 import load_v2_file_case, run_v2_file_case
from pyfireca.village.workflow_v3 import (
    V3CaseBundle,
    crop_v3_case_bundle,
    load_v3_case_bundle,
    run_v3_case_bundle,
    write_v3_case_bundle,
)

__all__ = [
    "LANDCOVER_LABELS",
    "RGB_HYPOTHESIS_LANDCOVER_LABELS",
    "AssemblyPreset",
    "AssemblyPresetLibrary",
    "CellParameterArrays",
    "ExposureEdges",
    "FieldSurvey",
    "FieldSurveyRecord",
    "M03SpatialAdapterResult",
    "MaterialCard",
    "MaterialCardLibrary",
    "MaterialCardSource",
    "MoistureInitialization",
    "MoistureState",
    "PreparedV2Case",
    "PreparedVillageDomain",
    "ResolutionIssue",
    "ResolvedMaterialRecord",
    "ResolvedSurveyCase",
    "V2CellPhysicsArrays",
    "V2RadiativeTransportParameters",
    "V2Scenario",
    "V2SimulationRequest",
    "V2SimulationResult",
    "V3CaseBundle",
    "V3EnergyBudget",
    "V3Scenario",
    "V3SimulationRequest",
    "V3SimulationResult",
    "V3Snapshot",
    "V3State",
    "VillageCellState",
    "VillageGridSpec",
    "VillageIgnitionEvent",
    "VillageScenario",
    "VillageSimulationRequest",
    "VillageSimulationResult",
    "VillageState",
    "adapt_resolved_survey_to_v03",
    "build_gaoyou_v3_case",
    "build_resource_sensitivity_report",
    "build_v2_radiative_transport_edges",
    "complete_rgb_hypothesis_spatial",
    "crop_v3_case_bundle",
    "inspect_v3_run",
    "load_assembly_preset_library",
    "load_field_survey",
    "load_geometry_areas",
    "load_material_card_library",
    "load_moisture_initialization",
    "load_prepared_v2_case",
    "load_v2_file_case",
    "load_v3_case_bundle",
    "parse_field_survey",
    "parse_material_card_library",
    "preflight_v3_case",
    "resolve_material_selections",
    "resolve_survey_v3",
    "review_unknown_v3",
    "run_prepared_v2_case",
    "run_v2_file_case",
    "run_v2_simulation",
    "run_v3_case_bundle",
    "run_v3_simulation",
    "run_village_simulation",
    "validate_unknown_mapping_v3",
    "write_v3_case_bundle",
]
