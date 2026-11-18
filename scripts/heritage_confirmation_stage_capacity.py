"""Count all fixed plans' actual mask stages in one bounded geometric pass."""

import numpy as np
from shapely import STRtree, points


def stage_counts(layout, transport, refined_ids, plans):
    # One bit per builder invocation mask; no spatial node is removed from the mesh.
    stages = {}
    for plan in plans:
        removed = set(plan.demolished_ids)
        active = np.array([not (ids and set(ids) <= removed) for ids in layout.owners], bool)
        stages[plan.plan_id, "after_roofs_before_grass"] = active
        stages[plan.plan_id, "after_roofs_and_grass"] = active & ~np.isin(
            layout.cell_id, plan.cleared_physical_cell_ids
        )
    if len(stages) > 8:
        raise ValueError("bit histogram supports up to four fixed paired-stage plans")
    bitmask = np.zeros(len(layout.owners), dtype=np.uint8)
    for i, active in enumerate(stages.values()):
        bitmask[active] |= 1 << i
    selected = np.array([bool(set(ids) & set(refined_ids)) for ids in layout.owners])
    src = np.flatnonzero(layout.thermal.source_mask)
    tgt = np.flatnonzero(layout.thermal.target_mask)
    bins = 1 << len(stages)
    raw, ordinary, integrated = (np.zeros(bins, dtype=np.int64) for _ in range(3))
    pt = points(layout.xy_m)
    polygons = np.asarray(layout.geometry, dtype=object)

    def accumulate(sources, targets, geometry, point_query):
        if not len(sources) or not len(targets):
            return
        tree = STRtree(geometry[targets])
        for start in range(0, len(sources), 128):
            ids = sources[start : start + 128]
            pair = tree.query(
                geometry[ids], predicate="dwithin", distance=transport.exposure_radius_m
            )
            s, t = ids[pair[0]], targets[pair[1]]
            good = s != t
            s, t = s[good], t[good]
            shared = bitmask[s] & bitmask[t]
            counts = np.bincount(shared, minlength=bins)
            if point_query:
                raw[:] += counts
                plain = ~selected[s] & ~selected[t]
                ordinary[:] += np.bincount(shared[plain], minlength=bins)
            else:
                integrated[:] += counts

    accumulate(src, tgt, pt, True)
    accumulate(src[selected[src]], tgt, polygons, False)
    accumulate(src[~selected[src]], tgt[selected[tgt]], polygons, False)
    upper = ordinary + integrated
    bin_ids = np.arange(bins)
    rows = {}
    for i, (plan_id, stage) in enumerate(stages):
        valid = (bin_ids & (1 << i)) > 0
        rows.setdefault(plan_id, {})[stage] = {
            "raw_point_edges": int(raw[valid].sum()),
            "integrated_edges_upper_bound": int(upper[valid].sum()),
        }
    return {
        "raw_point_edges": int(raw.sum()),
        "integrated_edges_upper_bound": int(upper.sum()),
    }, rows
