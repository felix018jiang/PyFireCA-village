"""Declare the original four coarse zero-core-only comparisons in a separate budget."""

from pathlib import Path

from prepare_heritage_joint_longcap import encoded, immutable
from run_heritage_joint_comparison import make_oracle, validate_config
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import read_json, file_sha256


def prepare(root):
    folder = root / "examples/research/heritage/area-20261110"
    res = root / "docs/results"
    base = folder / "pingyan-zero-core-heat-and-design-loss-native-registration-20261111.json"
    path = folder / "pingyan-zero-core-only-coarse-control-20261111.json"
    record_path = res / "heritage-pingyan-zero-heat-and-design-loss-independent-20261111.json"
    pre = read_json(base)
    record = read_json(record_path)
    if record["receipt_sha256"] != content_hash(
        {k: v for k, v in record.items() if k != "receipt_sha256"}
    ):
        raise ValueError("control audit seal changed")
    source = (
        root
        / "runs/research/heritage-thermal-and-geometric-pareto-pingyan-20261111/experiment.json"
    )
    original = read_json(source)
    selected = next(
        p
        for p in original["config"]["plans"]
        if p["plan_id"] == "minimum-ground-centre-core-clearance"
    )
    (final_plan,) = pre["plans"]
    reference = original["problem"]["fires"]
    for f in pre["source_fires"]:
        fire = reference[f["fire_id"]]
        ranks = fire["protocol"]["draws"][0]["priority_ids"]
        actual = {
            next(i for i in ranks if i not in p["demolished_ids"]) for p in [final_plan, selected]
        }
        if len(actual) != 1 or not actual <= set(fire["footprints"]):
            raise ValueError("control switches realized source")
    pre.update(
        plans=[selected],
        execution_priority_plan_ids=[selected["plan_id"]],
        max_new_checks=4,
        max_total_checks=8,
        execution_case_selection=[
            {"plan_id": selected["plan_id"], "scenario_id": f["fire_id"] + "-h1"}
            for f in pre["source_fires"]
        ],
        scope="Zero-core-only physical control on the four original coarse design supports in their registered order. Four permitted native checks out of the original two-scale eight-case domain, stopping search if a valid full endpoint or a rigorous temporal-prefix lower bound violates A<=241.49 or N_lower<=3. Source/meshes frozen jointly with 9865 construction; no independent source draw or previously reported loss imported.",
    )
    pre["additional_frozen_inputs_sha256"].update(
        {str(x): file_sha256(x) for x in [base, source, Path(__file__).resolve(), record_path]}
    )
    validate_config(pre)
    oracle, _ = make_oracle(path, pre, file_sha256(Path(__file__)))
    final, _ = make_oracle(base, read_json(base), file_sha256(Path(__file__)))
    if (
        oracle.problem["fires"] != final.problem["fires"]
        or oracle.problem["case_sha256"] != final.problem["case_sha256"]
    ):
        raise ValueError("different fixed coarse geometry")
    receipt = dict(
        schema_version="separate-four-coarse-zero-core-base-control-1",
        base_preparation_SHA256=file_sha256(base),
        final_analytic_audit_SHA256=file_sha256(record_path),
        full_common_source_footprints_layout_and_quadrature_specs_sha256=content_hash(
            oracle.problem["fires"]
        ),
        comparison_produces_four_identical_25m2_footprints=True,
        freeze_integration_membership=oracle.problem["freeze_integration_membership"],
        native_incumbent_verification_slots_separate=8,
        control_budget_slots=4,
        declared_coarse_scale_m=1,
        first_witness_condition="A durable saved burnout area lower bound strictly above the certified ceiling, or more than 3 distinct positively and uniquely attributed original burned owners. A wide annotation upper bound alone is insufficient for an unconditional count.",
        stopping_rule="Design ordinal order. A single qualifying material witness falsifies the corresponding bound. Inconclusive and invalid cases preserve their status; all unobserved cases remain unknown.",
        no_native_evaluations_launched=True,
        method_sha256=file_sha256(__file__),
    )
    receipt["receipt_sha256"] = content_hash(receipt)
    prefile = res / "heritage-pingyan-zero-core-only-coarse-control-registration-20261111.json"
    immutable(prefile, encoded(receipt))
    pre["additional_frozen_inputs_sha256"][str(prefile)] = file_sha256(prefile)
    immutable(path, encoded(pre))
    print("registered separate coarse control, 4 permitted budget slots", flush=True)


if __name__ == "__main__":
    prepare(Path(__file__).resolve().parents[1])
