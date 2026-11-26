"""Compiled sparse implementation of the registered, static-wind V3 thermal clock.

The original V3 request, node graph and heat/contact parameters are used directly.
Only time-zero prescribed ignitions are supported. This clock emits V3 states and
budgets at exact integration boundaries so original checks can be used verbatim.
"""

import ctypes as ct
import hashlib
import os
import platform
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np

from pyfireca.village.solver_v3 import V3EnergyBudget, V3SimulationResult, _initial_state

DOUBLE = ct.POINTER(ct.c_double)
INT = ct.POINTER(ct.c_int32)
BYTE = ct.POINTER(ct.c_uint8)


class Config(ct.Structure):
    _fields_ = [("n", ct.c_int32)] + [
        (name, ct.c_double)
        for name in (
            "ambient",
            "heating_delta",
            "max_dt",
            "sampling",
            "gas_temp",
            "contact_h",
            "fraction",
        )
    ]


class Params(ct.Structure):
    _fields_ = [
        (name, DOUBLE)
        for name in (
            "area",
            "capacity",
            "emiss",
            "ambient_h",
            "backing",
            "alpha",
            "ignition",
            "peak",
            "growth",
            "steady",
            "decay",
            "heat",
            "radiative",
            "fuel_tol",
        )
    ] + [("pilot", BYTE)]


class State(ct.Structure):
    _fields_ = [(name, BYTE) for name in ("phase", "valid", "cause")] + [
        (name, DOUBLE)
        for name in (
            "temp",
            "water",
            "fuel",
            "age",
            "hrr",
            "incident",
            "absorbed",
            "convection",
            "released",
            "ignition",
            "burnout",
            "peak_temp",
        )
    ]


class Edges(ct.Structure):
    _fields_ = [
        ("offsets", INT),
        ("targets", INT),
        ("weights", DOUBLE),
        ("contact_offsets", INT),
        ("contact_targets", INT),
        ("contact_weights", DOUBLE),
    ]


@lru_cache(maxsize=1)
def _kernel():
    source = Path(__file__).with_name("heritage_wind_terminal_kernel.cpp")
    identity = hashlib.sha256(source.read_bytes() + platform.machine().encode()).hexdigest()[:24]
    # User-private directory and atomic replacement allow simultaneous first use.
    directory = Path(tempfile.gettempdir()) / f"pyfireca-thermal-{os.getuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    library = directory / (identity + ".so")
    if not library.exists():
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as build:
            target = Path(build.name)
        try:
            subprocess.run(
                [
                    "c++",
                    "-std=c++17",
                    "-O3",
                    "-ffp-contract=off",
                    "-fPIC",
                    "-shared",
                    str(source),
                    "-o",
                    str(target),
                ],
                check=True,
            )
            os.replace(target, library)
        finally:
            target.unlink(missing_ok=True)
    function = ct.CDLL(str(library)).advance
    function.argtypes = [
        ct.POINTER(Config),
        ct.POINTER(Params),
        ct.POINTER(State),
        ct.POINTER(Edges),
        ct.c_double,
        ct.c_bool,
        ct.c_bool,
        DOUBLE,
        ct.POINTER(ct.c_int64),
        DOUBLE,
    ]
    function.restype = ct.c_int
    return function


def _struct(schema, arrays):
    fields = []
    for a, (_, pointer) in zip(arrays, schema._fields_, strict=True):
        dtype = np.float64 if pointer is DOUBLE else np.int32 if pointer is INT else np.uint8
        a = np.ascontiguousarray(a, dtype=dtype)
        fields.append(a.ctypes.data_as(pointer))
    # ctypes retains all pointer objects, which in turn retain their NumPy owners.
    return schema(*fields), fields


class TerminalWindClock:
    def __init__(self, request, contact, monitored_nodes=None):
        if request.firebrands is not None or any(
            e.time_s != 0.0 for e in request.scenario.ignitions
        ):
            raise ValueError(
                "the registered thermal clock requires time-zero sources and no firebrands"
            )
        self.request = request
        self.state = state = _initial_state(request)
        self.initial_fuel = state.fuel_remaining_kg.copy()
        self.initial_water = state.heated_water_remaining_kg.copy()
        self.cause = np.zeros_like(state.state)
        for e in request.scenario.ignitions:
            if not request.burnable_mask[e.row, e.col]:
                raise ValueError("initial ignition is outside burnable material")
            state.state[e.row, e.col] = 3
            state.temperature_valid_mask[e.row, e.col] = False
            state.ignition_time_s[e.row, e.col] = 0.0
            self.cause[e.row, e.col] = 1
        self.n = state.state.size
        self.monitored = (
            slice(None) if monitored_nodes is None else np.asarray(monitored_nodes).ravel()
        )
        self.peaks = np.full(self.n, request.scenario.ambient_temperature_K)
        self.budget = np.zeros(len(V3EnergyBudget.__dataclass_fields__), dtype=np.float64)
        self.config = Config(
            self.n,
            request.scenario.ambient_temperature_K,
            request.scenario.heating_temperature_delta_K,
            request.scenario.max_dt_s,
            request.sample_interval_s or 0.0,
            contact.parameters.gas_temperature_K if contact else 0.0,
            contact.parameters.heat_transfer_coefficient_W_m2_K if contact else 0.0,
            contact.parameters.contact_fraction if contact else 0.0,
        )
        p = request.parameters
        area = p.covered_area_m2
        capacity = np.divide(
            p.effective_heated_dry_mass_kg, area, out=np.zeros_like(area), where=area > 0
        )
        capacity *= p.specific_heat_J_kg_K
        self.params, self._param_ref = _struct(
            Params,
            [
                area,
                capacity,
                p.emissivity,
                p.ambient_heat_transfer_coefficient_W_m2_K,
                p.backing_conductance_W_m2_K,
                p.absorptivity,
                p.ignition_temperature_K,
                p.peak_hrr_W,
                p.growth_time_s,
                p.steady_time_s,
                p.decay_time_s,
                p.heat_of_combustion_J_kg_dry,
                p.radiative_fraction,
                np.maximum(1e-12, self.initial_fuel * 1e-9),
                request.piloted_ignition_mask,
            ],
        )
        # uint8 views retain writable boolean storage, so the C clock updates
        # the exact V3 state arrays (including temperature_valid_mask).
        self.arrays, self._state_ref = _struct(
            State,
            [
                state.state,
                state.temperature_valid_mask.view(np.uint8),
                self.cause,
                state.surface_temperature_K,
                state.heated_water_remaining_kg,
                state.fuel_remaining_kg,
                state.burn_age_s,
                state.interval_mean_hrr_W,
                state.incident_radiation_J,
                state.absorbed_radiation_J,
                state.received_convection_J,
                state.released_energy_J,
                state.ignition_time_s,
                state.burnout_time_s,
                self.peaks,
            ],
        )
        graph = request.exposure_edges
        offsets = np.r_[0, np.bincount(graph.source, minlength=self.n).cumsum()]
        if offsets[-1] >= 2**31:
            raise ValueError("radiation topology exceeds the registered sparse address width")
        if contact:
            valid = (contact.neighbors >= 0) & (contact.geometry_fraction > 0)
            contact_offsets = np.r_[0, valid.sum(axis=1).cumsum()]
            contact_targets = contact.neighbors[valid]
            contact_weights = contact.geometry_fraction[valid]
        else:
            contact_offsets = np.zeros(self.n + 1, dtype=np.int32)
            contact_targets = np.array([], dtype=np.int32)
            contact_weights = np.array([], dtype=np.float64)
        if contact_offsets[-1] >= 2**31:
            raise ValueError("contact topology exceeds the registered sparse address width")
        self.graph, self._graph_ref = _struct(
            Edges,
            [
                offsets,
                graph.target,
                graph.weight,
                contact_offsets,
                contact_targets,
                contact_weights,
            ],
        )

    @property
    def peak_K(self):
        return self.peaks[self.monitored]

    def advance(self, until_s, *, stop_on_extinction=True, exact=True):
        if not np.isfinite(until_s) or until_s < self.state.time_s:
            raise ValueError("the requested integration boundary must be finite and monotone")
        t = ct.c_double(self.state.time_s)
        step = ct.c_int64(self.state.step_index)
        code = _kernel()(
            self.config,
            self.params,
            self.arrays,
            self.graph,
            until_s,
            stop_on_extinction,
            exact,
            ct.byref(t),
            ct.byref(step),
            self.budget.ctypes.data_as(DOUBLE),
        )
        if code:
            reasons = {
                1: "ambient exterior CFL is active",
                2: "fresh fuel can spontaneously ignite at ambient",
                3: "nonpositive or invalid timestep",
                4: "burn curve ended with fuel",
            }
            raise RuntimeError(f"thermal continuation failed at {t.value:g} s: {reasons[code]}")
        self.state.time_s = t.value
        self.state.step_index = step.value
        return t.value

    def result(self):
        values = self.budget.tolist()
        values[-1] = int(values[-1])
        p = self.request.parameters
        return V3SimulationResult(
            final_state=self.state.copy(),
            initial_total_dry_fuel_kg=p.initial_dry_fuel_mass_kg,
            initial_fuel_kg=self.initial_fuel,
            initial_total_water_kg=p.water_mass_kg,
            initial_heated_water_kg=self.initial_water,
            ignition_cause=self.cause.copy(),
            energy_budget=V3EnergyBudget(*values),
            snapshots=(),
            firebrands=None,
        )
