"""Synchronous V3 validation clock with end-of-interval incident radiation accounting."""

import ctypes as ct
import hashlib
import os
import platform
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np
from heritage_wind_terminal_engine import DOUBLE, TerminalWindClock
from heritage_wind_terminal_engine import _kernel as reference_kernel

COMPILE_FLAGS = ("-std=c++17", "-O3", "-ffp-contract=off", "-fPIC", "-shared")


@lru_cache(maxsize=1)
def backend_signature():
    """Bind validation code and the full reference clock it directly imports/includes."""
    files = (
        Path(__file__),
        Path(__file__).with_name("heritage_wind_validation_kernel.cpp"),
        Path(__file__).with_name("heritage_wind_terminal_engine.py"),
        Path(__file__).with_name("heritage_wind_terminal_kernel.cpp"),
    )
    digest = hashlib.sha256()
    digest.update(platform.machine().encode())
    digest.update("\0".join(COMPILE_FLAGS).encode())
    digest.update(subprocess.check_output(["c++", "--version"]))
    for path in files:
        digest.update(path.name.encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _validation_kernel():
    source = Path(__file__).with_name("heritage_wind_validation_kernel.cpp")
    directory = Path(tempfile.gettempdir()) / f"pyfireca-thermal-{os.getuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    library = directory / f"validation-{platform.machine()}-{backend_signature()}.so"
    if not library.exists():
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
            temporary = Path(output.name)
        try:
            subprocess.run(["c++", *COMPILE_FLAGS, str(source), "-o", str(temporary)], check=True)
            os.replace(temporary, library)
        finally:
            temporary.unlink(missing_ok=True)
    function = ct.CDLL(str(library)).advance_validation
    function.argtypes = reference_kernel().argtypes
    function.restype = ct.c_int
    return function


class FastTerminalWindClock(TerminalWindClock):
    """Advance the original V3 state and every observable at the same clock boundaries."""

    def advance(self, until_s, *, stop_on_extinction=True, exact=True):
        if not np.isfinite(until_s) or until_s < self.state.time_s:
            raise ValueError("the requested integration boundary must be finite and monotone")
        elapsed = ct.c_double(self.state.time_s)
        step = ct.c_int64(self.state.step_index)
        code = _validation_kernel()(
            self.config,
            self.params,
            self.arrays,
            self.graph,
            until_s,
            stop_on_extinction,
            exact,
            ct.byref(elapsed),
            ct.byref(step),
            self.budget.ctypes.data_as(DOUBLE),
        )
        if code:
            reasons = {
                1: "ambient exterior CFL is active",
                2: "ambient thermal ignition",
                3: "invalid timestep",
                4: "fuel remains at burn-curve end",
            }
            raise RuntimeError(f"validation clock failed at {elapsed.value:g} s: {reasons[code]}")
        self.state.time_s = elapsed.value
        self.state.step_index = step.value
        return elapsed.value
