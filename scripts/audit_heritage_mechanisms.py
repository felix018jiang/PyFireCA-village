"""Isolate source propagation and source/receiver sampling; never optimize a plan."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.roof_diagnostics import (
    extract_roof,
    mechanism_request,
    point_samples,
    quadrature_samples,
    uniform_flux,
)
from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.flame_contact_v3 import FlameContactParameters
from pyfireca.village.solver_v3 import _run_resolved_v3_simulation
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import load_v3_case_bundle, write_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def matrix():
    rows = []
    for probe in ("gulou", "bridge"):
        # Whole-source fire removes source-growth timing entirely. Cross both axes.
        for hs in (5.0, 2.5, 1.0, 0.5):
            for ht in (5.0, 2.5, 1.0, 0.5):
                rows.append(dict(probe=probe, area="all", hs=hs, ht=ht, mode="seed_only"))
        for area in (5.0, 25.0):
            for hs in (1.0, 0.5):
                for ht in (1.0, 0.5):
                    rows.append(dict(probe=probe, area=area, hs=hs, ht=ht, mode="seed_only"))
                for mode in ("source_radiation", "source_contact", "source_both"):
                    rows.append(dict(probe=probe, area=area, hs=hs, ht=0.5, mode=mode))
    for row in rows:
        row["id"] = (
            f"{row['probe']}-A{row['area']}-s{row['hs']}-t{row['ht']}-{row['mode']}"
        ).replace(".", "p")
    return rows


class Observer:
    def __init__(self, source, target):
        self.ns, self.area = source.count, source.parameters.covered_area_m2.ravel()
        self.maximum = np.full(target.count, 300.0)
        self.development, self.trace = {}, []

    def __call__(self, state):
        n = self.ns
        valid = state.temperature_valid_mask.ravel()[n:]
        self.maximum[valid] = np.maximum(
            self.maximum[valid], state.surface_temperature_K.ravel()[n:][valid]
        )
        t = state.time_s
        ignited = np.isfinite(state.ignition_time_s.ravel()[:n])
        for clock in (0, 300, 600, 1200):
            if t >= clock - 1e-7 and str(clock) not in self.development:
                # Use saved exact ignition times rather than treating a nearby sample as exact.
                at_clock = state.ignition_time_s.ravel()[:n] <= clock + 1e-7
                self.development[str(clock)] = float(self.area[at_clock].sum())
        if not self.trace or t >= self.trace[-1]["time_s"] + 18 - 1e-7:
            self.trace.append(
                {
                    "time_s": t,
                    "source_ever_ignited_area_m2": float(self.area[ignited].sum()),
                    "source_interval_mean_hrr_W": float(
                        state.interval_mean_hrr_W.ravel()[:n].sum()
                    ),
                    "source_released_J": float(state.released_energy_J.ravel()[:n].sum()),
                    "core_maximum_valid_temperature_so_far_K": float(self.maximum.max()),
                }
            )
        return False


def run(output, *, probe_filter=None, restore=None):
    aggregate = ROOT / "docs/results/heritage-ignition-scale-20261023.json"
    old = json.loads(aggregate.read_text())["runs"]
    study = ROOT / "runs/research/heritage-geometry-20261023-r2/study.json"
    config = json.loads(study.read_text())
    if config["candidate_ids"] or config["fixed_source_ids"]:
        raise ValueError("real eligibility/source status must remain pending")
    parent = load_v3_case_bundle(study.parent / config["case_dir"])
    transport = V3RadiativeTransportParameters(**parent.metadata["transport_parameters"])
    parent_before = request_fingerprint(parent.request)
    selected = [r for r in matrix() if probe_filter is None or r["probe"] == probe_filter]
    if restore:
        selected = [r for r in selected if r["id"] == restore]
        if len(selected) != 1:
            raise ValueError("restore requires one declared experiment ID")
    else:
        output.mkdir(parents=True, exist_ok=False)
        dump(
            output / "protocol.json",
            {
                "schema_version": "heritage-mechanism-isolation-1",
                "matrix": selected,
                "scope": "two_object_counterfactual_not_protection_evaluation",
                "source_footprints": "unchanged_saved_polygons",
                "materials": "unchanged_saved_arrays",
                "omitted": ["other_objects", "target_feedback", "target_internal_spread"],
                "fixed_study_source_pending": True,
                "candidate_eligibility_pending": True,
                "optimization": False,
                "parent_sha256": parent_before,
                "aggregate_sha256": sha(aggregate),
            },
        )
        for file in (Path(__file__), ROOT / "src/pyfireca/planning/roof_diagnostics.py"):
            dest = output / "source-snapshot" / file.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, dest)
    cache, hashes, rows = {}, {}, []

    def objects(probe, area, h):
        key = (probe, area, h)
        if key in cache:
            return cache[key]
        row = next(
            r
            for r in old
            if r["probe_id"] == probe
            and r["area_m2"] == area
            and r["h_m"] == h
            and r["phase"] == [0.0, 0.0]
            and r["dt_s"] == 1.0
        )
        folder = Path(row["evaluation"]).parent
        layout = json.loads((folder / "layout.json").read_text())
        request = load_v3_case_bundle(folder / "node-storage-not-map").request
        with np.load(folder / "contact.npz") as contact:
            args = (contact["neighbors"], contact["geometry_fraction"])
            source, target = [
                extract_roof(
                    request,
                    layout["roof_components"],
                    identifier,
                    *args,
                    parent_elevation_m=parent.request.ground_elevation_m,
                )
                for identifier in (row["source_id"], row["target_id"])
            ]
        parameters = FlameContactParameters.from_mapping(
            json.loads((folder / "contact.json").read_text())
        )
        # A same-layout construction must reproduce the saved source-to-target operator.
        diagnostic, _ = mechanism_request(
            source, target, request.scenario, transport, parameters, mode="source_both"
        )
        src_ids = [
            p["node"] for p in layout["roof_components"] if p["building_ids"] == [row["source_id"]]
        ]
        dst_ids = [
            p["node"] for p in layout["roof_components"] if p["building_ids"] == [row["target_id"]]
        ]
        reverse = np.full(request.grid.width, -1, int)
        reverse[src_ids + dst_ids] = np.arange(len(src_ids + dst_ids))
        graph = request.exposure_edges
        keep = np.isin(graph.source, src_ids) & np.isin(graph.target, src_ids + dst_ids)
        from pyfireca.village.domain import ExposureEdges

        expected = ExposureEdges(
            reverse[graph.source[keep]],
            reverse[graph.target[keep]],
            graph.weight[keep],
            diagnostic.grid.width,
        )
        actual = diagnostic.exposure_edges
        if (
            not np.array_equal(actual.source, expected.source)
            or not np.array_equal(actual.target, expected.target)
            or not np.allclose(actual.weight, expected.weight, rtol=1e-13, atol=1e-15)
        ):
            raise AssertionError("point reconstruction differs from saved physical operator")
        budget = {
            name: float(getattr(source.parameters, name).ravel()[source.seed].sum())
            for name in row["source_budget"]
        }
        if any(
            not np.isclose(v, row["source_budget"][k], rtol=1e-12, atol=1e-7)
            for k, v in budget.items()
        ):
            raise AssertionError("extraction altered prescribed source budget")
        hashes.update({str(p.relative_to(ROOT)): sha(p) for p in folder.rglob("*") if p.is_file()})
        cache[key] = (source, target, request.scenario, parameters, row)
        return cache[key]

    for row in selected:
        print("Starting " + row["id"], flush=True)
        source, _, scenario, contact_parameters, original = objects(
            row["probe"], row["area"], row["hs"]
        )
        _, target, _, _, _ = objects(row["probe"], row["area"], row["ht"])
        request, contact = mechanism_request(
            source, target, scenario, transport, contact_parameters, mode=row["mode"]
        )
        observer = Observer(source, target)
        result = _run_resolved_v3_simulation(
            request, contact=contact, retain_snapshots=False, stop_when=observer
        )
        observer(result.final_state)
        validation = check_final(request, result)
        final = result.final_state
        core_times = final.ignition_time_s.ravel()[source.count :]
        finite = core_times[np.isfinite(core_times)]
        record = {
            **row,
            "first_core_ignition_s": float(finite.min()) if len(finite) else None,
            "maximum_valid_core_temperature_K": float(observer.maximum.max()),
            "source_ignited_area_m2": observer.development,
            "source_released_J": float(final.released_energy_J.ravel()[: source.count].sum()),
            "prescribed_patch_released_J": float(
                final.released_energy_J.ravel()[: source.count][source.seed].sum()
            ),
            "core_incident_radiation_J": float(
                final.incident_radiation_J.ravel()[source.count :].sum()
            ),
            "core_received_convection_J": float(
                final.received_convection_J.ravel()[source.count :].sum()
            ),
            "prescribed_budget": original["source_budget"],
            "footprint_sha256": original["ignition_geometry_sha256"],
            "request_sha256": request_fingerprint(request),
            "validation": validation,
            "source_count": source.count,
            "core_count": target.count,
        }
        if record["core_received_convection_J"] != 0:
            raise AssertionError("passive receiver acquired disabled contact")
        if row["mode"] == "seed_only" and not np.allclose(
            list(observer.development.values()),
            original["source_budget"]["covered_area_m2"],
            rtol=1e-10,
            atol=1e-7,
        ):
            raise AssertionError("seed-only control acquired secondary source fire")
        folder = output / row["id"]
        if restore:
            saved = json.loads((folder / "result.json").read_text())
            if saved != record:
                raise AssertionError("restored scalar diagnostic differs")
            with np.load(folder / "final-state.npz") as data:
                for key in final.__dataclass_fields__:
                    if not np.array_equal(data[key], getattr(final, key), equal_nan=True):
                        raise AssertionError("restored final field differs: " + key)
            return record
        folder.mkdir()
        dump(folder / "result.json", record)
        dump(folder / "trace.json", observer.trace)
        np.savez_compressed(
            folder / "final-state.npz",
            **{k: getattr(final, k) for k in final.__dataclass_fields__},
            ignition_cause=result.ignition_cause,
        )
        # Resolveable compact arrays: use the saved contact topology, never 1xN adjacency.
        write_v3_case_bundle(
            request,
            folder / "node-storage-not-map",
            metadata={
                "model": "heritage-mechanism-isolation-1",
                "storage_grid_is_geographic": False,
                "not_a_protection_evaluation": True,
                "requires_saved_contact_topology": contact is not None,
            },
        )
        if contact is not None:
            np.savez_compressed(
                folder / "contact.npz",
                neighbors=contact.neighbors,
                geometry_fraction=contact.geometry_fraction,
            )
            dump(folder / "contact.json", contact.parameters.to_mapping())
        rows.append(record)
        dump(output / "summary.json", {"runs": rows, "complete": len(rows) == len(selected)})
        print(
            f"  core {record['first_core_ignition_s']}; "
            f"T {record['maximum_valid_core_temperature_K']:.3f}; "
            f"source600 {observer.development['600']:.3f}",
            flush=True,
        )

    # Common quadrature is an integration reference, not a changed material/thermal model.
    quadrature = []
    for probe in ("gulou", "bridge"):
        if probe_filter and probe != probe_filter:
            continue
        for area in (5.0, 25.0, "all"):
            print(f"Quadrature {probe} A{area}", flush=True)
            source, target, _, _, _ = objects(probe, area, 0.5)
            tq = quadrature_samples(target, 0.25, seed_only=False)
            references = {}
            for hs in (0.5, 0.25, 0.125):
                sq = quadrature_samples(source, hs, seed_only=True)
                flux = uniform_flux(sq, tq, transport)
                references[str(hs)] = {
                    "source_points": len(sq["xy_m"]),
                    "target_points": len(tq["xy_m"]),
                    "maximum_unit_flux": float(flux.max()),
                    "area_mean_unit_flux": float(np.average(flux, weights=tq["area_m2"])),
                }
            sample_rows = []
            for hs in (5.0, 2.5, 1.0, 0.5) if area == "all" else (1.0, 0.5):
                source, _, _, _, _ = objects(probe, area, hs)
                flux = uniform_flux(point_samples(source, seed_only=True), tq, transport)
                sample_rows.append(
                    {
                        "source_h_m": hs,
                        "receiver": "common_0.25m_quadrature",
                        "maximum_unit_flux": float(flux.max()),
                        "area_mean_unit_flux": float(np.average(flux, weights=tq["area_m2"])),
                    }
                )
            # Hold source integration fixed and move receiver sampling only.
            source, _, _, _, _ = objects(probe, area, 0.5)
            sq = quadrature_samples(source, 0.125, seed_only=True)
            for ht in (5.0, 2.5, 1.0, 0.5) if area == "all" else (1.0, 0.5):
                _, target, _, _, _ = objects(probe, area, ht)
                pts = point_samples(target)
                flux = uniform_flux(sq, pts, transport)
                sample_rows.append(
                    {
                        "source": "common_0.125m_quadrature",
                        "receiver_h_m": ht,
                        "maximum_unit_flux": float(flux.max()),
                        "area_mean_unit_flux": float(np.average(flux, weights=pts["area_m2"])),
                    }
                )
            quadrature.append(
                {
                    "probe": probe,
                    "area": area,
                    "references": references,
                    "axis_controls": sample_rows,
                }
            )
            dump(output / "quadrature.json", quadrature)
    if request_fingerprint(parent.request) != parent_before:
        raise AssertionError("diagnostics mutated parent physics")
    if any(sha(ROOT / p) != value for p, value in hashes.items()):
        raise AssertionError("diagnostics mutated prior saved experiment")
    dump(output / "input-hashes.json", hashes)
    dump(
        output / "summary.json",
        {
            "runs": rows,
            "quadrature": quadrature,
            "complete": True,
            "input_files_unchanged": len(hashes),
            "parent_sha256": parent_before,
            "not_a_protection_evaluation": True,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe", choices=("gulou", "bridge"))
    parser.add_argument(
        "--restore", help="Recompute and verify one experiment in its existing output"
    )
    args = parser.parse_args()
    run(args.output.resolve(), probe_filter=args.probe, restore=args.restore)
