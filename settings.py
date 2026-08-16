"""
Settings management for Lens Drawing Application.
Persists user preferences to JSON file.
"""
import json
import os
import math
import sys
from copy import deepcopy


def _settings_file_path():
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local"
        )
        return os.path.join(base, "LensDrawing", "app_settings.json")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_settings.json")


SETTINGS_FILE = _settings_file_path()

DEFAULT_SETTINGS = {
    "J_multiplier": 0.10,
    "font_size": 9,
    "arrow_scale": 0.3,
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
    "proc_molding": "Molding",            # Scribe&Break/Molding 右侧值
    "proc_chipping": "0.2",
    "proc_roughness": "0.01",
    "proc_ink_brand": "GT-7II",
    "proc_ink_proportion": "8: 1: 9(Paint: Curing agent: Diluent)",
    "proc_ink_thickness": "3~5um",
    "proc_spraying_position": "Arrow indication The dashed line",
    "proc_dimensions_rule": "According to the drawing",
    "proc_ink_leakage": "0.1",
    "special_notes": "",
    # 镀膜预设
    "coat_preset": "SQ-A1",
    # 镀膜波段默认值
    "coat_s1_wave1": "420-680",
    "coat_s1_wave2": "680-850",
    "coat_s2_wave1": "420-680",
    "coat_s2_wave2": "680-850",
    # 反射率默认值
    "coat_s1_ravg1": "0.4",
    "coat_s1_ravg2": "0.8",
    "coat_s2_ravg1": "0.4",
    "coat_s2_ravg2": "0.8",
    # 透过角度默认值
    "coat_s1_angle1": "0-15",
    "coat_s1_angle2": "0-15",
    "coat_s2_angle1": "0-15",
    "coat_s2_angle2": "0-15",
    # CA自动计算系数
    "ca_ratio": 0.94,
}


def get_agent_default_settings():
    """Return the immutable V4 Agent baseline, never persisted GUI preferences."""
    return deepcopy(DEFAULT_SETTINGS)


def validate_settings_updates(updates):
    """Validate a partial settings payload and return normalized values."""
    if not isinstance(updates, dict):
        raise ValueError("设置内容必须是对象")

    unknown = sorted(set(updates) - set(DEFAULT_SETTINGS))
    if unknown:
        raise ValueError(f"包含未知设置项: {', '.join(unknown)}")

    normalized = {}
    numeric_keys = {
        key for key, default in DEFAULT_SETTINGS.items()
        if isinstance(default, (int, float)) and not isinstance(default, bool)
    }
    strictly_positive = {"J_multiplier", "font_size", "arrow_scale", "ca_ratio"}
    nonnegative = {
        "ct_offset_J", "et_offset_J", "sag_offset_J", "dia_offset_J",
        "ad_offset_J", "r_offset_J", "spray_gap_J",
        "chamfer_left", "chamfer_right", "t_tol", "sag_tol",
        "dia_tol_upper", "dia_tol_lower",
        "dia_tol_pos_upper", "dia_tol_pos_lower",
        "dia_tol_nonpos_upper", "dia_tol_nonpos_lower",
    }

    for key, raw in updates.items():
        if key in numeric_keys:
            if isinstance(raw, bool):
                raise ValueError(f"设置 {key} 必须是数值")
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"设置 {key} 必须是数值")
            if not math.isfinite(value):
                raise ValueError(f"设置 {key} 必须是有限数值")
            if key in strictly_positive and value <= 0:
                raise ValueError(f"设置 {key} 必须大于 0")
            if key in nonnegative and value < 0:
                raise ValueError(f"设置 {key} 不能为负数")
            if key == "ca_ratio" and value > 1:
                raise ValueError("设置 ca_ratio 不能大于 1")
            if key == "cemented_ref_lens":
                if not value.is_integer() or not 1 <= int(value) <= 3:
                    raise ValueError("胶合定位镜片必须是 1~3 的整数")
                normalized[key] = int(value)
            else:
                normalized[key] = value
            continue

        if raw is None:
            raise ValueError(f"设置 {key} 不能为空")
        value = str(raw)
        if key == "coat_preset" and value == "SQ-A3":
            value = "SQ-A6"
        normalized[key] = value

    if normalized.get("chamfer_mode", DEFAULT_SETTINGS["chamfer_mode"]) not in ("auto", "manual"):
        raise ValueError("倒角模式无效")
    if normalized.get("proc_N_mode", DEFAULT_SETTINGS["proc_N_mode"]) not in ("auto", "manual"):
        raise ValueError("N 模式无效")
    if "proc_N_manual" in normalized:
        try:
            n_value = float(normalized["proc_N_manual"])
        except (TypeError, ValueError):
            raise ValueError("手动 N 必须是数值")
        if not math.isfinite(n_value) or n_value <= 0:
            raise ValueError("手动 N 必须是大于 0 的有限数值")

    return normalized


def load_settings():
    """Load settings from JSON file, return defaults if not found."""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if not isinstance(saved, dict):
                return DEFAULT_SETTINGS.copy()

            # Invalid legacy values fall back per field so one damaged setting
            # cannot make every preview fail after startup.
            sanitized = {}
            for key, value in saved.items():
                if key not in DEFAULT_SETTINGS:
                    continue
                try:
                    sanitized.update(validate_settings_updates({key: value}))
                except ValueError:
                    continue

            result = DEFAULT_SETTINGS.copy()
            result.update(sanitized)
            # ── 自动迁移：旧键 → 新键（仅当新键不存在时） ──
            if "dia_tol_pos_upper" not in saved and "dia_tol_upper" in sanitized:
                result["dia_tol_pos_upper"] = sanitized["dia_tol_upper"]
            if "dia_tol_pos_lower" not in saved and "dia_tol_lower" in sanitized:
                result["dia_tol_pos_lower"] = sanitized["dia_tol_lower"]
            if result.get("coat_preset") == "SQ-A3":
                result["coat_preset"] = "SQ-A6"
            return result
        except Exception:
            return DEFAULT_SETTINGS.copy()
    return DEFAULT_SETTINGS.copy()


def save_settings(settings):
    """Save settings to JSON file.
    Applies type coercion to ensure critical keys match DEFAULT_SETTINGS types.
    """
    coerced = settings.copy()
    # 确保 cemented_ref_lens 为整数
    try:
        coerced["cemented_ref_lens"] = int(coerced.get("cemented_ref_lens", DEFAULT_SETTINGS["cemented_ref_lens"]))
    except (ValueError, TypeError):
        coerced["cemented_ref_lens"] = DEFAULT_SETTINGS["cemented_ref_lens"]
    # 确保 proc_N_manual 为字符串
    n_manual = coerced.get("proc_N_manual", DEFAULT_SETTINGS["proc_N_manual"])
    coerced["proc_N_manual"] = str(n_manual)
    if coerced.get("coat_preset") == "SQ-A3":
        coerced["coat_preset"] = "SQ-A6"
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(coerced, f, indent=2, ensure_ascii=False)
