"""Strict, low-burden material-card inventory for village surveys.

The M01 card library is deliberately a *selector and provenance inventory*.
It does not contain numerical heat-release, ignition, moisture, or transport
parameters.  Those values belong to a later assembly/scenario resolver and
must remain traceable to a measured or otherwise explicitly qualified source.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from pyfireca.village.survey import COMPONENT_IDS, MATERIAL_CLASS_IDS

SCHEMA_VERSION = "village-material-cards-1"
INVENTORY_STATUSES = ("minimal_unresolved", "reviewed_inventory")
COMBUSTIBILITY_IDS = ("burnable", "inert", "unknown")
EVIDENCE_STATUS_IDS = (
    "reference_only",
    "pending_full_text",
    "scenario_assumed",
    "field_observation_only",
)
SOURCE_ROLES = ("numeric_reference", "method_only", "classification_only")
SOURCE_KINDS = frozenset(
    {
        "peer_reviewed_journal_article",
        "peer_reviewed_conference_paper",
        "government_research_publication",
        "official_classification_or_standard",
    }
)

_ROOT_KEYS = frozenset(
    {
        "schema_version",
        "library_id",
        "evidence_inventory_status",
        "production_ready",
        "sources",
        "cards",
    }
)
_SOURCE_KEYS = frozenset({"title", "citation", "kind", "url", "role", "notes"})
_CARD_KEYS = frozenset(
    {
        "id",
        "version",
        "label",
        "material_class",
        "allowed_components",
        "combustibility",
        "reference_profile_ids",
        "assembly_preset_ids",
        "evidence_status",
        "source_ids",
        "limitations",
    }
)


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a mapping")
    return value


def _exact_keys(value: dict[str, Any], expected: frozenset[str], name: str) -> None:
    missing = expected - value.keys()
    unknown = value.keys() - expected
    if missing:
        raise ValueError(f"{name} is missing keys: {sorted(missing)}")
    if unknown:
        raise ValueError(f"{name} contains unknown keys: {sorted(unknown)}")


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _enum(value: Any, name: str, allowed: tuple[str, ...]) -> str:
    result = _text(value, name)
    if result not in allowed:
        raise ValueError(f"{name} must be one of {list(allowed)!r}; got {result!r}")
    return result


def _text_list(value: Any, name: str, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a list")
    if not allow_empty and not value:
        raise ValueError(f"{name} must not be empty")
    result = tuple(_text(item, f"{name}[{index}]") for index, item in enumerate(value))
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must not contain duplicate values")
    return result


@dataclass(frozen=True, slots=True)
class MaterialCardSource:
    """A source attached to a card, without copying numerical values."""

    id: str
    title: str
    citation: str
    kind: str
    url: str
    role: str
    notes: str


@dataclass(frozen=True, slots=True)
class MaterialCard:
    """One field-selectable material category and its evidence boundary."""

    id: str
    version: str
    label: str
    material_class: str
    allowed_components: tuple[str, ...]
    combustibility: str
    reference_profile_ids: tuple[str, ...]
    assembly_preset_ids: tuple[str, ...]
    evidence_status: str
    source_ids: tuple[str, ...]
    limitations: str


@dataclass(frozen=True, slots=True)
class MaterialCardLibrary:
    """Validated M01 inventory; it is not a runnable simulation config."""

    path: Path
    library_id: str
    evidence_inventory_status: str
    production_ready: bool
    sources: dict[str, MaterialCardSource]
    cards: tuple[MaterialCard, ...]

    @property
    def by_id(self) -> dict[str, MaterialCard]:
        return {card.id: card for card in self.cards}

    @property
    def by_material_class(self) -> dict[str, MaterialCard]:
        return {card.material_class: card for card in self.cards}

    @property
    def evidence_status_counts(self) -> dict[str, int]:
        return {
            status: sum(card.evidence_status == status for card in self.cards)
            for status in EVIDENCE_STATUS_IDS
        }

    @property
    def referenced_source_ids(self) -> tuple[str, ...]:
        used = {source_id for card in self.cards for source_id in card.source_ids}
        return tuple(source_id for source_id in self.sources if source_id in used)


def parse_material_card_library(data: Any, *, path: Path | None = None) -> MaterialCardLibrary:
    """Parse and validate a ``village-material-cards-1`` YAML document.

    Exact card keys intentionally reject a ``parameters`` mapping and any
    dry/wet selector.  This keeps M01 from silently turning a field category
    into an untraceable numerical material model.
    """

    root = _mapping(data, "material-card-library")
    _exact_keys(root, _ROOT_KEYS, "material-card-library")
    if root["schema_version"] != SCHEMA_VERSION:
        raise ValueError(
            f"material-card-library.schema_version must be {SCHEMA_VERSION!r}; "
            f"got {root['schema_version']!r}"
        )
    library_id = _text(root["library_id"], "library_id")
    inventory_status = _enum(
        root["evidence_inventory_status"],
        "evidence_inventory_status",
        INVENTORY_STATUSES,
    )
    if root["production_ready"] is not False:
        raise ValueError("material card library must keep production_ready false")

    raw_sources = _mapping(root["sources"], "sources")
    if not raw_sources:
        raise ValueError("sources must not be empty")
    sources: dict[str, MaterialCardSource] = {}
    for raw_id, raw_value in raw_sources.items():
        source_id = _text(raw_id, "source ID")
        if source_id in sources:
            raise ValueError(f"duplicate source ID {source_id!r}")
        source = _mapping(raw_value, f"source {source_id}")
        _exact_keys(source, _SOURCE_KEYS, f"source {source_id}")
        url = _text(source["url"], f"source {source_id}.url")
        if not url.startswith(("https://", "http://")):
            raise ValueError(f"source {source_id}.url needs an HTTP(S) URL")
        sources[source_id] = MaterialCardSource(
            id=source_id,
            title=_text(source["title"], f"source {source_id}.title"),
            citation=_text(source["citation"], f"source {source_id}.citation"),
            kind=_enum(source["kind"], f"source {source_id}.kind", tuple(SOURCE_KINDS)),
            url=url,
            role=_enum(source["role"], f"source {source_id}.role", SOURCE_ROLES),
            notes=_text(source["notes"], f"source {source_id}.notes"),
        )

    raw_cards = root["cards"]
    if not isinstance(raw_cards, list) or not raw_cards:
        raise ValueError("cards must be a non-empty list")
    cards: list[MaterialCard] = []
    card_ids: set[str] = set()
    classes: set[str] = set()
    for index, raw_value in enumerate(raw_cards):
        name = f"cards[{index}]"
        raw = _mapping(raw_value, name)
        _exact_keys(raw, _CARD_KEYS, name)
        card_id = _text(raw["id"], f"{name}.id")
        if card_id in card_ids:
            raise ValueError(f"duplicate card ID {card_id!r}")
        card_ids.add(card_id)
        material_class = _enum(
            raw["material_class"], f"{card_id}.material_class", MATERIAL_CLASS_IDS
        )
        if material_class in classes:
            raise ValueError(f"duplicate material_class {material_class!r}")
        classes.add(material_class)
        allowed_components = _text_list(
            raw["allowed_components"], f"{card_id}.allowed_components", allow_empty=False
        )
        invalid_components = set(allowed_components) - set(COMPONENT_IDS)
        if invalid_components:
            raise ValueError(
                f"{card_id}.allowed_components has unsupported values {sorted(invalid_components)}"
            )
        combustibility = _enum(
            raw["combustibility"], f"{card_id}.combustibility", COMBUSTIBILITY_IDS
        )
        evidence_status = _enum(
            raw["evidence_status"], f"{card_id}.evidence_status", EVIDENCE_STATUS_IDS
        )
        reference_profile_ids = _text_list(
            raw["reference_profile_ids"], f"{card_id}.reference_profile_ids", allow_empty=True
        )
        assembly_preset_ids = _text_list(
            raw["assembly_preset_ids"], f"{card_id}.assembly_preset_ids", allow_empty=True
        )
        source_ids = _text_list(raw["source_ids"], f"{card_id}.source_ids", allow_empty=True)
        missing_sources = set(source_ids) - sources.keys()
        if missing_sources:
            raise ValueError(f"{card_id} references unknown sources {sorted(missing_sources)}")
        if evidence_status == "reference_only" and not reference_profile_ids:
            raise ValueError(f"{card_id} reference_only card needs reference_profile_ids")
        if evidence_status == "reference_only" and not source_ids:
            raise ValueError(f"{card_id} reference_only card needs source_ids")
        if combustibility == "inert" and evidence_status == "scenario_assumed":
            raise ValueError(f"{card_id} cannot call an assumed inert card a reference result")
        cards.append(
            MaterialCard(
                id=card_id,
                version=_text(raw["version"], f"{card_id}.version"),
                label=_text(raw["label"], f"{card_id}.label"),
                material_class=material_class,
                allowed_components=allowed_components,
                combustibility=combustibility,
                reference_profile_ids=reference_profile_ids,
                assembly_preset_ids=assembly_preset_ids,
                evidence_status=evidence_status,
                source_ids=source_ids,
                limitations=_text(raw["limitations"], f"{card_id}.limitations"),
            )
        )

    missing_classes = set(MATERIAL_CLASS_IDS) - classes
    if missing_classes:
        raise ValueError(
            f"material card library is missing material classes {sorted(missing_classes)}"
        )
    return MaterialCardLibrary(
        path=path or Path("<memory>"),
        library_id=library_id,
        evidence_inventory_status=inventory_status,
        production_ready=False,
        sources=MappingProxyType(sources),
        cards=tuple(cards),
    )


def load_material_card_library(path: str | Path) -> MaterialCardLibrary:
    source = Path(path).expanduser().resolve()
    with source.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    return parse_material_card_library(data, path=source)


__all__ = [
    "COMBUSTIBILITY_IDS",
    "EVIDENCE_STATUS_IDS",
    "INVENTORY_STATUSES",
    "SCHEMA_VERSION",
    "MaterialCard",
    "MaterialCardLibrary",
    "MaterialCardSource",
    "load_material_card_library",
    "parse_material_card_library",
]
