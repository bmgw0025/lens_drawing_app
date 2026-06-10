"""
Settings management for Lens Drawing Application.
Persists user preferences to JSON file.
"""
import json
import os

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_settings.json")

DEFAULT_SETTINGS = {
    "J_multiplier": 0.10,
    "font_size": 9,
    "arrow_scale": 1.0,
    "ct_offset_J": 10.0,
    "et_offset_J": 8.0,
    "sag_offset_J": 8.0,
    "dia_offset_J": 8.0,
    "ad_offset_J": 6.0,
    "spray_gap_J": 0.2,
    "chamfer_mode": "auto",
    "chamfer_left": 0.2,
    "chamfer_right": 0.4,
    "t_tol": 0.02,
    "sag_tol": 0.02,
    # ── 直径公差（新键：定位/非定位分离；旧键保留向后兼容）──
    "dia_tol_upper": 0.010,              # 旧键，映射为 dia_tol_pos_upper
    "dia_tol_lower": 0.025,              # 旧键，映射为 dia_tol_pos_lower
    "dia_tol_pos_upper": 0.010,          # 直径定位公差上偏差
    "dia_tol_pos_lower": 0.025,          # 直径定位公差下偏差
    "dia_tol_nonpos_upper": 0.05,        # 直径非定位公差上偏差
    "dia_tol_nonpos_lower": 0.10,        # 直径非定位公差下偏差
    "cemented_ref_lens": 2,              # 胶合定位镜片序号（1-based, 默认第2片）
    "r_offset_J": 5.5,
    # 加工要求默认值
    "proc_c_single": "60″",              # 单页C（所有单片页使用）
    "proc_c_assembly": "60″",            # 胶合页C（仅胶合整体页使用）
    "proc_surface_defect": "60/40",       # 变量5：表面瑕疵B
    "proc_N_mode": "auto",               # N模式：auto=自动计算, manual=手动输入
    "proc_N_manual": "1.5",              # 手动N默认值
    "proc_DN": "0.3",                    # ΔN默认值
    "proc_signature": "l.y.h",            # 变量18：出图人署名
    "proc_vendor": "CDGM",                # 变量23：玻璃厂商
    "proc_ranking": "01",                 # 变量24：玻璃品级
    # 镀膜预设
    "coat_preset": "SQ-A1",
    # 镀膜波段默认值
    "coat_s1_wave1": "420-680",
    "coat_s1_wave2": "850/940",
    "coat_s2_wave1": "420-680",
    "coat_s2_wave2": "850/940",
    # 反射率默认值
    "coat_s1_ravg1": "0.5",
    "coat_s1_ravg2": "1",
    "coat_s2_ravg1": "0.5",
    "coat_s2_ravg2": "1",
    # 透过角度默认值
    "coat_s1_angle1": "0-22",
    "coat_s1_angle2": "0-22",
    "coat_s2_angle1": "0-22",
    "coat_s2_angle2": "0-22",
    # CA自动计算系数
    "ca_ratio": 0.94,
}


def load_settings():
    """Load settings from JSON file, return defaults if not found."""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # Merge with defaults (in case new keys added)
            result = DEFAULT_SETTINGS.copy()
            result.update(saved)
            # ── 自动迁移：旧键 → 新键（仅当新键不存在时） ──
            if "dia_tol_pos_upper" not in saved and "dia_tol_upper" in saved:
                result["dia_tol_pos_upper"] = saved["dia_tol_upper"]
            if "dia_tol_pos_lower" not in saved and "dia_tol_lower" in saved:
                result["dia_tol_pos_lower"] = saved["dia_tol_lower"]
            return result
        except Exception:
            return DEFAULT_SETTINGS.copy()
    return DEFAULT_SETTINGS.copy()


def save_settings(settings):
    """Save settings to JSON file."""
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
