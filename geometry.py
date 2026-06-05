# geometry.py
import math
from typing import Tuple, List, Dict, Any


def calc_points(T: float, R1: float, R2: float,
                MD: float, AD1: float, AD2: float):
    """
    Calculate key profile points.

    Coordinate system:
      - X: horizontal, left surface vertex A at origin (0,0)
      - Y: vertical, upward positive
      - Light travels left → right

    Sign convention:
      R1 > 0 : left surface convex (center of curvature to the RIGHT of A)
      R1 < 0 : left surface concave (center of curvature to the LEFT of A)
      R2 < 0 : right surface convex (center of curvature to the LEFT of D)
      R2 > 0 : right surface concave (center of curvature to the RIGHT of D)
    """
    A = (0.0, 0.0)
    D = (float(T), 0.0)

    # ---- Left surface: B = intersection of left sphere with y = AD1/2 ----
    if abs(R1) < 1e-9:
        # Plano surface: vertical line at x=0, B directly above A
        Bx = 0.0
    else:
        r1 = abs(R1)
        cx1 = R1          # center of left sphere on X-axis
        dx1 = math.sqrt(r1 * r1 - (AD1 / 2.0) ** 2)
        if R1 > 0:
            Bx = cx1 - dx1   # left intersection (closer to A)
        else:
            Bx = cx1 + dx1   # right intersection (closer to A)
    B = (Bx, AD1 / 2.0)
    C = (Bx, MD / 2.0)

    # ---- Right surface: E = intersection of right sphere with y = AD2/2 ----
    if abs(R2) < 1e-9:
        # Plano surface: vertical line at x=T, E directly above D
        Ex = float(T)
    else:
        r2 = abs(R2)
        cx2 = float(T) + R2   # center of right sphere on X-axis
        dx2 = math.sqrt(r2 * r2 - (AD2 / 2.0) ** 2)
        if R2 < 0:
            Ex = cx2 + dx2   # right intersection (closer to D)
        else:
            Ex = cx2 - dx2   # left intersection (closer to D)
    E = (Ex, AD2 / 2.0)
    F = (Ex, MD / 2.0)

    return A, B, C, D, E, F


def arc_points_between(cx: float, cy: float, r: float,
                       p_start: Tuple[float, float],
                       p_end: Tuple[float, float],
                       n: int = 200) -> Tuple[List[float], List[float]]:
    """
    Generate n+1 points along the SHORT arc of circle (cx,cy,r)
    from p_start to p_end, going the shorter way around the circle.
    """
    a_start = math.degrees(math.atan2(p_start[1] - cy, p_start[0] - cx))
    a_end = math.degrees(math.atan2(p_end[1] - cy, p_end[0] - cx))
    
    delta = a_end - a_start
    while delta > 180:
        delta -= 360
    while delta < -180:
        delta += 360
    
    angles = []
    for i in range(n + 1):
        t = i / n
        a = a_start + delta * t
        angles.append(math.radians(a))
    
    xs = [cx + r * math.cos(a) for a in angles]
    ys = [cy + r * math.sin(a) for a in angles]
    return xs, ys


def build_profile(T: float, R1: float, R2: float,
                  MD: float, AD1: float, AD2: float):
    """
    Build upper-half lens profile segments.

    Returns:
        segments : list of ('arc'|'line', xs, ys)
        pts      : dict with keys A B C D E F
    """
    A, B, C, D, E, F = calc_points(T, R1, R2, MD, AD1, AD2)
    upper = []

    # ---- Left surface: A → B ----
    if abs(R1) < 1e-9:
        # Plano: vertical line from A(0,0) to B(Bx, AD1/2)
        upper.append(("line", [A[0], B[0]], [A[1], B[1]]))
    else:
        cx1 = R1
        r1 = abs(R1)
        xs_ab, ys_ab = arc_points_between(cx1, 0.0, r1, A, B, n=200)
        upper.append(("arc", xs_ab, ys_ab))

    # ---- Line B → C (vertical edge, left side) ----
    if abs(C[1] - B[1]) > 1e-9:
        upper.append(("line", [B[0], C[0]], [B[1], C[1]]))

    # ---- Line C → F (horizontal top edge) ----
    upper.append(("line", [C[0], F[0]], [C[1], F[1]]))

    # ---- Line F → E (vertical edge, right side) ----
    if abs(F[1] - E[1]) > 1e-9:
        upper.append(("line", [F[0], E[0]], [F[1], E[1]]))

    # ---- Right surface: E → D ----
    if abs(R2) < 1e-9:
        # Plano: vertical line from E(Ex, AD2/2) to D(T, 0)
        upper.append(("line", [E[0], D[0]], [E[1], D[1]]))
    else:
        cx2 = float(T) + R2
        r2 = abs(R2)
        xs_ed, ys_ed = arc_points_between(cx2, 0.0, r2, E, D, n=200)
        upper.append(("arc", xs_ed, ys_ed))

    pts = {"A": A, "B": B, "C": C, "D": D, "E": E, "F": F}
    return upper, pts
