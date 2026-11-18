"""A total retained-source rule for bounded whole-village design searches."""

import numpy as np

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.random_sources import freeze_source_protocol


def with_global_fallback(original, inventory, *, fallback_seed):
    if original["inventory_sha256"] != inventory.sha256:
        raise ValueError("source protocol inventory mismatch")
    all_sources = freeze_source_protocol(
        inventory,
        seed=fallback_seed,
        draws_per_stratum=1,
        area_m2=original["ignition_area_m2"],
        near_distance_m=original["near_distance_m"],
    )
    pool = sorted(all_sources["members"])
    draws = []
    for draw in original["draws"]:
        prefix = draw["priority_ids"]
        if not set(prefix) <= set(pool):
            raise ValueError("preferred sources are not valid in the full-village pool")
        digest = content_hash([fallback_seed, draw["draw_id"]])
        rng = np.random.Generator(np.random.PCG64(int(digest[:16], 16)))
        suffix = rng.permutation([i for i in pool if i not in prefix]).tolist()
        draws.append(
            {
                **draw,
                "preferred_stratum": draw["stratum"],
                "stratum": "all_retained_sources",
                "priority_ids": prefix + suffix,
            }
        )
    value = {
        **original,
        "parent_protocol_sha256": original["protocol_sha256"],
        "fallback_seed": fallback_seed,
        "strata": {"all_retained_sources": pool},
        "members": all_sources["members"],
        "draws": draws,
        "sampling": "frozen_preferred_priority_then_global_fallback;not_uniform_village_risk",
        "source_condition": "one_fire_in_retained_eligible_village_buildings",
        "weight_policy": "design_constraints_only;no_weighted_population_risk",
    }
    value.pop("protocol_sha256")
    value["protocol_sha256"] = content_hash(value)
    return value
