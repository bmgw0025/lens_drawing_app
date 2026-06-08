# config.py

DEFAULTS = {
    "T":   4.70,
    "R1":  -35.0,
    "R2":  30.0,
    "MD":  19.00,
    "AD1": 15.00,
    "AD2": 13.00,
}


def validate(T, R1, R2, MD, AD1, AD2):
    """Validate parameters. Returns list of error strings (empty = valid)."""
    import math
    errors = []
    if T <= 0:
        errors.append("T must be greater than 0")
    if MD <= 0:
        errors.append("MD must be greater than 0")
    if AD1 <= 0 or AD2 <= 0:
        errors.append("AD1 and AD2 must be greater than 0")
    if AD1 > MD or AD2 > MD:
        errors.append("AD cannot exceed MD")
    if abs(R1) > 0 and abs(R1) <= AD1 / 2.0:
        errors.append("|R1| must be greater than AD1/2 for valid arc")
    if abs(R2) > 0 and abs(R2) <= AD2 / 2.0:
        errors.append("|R2| must be greater than AD2/2 for valid arc")
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


def auto_CA(AD, ratio=0.98):
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
