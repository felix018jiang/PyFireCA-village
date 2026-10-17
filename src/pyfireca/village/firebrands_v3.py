"""Mass-conserving research firebrands on the V3 clock.

Terminal settling / linear mass loss follow Martin & Hillen (2016), A19/A34.
Coefficients in this first schema are explicitly synthetic, not fitted village
properties. Statistical ignition starts a local flame followed by the existing
CA growth curve; it never fabricates a whole-cell ignition temperature.
"""

from __future__ import annotations

import hashlib
import heapq
import json
from dataclasses import asdict, dataclass, fields
from math import floor, isfinite, sqrt

import numpy as np

from pyfireca.village.wind_v3 import wind_from_to_velocity


def positive(value, name, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if (value < 0) if zero else (value <= 0):
        raise ValueError(f"{name} outside its positive range")


@dataclass(frozen=True, slots=True)
class FirebrandSource:
    material_index: int
    particle_state: str
    release_mass_fraction: float
    particle_mass_kg: float
    projected_area_m2: float
    drag_coefficient: float
    release_height_m: float
    height_relative_half_range: float
    mass_loss_rate_kg_s: float
    active_lifetime_s: float

    def __post_init__(self):
        if type(self.material_index) is not int or self.material_index < 0:
            raise ValueError("source material index must be a nonnegative integer")
        if self.particle_state not in {"flaming", "glowing"}:
            raise ValueError("unsupported particle state")
        for f in fields(self):
            if f.name not in {"material_index", "particle_state"}:
                positive(
                    getattr(self, f.name),
                    f.name,
                    zero=f.name in {"release_mass_fraction", "height_relative_half_range"},
                )
        if self.release_mass_fraction > 1 or self.height_relative_half_range >= 1:
            raise ValueError("mass fraction must be <=1 and height half range <1")


@dataclass(frozen=True, slots=True)
class FirebrandReceiver:
    material_index: int
    particle_state: str
    model: str
    contact_area_m2: float
    critical_mass_kg: float
    intercept: float
    mass_coefficient_per_kg: float
    moisture_coefficient: float
    wind_coefficient_s_m: float
    moisture_min_dry_basis: float
    moisture_max_dry_basis: float
    development_delay_s: float
    fuel_bed_description: str

    def __post_init__(self):
        if type(self.material_index) is not int or self.material_index < 0:
            raise ValueError("receiver material index must be a nonnegative integer")
        if self.particle_state not in {"flaming", "glowing"}:
            raise ValueError("unsupported receiver particle state")
        if self.model not in {"linear_mass_v1", "logistic_v1"}:
            raise ValueError("unsupported firebrand ignition model")
        if self.model == "linear_mass_v1" and self.particle_state != "flaming":
            raise ValueError("linear mass ignition is restricted to flaming particles")
        for name in ("contact_area_m2", "critical_mass_kg"):
            positive(getattr(self, name), name)
        for name in ("moisture_min_dry_basis", "moisture_max_dry_basis", "development_delay_s"):
            positive(getattr(self, name), name, zero=True)
        for name in (
            "intercept",
            "mass_coefficient_per_kg",
            "moisture_coefficient",
            "wind_coefficient_s_m",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (float, int))
                or not isfinite(value)
            ):
                raise ValueError(f"{name} must be finite")
        if self.moisture_min_dry_basis > self.moisture_max_dry_basis:
            raise ValueError("invalid moisture applicability interval")
        if self.particle_state == "glowing" and self.development_delay_s <= 0:
            raise ValueError("glowing ignition requires an explicit smolder-to-flame delay")
        if not isinstance(self.fuel_bed_description, str) or not self.fuel_bed_description.strip():
            raise ValueError("receiver fuel bed description is required")

    def probability(self, mass, moisture, wind):
        if not self.moisture_min_dry_basis <= moisture <= self.moisture_max_dry_basis:
            raise ValueError("receiver moisture outside the declared applicability interval")
        if self.model == "linear_mass_v1":
            return min(1.0, mass / self.critical_mass_kg)
        eta = (
            self.intercept
            + self.mass_coefficient_per_kg * mass
            + self.moisture_coefficient * moisture
            + self.wind_coefficient_s_m * wind
        )
        return float(1 / (1 + np.exp(-np.clip(eta, -700, 700))))


@dataclass(frozen=True, slots=True)
class FirebrandParameters:
    seed: int
    wind_speed_m_s: float
    wind_from_deg: float
    air_density_kg_m3: float
    release_interval_s: float
    sources: tuple[FirebrandSource, ...]
    receivers: tuple[FirebrandReceiver, ...]
    max_active_particles: int
    max_output_bytes: int
    parameter_evidence: str
    parameter_source: str

    def __post_init__(self):
        if type(self.seed) is not int or not 0 <= self.seed < 2**63:
            raise ValueError("firebrand seed must be an integer in [0,2^63)")
        wind_from_to_velocity(self.wind_speed_m_s, self.wind_from_deg)
        positive(self.air_density_kg_m3, "air density")
        positive(self.release_interval_s, "release interval")
        for name in ("max_active_particles", "max_output_bytes"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not self.sources or not self.receivers:
            raise ValueError("explicit source and receiver profiles required")
        if not all(isinstance(s, FirebrandSource) for s in self.sources) or not all(
            isinstance(r, FirebrandReceiver) for r in self.receivers
        ):
            raise TypeError("firebrand profiles must be typed")
        if len({s.material_index for s in self.sources}) != len(self.sources):
            raise ValueError("duplicate source material")
        if len({(r.material_index, r.particle_state) for r in self.receivers}) != len(
            self.receivers
        ):
            raise ValueError("duplicate receiver material/state")
        if self.parameter_evidence != "synthetic":
            raise ValueError("research-1 only supports explicitly synthetic coefficients")
        if not isinstance(self.parameter_source, str) or not self.parameter_source.strip():
            raise ValueError("parameter provenance required")

    def to_mapping(self):
        return {"schema_version": "village-firebrands-research-1", **asdict(self)}

    @classmethod
    def from_mapping(cls, raw):
        expected = {f.name for f in fields(cls)} | {"schema_version"}
        if not isinstance(raw, dict) or set(raw) != expected:
            raise ValueError("missing or unknown firebrand configuration fields")
        if raw["schema_version"] != "village-firebrands-research-1":
            raise ValueError("unsupported firebrand configuration schema")
        value = {k: v for k, v in raw.items() if k != "schema_version"}
        value["sources"] = tuple(FirebrandSource(**s) for s in value["sources"])
        value["receivers"] = tuple(FirebrandReceiver(**r) for r in value["receivers"])
        return cls(**value)


def draw(seed, event_id, purpose):
    key = f"{seed}|{event_id}|{purpose}".encode()
    integer = int.from_bytes(hashlib.blake2b(key, digest_size=8).digest(), "big") >> 11
    return integer / 2**53


def first_intersection(grid, elevation, x, y, z, u, v, settling, lifetime, domain_mask=None):
    """Exact grid-face traversal through piecewise-constant DEM columns.

    The first raised column is checked at entry; a narrow ridge cannot be
    skipped by a long flight integration step. Buildings/canopy interception
    and plume dynamics are outside this prescribed-wind closure.
    """
    size = grid.cell_size_m
    t = 0.0
    while t < lifetime:
        # Resolve the cell immediately downstream at a face (not the old cell).
        eps = 1e-8
        xx, yy = x + u * (t + eps), y + v * (t + eps)
        col = floor((xx - grid.x_min_m) / size)
        row = floor((grid.y_max_m - yy) / size)
        if not (0 <= row < grid.height and 0 <= col < grid.width):
            return t, "exited_domain", None
        if domain_mask is not None and not domain_mask[row, col]:
            return t, "exited_domain", None
        ground = 0.0 if elevation is None else float(elevation[row, col])
        hit = (z - ground) / settling
        left = grid.x_min_m + col * size
        top = grid.y_max_m - row * size
        tx = ((left + size if u > 0 else left) - x) / u if u else np.inf
        ty = ((top if v > 0 else top - size) - y) / v if v else np.inf
        leave = min(tx, ty, lifetime)
        if hit <= t + eps:
            return t, "landed", (row, col)
        if hit < leave - 1e-10:
            return hit, "landed", (row, col)
        if leave >= lifetime:
            return lifetime, "extinguished", None
        if leave <= t + 1e-12:
            raise RuntimeError("firebrand terrain traversal did not advance")
        t = leave
    return lifetime, "extinguished", None


class FirebrandOperator:
    def __init__(self, request):
        self.request, self.config = request, request.firebrands
        self.pending = np.zeros(request.grid.shape)
        self.ordinals = np.zeros(request.grid.shape, dtype=np.int64)
        self.sources = {s.material_index: s for s in self.config.sources}
        self.receivers = {(r.material_index, r.particle_state): r for r in self.config.receivers}
        self.queue = []
        self.events, self.budgets = [], []
        self.serial, self.output_bytes = 0, 0
        self.release_index = 1
        self.transferred_mass = self.transferred_energy = 0.0
        self.air_burn_mass = self.air_burn_energy = 0.0
        self.terminal = {kind: [0.0, 0.0] for kind in ("landed", "exited_domain", "extinguished")}
        self.counts = {
            kind: 0
            for kind in (
                "released",
                "landed",
                "exited_domain",
                "extinguished",
                "ignited",
                "scheduled",
            )
        }
        self.fraction = np.zeros(request.grid.shape)
        ground = request.ground_elevation_m
        self.surface = np.zeros(request.grid.shape) if ground is None else ground.copy()
        self.surface += np.where(
            request.building_index >= 0, request.parameters.receiver_height_m, 0.0
        )
        for material, source in self.sources.items():
            mask = (
                (request.material_index == material) & request.source_mask & request.burnable_mask
            )
            self.fraction[mask] = source.release_mass_fraction

    def record(self, row):
        self.output_bytes += len(json.dumps(row, sort_keys=True).encode()) + 1
        if self.output_bytes > self.config.max_output_bytes:
            raise RuntimeError("firebrand output byte limit exceeded; result is incomplete")
        self.events.append(row)

    def next_time(self):
        return min(
            self.release_index * self.config.release_interval_s,
            self.queue[0][0] if self.queue else np.inf,
        )

    def divert(self, gross_mass):
        mass = gross_mass * self.fraction
        self.pending += mass
        self.transferred_mass += float(mass.sum())
        self.transferred_energy += float(
            np.sum(mass * self.request.parameters.heat_of_combustion_J_kg_dry)
        )
        return 1 - self.fraction

    def push(self, time, event_id, kind, payload):
        if len(self.queue) >= self.config.max_active_particles:
            raise RuntimeError("firebrand active event limit exceeded; result is incomplete")
        self.serial += 1
        heapq.heappush(self.queue, (time, event_id, self.serial, kind, payload))

    def release(self, time):
        grid = self.request.grid
        u, v = wind_from_to_velocity(self.config.wind_speed_m_s, self.config.wind_from_deg)
        for material, profile in sorted(self.sources.items()):
            eligible = (self.request.material_index == material) & (
                self.pending >= profile.particle_mass_kg
            )
            for row, col in zip(*np.nonzero(eligible), strict=True):
                n = floor(self.pending[row, col] / profile.particle_mass_kg)
                self.pending[row, col] -= n * profile.particle_mass_kg
                x0 = grid.x_min_m + (col + 0.5) * grid.cell_size_m
                y0 = grid.y_max_m - (row + 0.5) * grid.cell_size_m
                hc = float(self.request.parameters.heat_of_combustion_J_kg_dry[row, col])
                for _ in range(n):
                    ordinal = int(self.ordinals[row, col])
                    self.ordinals[row, col] += 1
                    event_id = f"{x0:.6f}:{y0:.6f}:{ordinal}"
                    height = profile.release_height_m * (
                        1
                        + profile.height_relative_half_range
                        * (2 * draw(self.config.seed, event_id, "height") - 1)
                    )
                    x = x0 + (draw(self.config.seed, event_id, "x") - 0.5) * grid.cell_size_m
                    y = y0 + (draw(self.config.seed, event_id, "y") - 0.5) * grid.cell_size_m
                    ground = (
                        0.0
                        if self.request.ground_elevation_m is None
                        else float(self.request.ground_elevation_m[row, col])
                    )
                    z = ground + height
                    vt = sqrt(
                        2
                        * profile.particle_mass_kg
                        * 9.81
                        / (
                            self.config.air_density_kg_m3
                            * profile.drag_coefficient
                            * profile.projected_area_m2
                        )
                    )
                    life = min(
                        profile.active_lifetime_s,
                        profile.particle_mass_kg / profile.mass_loss_rate_kg_s,
                    )
                    flight, kind, target = first_intersection(
                        grid, self.surface, x, y, z, u, v, vt, life, self.request.domain_mask
                    )
                    payload = {
                        "profile": profile,
                        "start": time,
                        "flight": flight,
                        "x": x,
                        "y": y,
                        "z": z,
                        "u": u,
                        "v": v,
                        "vt": vt,
                        "target": target,
                        "hc": hc,
                        "source_cell_id": grid.cell_id(int(row), int(col)),
                    }
                    self.push(time + flight, event_id, kind, payload)
                    self.counts["released"] += 1
                    self.record(
                        {
                            "event_id": event_id + ":released",
                            "particle_id": event_id,
                            "source_cell_id": payload["source_cell_id"],
                            "time_s": time,
                            "kind": "released",
                            "x_m": x,
                            "y_m": y,
                            "z_m": z,
                            "mass_kg": profile.particle_mass_kg,
                            "particle_state": profile.particle_state,
                        }
                    )

    def process(self, state, cause):
        time = state.time_s
        if time + 1e-9 >= self.release_index * self.config.release_interval_s:
            self.release(time)
            self.release_index += 1
        # Direct and thermal events already applied at this exact clock time.
        while self.queue and self.queue[0][0] <= time + 1e-9:
            when, event_id, _, kind, p = heapq.heappop(self.queue)
            if kind == "ignite":
                row, col = p["target"]
                if state.state[row, col] in (1, 2) and state.fuel_remaining_kg[row, col] > 0:
                    state.state[row, col] = 3
                    state.ignition_time_s[row, col] = when
                    state.temperature_valid_mask[row, col] = False
                    state.burn_age_s[row, col] = 0.0
                    cause[row, col] = 3
                    self.counts["ignited"] += 1
                    self.record(
                        {
                            **p["landing"],
                            "event_id": event_id + ":ignition",
                            "time_s": when,
                            "kind": "ignited",
                            "ignited": True,
                            "reason": "statistical_local_flame_then_CA_growth",
                        }
                    )
                continue
            profile = p["profile"]
            lost = min(profile.particle_mass_kg, profile.mass_loss_rate_kg_s * p["flight"])
            mass = max(0.0, profile.particle_mass_kg - lost)
            self.air_burn_mass += lost
            self.air_burn_energy += lost * p["hc"]
            self.terminal[kind][0] += mass
            self.terminal[kind][1] += mass * p["hc"]
            self.counts[kind] += 1
            landing = {
                "event_id": event_id + ":" + kind,
                "particle_id": event_id,
                "source_cell_id": p["source_cell_id"],
                "time_s": when,
                "kind": kind,
                "x_m": p["x"] + p["u"] * p["flight"],
                "y_m": p["y"] + p["v"] * p["flight"],
                "z_m": p["z"] - p["vt"] * p["flight"],
                "mass_kg": mass,
                "particle_state": profile.particle_state,
                "p_ignite": None,
                "ignited": False,
                "reason": kind,
            }
            if kind == "landed":
                row, col = p["target"]
                landing["target_cell_id"] = self.request.grid.cell_id(row, col)
                receiver = self.receivers.get(
                    (int(self.request.material_index[row, col]), profile.particle_state)
                )
                eligible = (
                    self.request.domain_mask[row, col]
                    and self.request.known_mask[row, col]
                    and self.request.burnable_mask[row, col]
                    and self.request.piloted_ignition_mask[row, col]
                    and state.state[row, col] in (1, 2)
                    and state.fuel_remaining_kg[row, col] > 0
                    and mass > 0
                )
                if eligible and receiver:
                    moisture = (
                        self.request.parameters.water_mass_kg[row, col]
                        / self.request.parameters.initial_dry_fuel_mass_kg[row, col]
                    )
                    probability = receiver.probability(mass, moisture, self.config.wind_speed_m_s)
                    landing["p_ignite"] = probability
                    success = draw(self.config.seed, event_id, "ignite") < probability
                    landing["reason"] = "local_flame_scheduled" if success else "draw_failed"
                    if success:
                        self.counts["scheduled"] += 1
                        landing["scheduled_flame_time_s"] = when + receiver.development_delay_s
                        self.push(
                            when + receiver.development_delay_s,
                            event_id,
                            "ignite",
                            {"target": (row, col), "landing": landing.copy()},
                        )
                else:
                    landing["reason"] = "ineligible_receiver_or_no_profile"
            self.record(landing)

    def budget(self, time):
        active_mass = active_energy = extra_air_mass = extra_air_energy = 0.0
        for _, _, _, kind, p in self.queue:
            if kind == "ignite":
                continue
            profile = p["profile"]
            lost = min(
                profile.particle_mass_kg, profile.mass_loss_rate_kg_s * max(0.0, time - p["start"])
            )
            mass = profile.particle_mass_kg - lost
            active_mass += mass
            active_energy += mass * p["hc"]
            extra_air_mass += lost
            extra_air_energy += lost * p["hc"]
        pending_mass = float(self.pending.sum())
        pending_energy = float(
            np.sum(self.pending * self.request.parameters.heat_of_combustion_J_kg_dry)
        )
        air_mass = self.air_burn_mass + extra_air_mass
        air_energy = self.air_burn_energy + extra_air_energy
        mass_error = abs(
            self.transferred_mass
            - pending_mass
            - active_mass
            - air_mass
            - sum(v[0] for v in self.terminal.values())
        )
        energy_error = abs(
            self.transferred_energy
            - pending_energy
            - active_energy
            - air_energy
            - sum(v[1] for v in self.terminal.values())
        )
        if (
            mass_error > 1e-9 + self.transferred_mass * 1e-9
            or energy_error > 1e-5 + self.transferred_energy * 1e-9
        ):
            raise AssertionError("firebrand mass/chemical energy ledger failed")
        return {
            "time_s": time,
            "transferred_mass_kg": self.transferred_mass,
            "transferred_chemical_J": self.transferred_energy,
            "pending_mass_kg": pending_mass,
            "pending_chemical_J": pending_energy,
            "in_flight_mass_kg": active_mass,
            "in_flight_chemical_J": active_energy,
            "air_consumed_mass_kg": air_mass,
            "air_consumed_chemical_J": air_energy,
            **{kind + "_mass_kg": v[0] for kind, v in self.terminal.items()},
            **{kind + "_chemical_J": v[1] for kind, v in self.terminal.items()},
            "mass_closure_error_kg": mass_error,
            "chemical_closure_error_J": energy_error,
        }

    def capture(self, time):
        value = self.budget(time)
        if not self.budgets or abs(self.budgets[-1]["time_s"] - time) > 1e-9:
            self.output_bytes += len(json.dumps(value).encode()) + 1
            if self.output_bytes > self.config.max_output_bytes:
                raise RuntimeError("firebrand output byte limit exceeded; result is incomplete")
            self.budgets.append(value)

    def result(self, time):
        self.capture(time)
        pending = []
        for when, event_id, _, kind, p in sorted(self.queue):
            row = {"event_id": event_id, "kind": kind, "due_time_s": when}
            if kind == "ignite":
                row["target_cell_id"] = self.request.grid.cell_id(*p["target"])
            else:
                age = max(0.0, time - p["start"])
                row.update(
                    x_m=p["x"] + p["u"] * age,
                    y_m=p["y"] + p["v"] * age,
                    z_m=p["z"] - p["vt"] * age,
                    mass_kg=max(
                        0.0, p["profile"].particle_mass_kg - p["profile"].mass_loss_rate_kg_s * age
                    ),
                )
            pending.append(row)
        report = {
            "schema_version": "village-firebrands-output-1",
            "configuration": self.config.to_mapping(),
            "counts": self.counts,
            "events": self.events,
            "budgets": self.budgets,
            "final_budget": self.budget(time),
            "pending_events": pending,
            "landing_model": "single_brand_contact_patch_no_pile_accumulation",
            "source_clock": "mass_diversion_with_exact_fixed_release_bins",
            "trajectory": "uniform_wind_terminal_first_terrain_and_effective_building_surface",
        }
        if len(json.dumps(report).encode()) > self.config.max_output_bytes:
            raise RuntimeError("firebrand final output byte limit exceeded; result is incomplete")
        return report
