"""Execute the registered two-route supplement on the original full-domain wind clock."""

import argparse
from pathlib import Path

import finish_heritage_wind_validation as full_domain
import resume_heritage_geometry_validation as reuse
import run_heritage_wind_validation as validation
from run_heritage_geometry_validation import (
    observe_ordinary_roofs,
    read_json,
    solver_signatures,
    verify_binding,
)

from pyfireca.planning.heritage import content_hash

DEST = validation.ROOT / "output/heritage-midbudget-20261120"


def verify():
    reg = read_json(DEST / "registration.json")
    if (
        content_hash({k: v for k, v in reg.items() if k != "registration_sha256"})
        != reg["registration_sha256"]
    ):
        raise ValueError("Registered experiment changed")
    for key in (
        "wide_registration",
        "compact_budget_registration",
        "backend_verification",
        "domain_reuse_verification",
        "design_program",
        "readout_program",
        "run_program",
        "protocol",
    ):
        verify_binding(reg[key])
    archive = read_json(verify_binding(reg["wide_registration"]))
    reference, model = solver_signatures(archive)
    cache_receipt = read_json(verify_binding(reg["domain_reuse_verification"]))
    cache_seal = cache_receipt.pop("verification_sha256")
    if (
        validation.record_hash(cache_receipt) != cache_seal
        or validation.digest(Path(reuse.__file__)) != cache_receipt["scheduler_sha256"]
        or cache_receipt["registration_sha256"] != reg["compact_budget_registration"]["sha256"]
        or {c["village"] for c in cache_receipt["cases"] if c["identical_arrays_and_ignition"]}
        != set(reg["villages"])
        or reg["winds"] != archive["winds"]
    ):
        raise ValueError("Original full-domain geometry or wind panel changed")
    for village, setup in reg["villages"].items():
        verify_binding(setup["route_map"])
        for field in (
            "fires",
            "core_ids",
            "fixed_source_ids",
            "study_path",
            "core_overlap_policy",
            "random_eligible_ids_by_stratum",
            "grid",
            "input_files_sha256",
        ):
            if setup[field] != archive["villages"][village][field]:
                raise ValueError(f"{village}: paired physical input changed")
        for name, sha in setup["input_files_sha256"].items():
            if validation.digest(validation.ROOT / name) != sha:
                raise ValueError(f"Input changed: {name}")
    return reg, reference, model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=7)
    parser.add_argument("--batch-size", type=int, default=2)
    args = parser.parse_args()
    if min(args.workers, args.batch_size) < 1:
        parser.error("Positive worker count and batch size required")
    reg, _, model = verify()
    reg_hash = validation.digest(DEST / "registration.json")
    receipt = verify_binding(reg["backend_verification"])
    local = DEST / "backend-verification.json"
    if not local.exists():
        with local.open("xb") as out:
            out.write(receipt.read_bytes())
    if validation.digest(local) != validation.digest(receipt):
        raise ValueError("Verified solver backend changed")
    for name in ("cases", "certificates", "expansions"):
        (DEST / name).mkdir(exist_ok=True)
    validation.prepare_domain = reuse.prepare
    validation.observations = observe_ordinary_roofs
    for village in reg["villages"]:
        remaining = None
        for workers in (args.workers, max(1, args.workers // 2)):
            remaining = full_domain.run_pass(
                DEST, reg, reg_hash, model, village, workers, args.batch_size, remaining
            )
            if not remaining:
                break
        if remaining:
            raise RuntimeError(f"{village}: {len(remaining)} panels require recovery")
    print("All registered cases closed", flush=True)


if __name__ == "__main__":
    main()
