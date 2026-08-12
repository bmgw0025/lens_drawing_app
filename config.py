# config.py

DEFAULTS = {
    "T":   4.70,
    "R1":  -35.0,
    "R2":  30.0,
    "MD":  19.00,
    "AD1": 15.00,
    "AD2": 13.00,
}


def _is_finite_number(value):
    import math
    return (not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value))


def validate(T, R1, R2, MD, AD1, AD2):
    """校验单片几何参数，返回可直接展示给用户的错误列表。"""
    errors = []
    values = {
        "T": T, "R1": R1, "R2": R2,
        "MD": MD, "AD1": AD1, "AD2": AD2,
    }
    invalid_names = [name for name, value in values.items()
                     if not _is_finite_number(value)]
    if invalid_names:
        errors.append(f"{', '.join(invalid_names)} 必须是有限数值")
        return errors

    if T <= 0:
        errors.append("T 必须大于 0")
    if MD <= 0:
        errors.append("MD 必须大于 0")
    if AD1 <= 0:
        errors.append("AD1 必须大于 0")
    if AD2 <= 0:
        errors.append("AD2 必须大于 0")
    if MD > 0 and AD1 > MD:
        errors.append("AD1 不能大于 MD")
    if MD > 0 and AD2 > MD:
        errors.append("AD2 不能大于 MD")

    # R=0 表示平面。非平面曲率必须能覆盖对应口径。
    if AD1 > 0 and abs(R1) > 1e-9 and abs(R1) <= AD1 / 2.0:
        errors.append("|R1| 必须大于 AD1/2，否则无法形成有效圆弧")
    if AD2 > 0 and abs(R2) > 1e-9 and abs(R2) <= AD2 / 2.0:
        errors.append("|R2| 必须大于 AD2/2，否则无法形成有效圆弧")

    # 在口径边缘处右表面必须位于左表面右侧，否则轮廓会反向或自交。
    if not errors:
        from geometry import calc_points
        _, B, _, _, E, _ = calc_points(T, R1, R2, MD, AD1, AD2)
        edge_thickness = E[0] - B[0]
        if edge_thickness <= 1e-9:
            errors.append(
                f"边缘厚度必须大于 0（当前计算值 {edge_thickness:.3f} mm），"
                "请检查 T、R 和 AD"
            )
    return errors


def validate_cemented_lenses(lenses):
    """校验 1~3 片镜片及相邻胶合面的曲率连续性。"""
    import math

    errors = []
    if not isinstance(lenses, (list, tuple)) or not 1 <= len(lenses) <= 3:
        return ["镜片数量必须为 1~3 片"]

    for index, lens in enumerate(lenses, start=1):
        lens_errors = validate(
            lens.T, lens.R_left, lens.R_right,
            lens.MD, lens.AD_left, lens.AD_right,
        )
        for message in lens_errors:
            semantic_message = (message
                                .replace("AD1", "左侧 AD")
                                .replace("AD2", "右侧 AD"))
            errors.append(f"镜片{index}: {semantic_message}")

    for index in range(len(lenses) - 1):
        right_radius = lenses[index].R_right
        left_radius = lenses[index + 1].R_left
        if (_is_finite_number(right_radius) and _is_finite_number(left_radius)
                and not math.isclose(right_radius, left_radius,
                                     rel_tol=1e-9, abs_tol=1e-9)):
            errors.append(
                f"胶合面{index + 1}曲率不连续："
                f"镜片{index + 1}右面 R={right_radius:g}，"
                f"镜片{index + 2}左面 R={left_radius:g}"
            )
    return errors


def auto_chamfer(MD, R1, R2):
    """
    Auto-calculate chamfer values based on diameter and curvature rules.
    
    Rules:
      D ≤ 30     → 0.2
      30 < D ≤ 80 → 0.3
      D > 80     → 0.4
    
    Special rule (仅双凸/双凹):
      若 ||R1| - |R2|| / min(|R1|, |R2|) ≤ 70%
      → left=0.2, right=0.4 (ignore diameter tier)
    """
    # Basic diameter rule
    if MD <= 30:
        c_left = c_right = 0.2
    elif MD <= 80:
        c_left = c_right = 0.3
    else:
        c_left = c_right = 0.4
    
    # Special curvature rule (仅双凸 R1>0,R2<0 或双凹 R1<0,R2>0)
    # 当 |R1| == |R2| 时完全对称，不需要区分左右，直接用直径规则
    is_biconvex = (R1 > 0 and R2 < 0)
    is_biconcave = (R1 < 0 and R2 > 0)
    if is_biconvex or is_biconcave:
        r1_abs = abs(R1)
        r2_abs = abs(R2)
        if r1_abs > 0 and r2_abs > 0 and r1_abs != r2_abs:
            ratio = abs(r1_abs - r2_abs) / min(r1_abs, r2_abs)
            if ratio <= 0.70:
                c_left = 0.2
                c_right = 0.4
    
    return c_left, c_right


def auto_chamfer_by_dia(MD):
    """
    Auto-calculate chamfer values based on diameter only (no left/right distinction).
    Used for cemented lens individual pages.
    
    Rules:
      D ≤ 30     → 0.2
      30 < D ≤ 80 → 0.3
      D > 80     → 0.4
    """
    if MD <= 30:
        c = 0.2
    elif MD <= 80:
        c = 0.3
    else:
        c = 0.4
    return c


def auto_CA(AD, ratio=0.94):
    """
    Auto-calculate Clear Aperture (净口径 CA) based on AD.
    CA = ratio * AD，向下取一位有效小数。
    例：AD=92, ratio=0.98 → CA=90.1
    """
    import math
    raw = ratio * AD
    # 向下取一位有效小数：floor(raw * 10) / 10
    ca = math.floor(raw * 10) / 10.0
    return ca


def auto_N(MD):
    """
    Auto-calculate 光圈数 N based on diameter MD.
    规则：
      MD ≤ 15       → N = 1.5
      15 < MD ≤ 30  → N = 2
      30 < MD ≤ 80  → N = 3
      80 < MD ≤ 130 → N = 4
      130 < MD ≤ 180→ N = 6
      MD > 180      → N = 8
    """
    if MD <= 15:
        return 1.5
    elif MD <= 30:
        return 2
    elif MD <= 80:
        return 3
    elif MD <= 130:
        return 4
    elif MD <= 180:
        return 6
    else:
        return 8
