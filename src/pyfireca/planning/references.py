"""Independent continuum references for restricted, isotropic D0 scenes.

No raster solver or Shapely intersection is used here. These references are
mathematical values under specified constant rates, never wildfire truth.
"""

from math import cos, hypot, radians, sin, sqrt

from pyfireca.planning.contracts import PointXY, require_number, require_point


def rotate_point(point: PointXY, angle_deg: float) -> PointXY:
    """Rigid rotation about the local origin, preserving physical lengths."""
    require_point(point)
    require_point((angle_deg, 0))
    angle = radians(angle_deg)
    return (
        cos(angle) * point[0] - sin(angle) * point[1],
        sin(angle) * point[0] + cos(angle) * point[1],
    )


def _opposite_points(source: PointXY, target: PointXY, width: float) -> None:
    require_point(source)
    require_point(target)
    require_number("width", width, positive=True)
    if not source[0] < -width / 2 or not target[0] > width / 2:
        raise ValueError("reference requires source left of strip and target right of strip")


def rectangle_detour_infimum_s(
    source: PointXY,
    target: PointXY,
    width_m: float,
    half_length_m: float,
    background_rate_m_s: float = 1,
) -> float:
    """Shortest-time infimum around a closed rectangle in strip coordinates.

    Both points must lie within its tangential extent and on opposite normal
    sides. The two candidates travel via the upper or lower corner pair. Their
    boundary-following paths are limiting values: M0 forbids actual contact,
    but arbitrarily close exterior paths approach these lengths.
    """
    _opposite_points(source, target, width_m)
    require_number("half_length_m", half_length_m, positive=True)
    require_number("background_rate_m_s", background_rate_m_s, positive=True)
    if abs(source[1]) >= half_length_m or abs(target[1]) >= half_length_m:
        raise ValueError("reference requires points within the strip's tangential extent")
    lengths = [
        hypot(source[0] + width_m / 2, source[1] - y)
        + width_m
        + hypot(target[0] - width_m / 2, target[1] - y)
        for y in (-half_length_m, half_length_m)
    ]
    return min(lengths) / background_rate_m_s


def parallel_band_time_s(
    source: PointXY,
    target: PointXY,
    width_m: float,
    background_rate_m_s: float,
    treated_rate_m_s: float,
) -> float:
    """Fermat/Snell minimum through an infinite parallel constant-rate band.

    Minimize sum(hypot(dx_k,dy_k)/R_k) subject to sum(dy_k)=target_y-source_y.
    The unique convex optimum has dy_k=dx_k*p*R_k/sqrt(1-(p*R_k)^2).
    Bisection solves for the common tangential slowness p. Finite bands require
    a separate check that end bypass cannot improve this reference.
    """
    _opposite_points(source, target, width_m)
    require_number("background_rate_m_s", background_rate_m_s, positive=True)
    require_number("treated_rate_m_s", treated_rate_m_s, positive=True)
    dx = (-width_m / 2 - source[0], width_m, target[0] - width_m / 2)
    rates = (background_rate_m_s, treated_rate_m_s, background_rate_m_s)
    dy = abs(target[1] - source[1])
    low, high = 0.0, (1 - 1e-14) / max(rates)

    def displacements(p):
        return tuple(
            x * (p * rate) / sqrt(1 - (p * rate) ** 2) for x, rate in zip(dx, rates, strict=True)
        )

    if sum(displacements(high)) < dy:
        raise ValueError("reference displacement exceeds representable precision")
    for _ in range(80):
        middle = (low + high) / 2
        if sum(displacements(middle)) < dy:
            low = middle
        else:
            high = middle
    transverse = displacements((low + high) / 2)
    return sum(hypot(x, y) / rate for x, y, rate in zip(dx, transverse, rates, strict=True))


def octile_time_s(source: PointXY, target: PointXY, rate_m_s: float = 1) -> float:
    """Independent exact full-domain Moore-graph baseline for mapped centres."""
    require_point(source)
    require_point(target)
    require_number("rate_m_s", rate_m_s, positive=True)
    dx, dy = abs(target[0] - source[0]), abs(target[1] - source[1])
    return (max(dx, dy) + (sqrt(2) - 1) * min(dx, dy)) / rate_m_s
