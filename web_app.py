# -*- coding: utf-8 -*-
"""
Lens Drawing Tool - Flask Web Backend
Wraps existing drawing/export logic as REST APIs for PyWebview frontend.
"""
import sys, os, io, base64, json, threading, math, re, tempfile
from datetime import datetime
from urllib.parse import urlsplit

from flask import Flask, render_template, request, jsonify, send_file
from PIL import Image

# Ensure project root is on path for imports
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Matplotlib setup (non-interactive backend)
import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"] = 42
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

# Import existing business logic
from main import (
    draw_lens, _build_single_page_figure, export_pdf,
    _arrow, _sagitta, _calc_et, _ann_ct,
    _ann_et, _ann_diameter, _ann_diameter_left, _ann_sag1, _ann_sag2,
    _ann_ad1, _ann_ad2, _ann_r1, _ann_r2, _ann_chamfer_left,
    _ann_chamfer_right, _ann_spraying, _ann_optical_axis,
    draw_cemented_assembly, _build_assembly_page_figure, export_cemented_pdf,
    build_cemented_preview_figures, get_preview_field_metadata,
    extract_field_positions,
)
from geometry import build_profile
from config import (
    DEFAULTS, validate, validate_cemented_lenses,
    auto_chamfer,
)
from settings import (
    load_settings, save_settings, validate_settings_updates, DEFAULT_SETTINGS,
)
from batch_import import CementedLensData, SingleLensData, export_batch_excel

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB max upload

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _request_origin_matches_host(origin):
    try:
        origin_url = urlsplit(origin)
        request_url = urlsplit(f"{request.scheme}://{request.host}")
        origin_port = origin_url.port or (443 if origin_url.scheme == "https" else 80)
        request_port = request_url.port or (443 if request_url.scheme == "https" else 80)
    except (TypeError, ValueError):
        return False
    return (
        origin_url.scheme == request_url.scheme
        and (origin_url.hostname or "").lower() == (request_url.hostname or "").lower()
        and origin_port == request_port
    )


@app.before_request
def _restrict_to_local_same_origin_requests():
    """Keep the desktop-only HTTP API local and reject browser cross-site writes."""
    try:
        hostname = (urlsplit(f"//{request.host}").hostname or "").lower()
    except ValueError:
        hostname = ""
    if hostname not in _LOOPBACK_HOSTS:
        return jsonify({"success": False, "error": "仅允许本机访问"}), 403

    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
            return jsonify({"success": False, "error": "已拒绝跨站请求"}), 403
        origin = request.headers.get("Origin")
        if origin and not _request_origin_matches_host(origin):
            return jsonify({"success": False, "error": "请求来源无效"}), 403

# In-memory settings (loaded at startup, persisted on change)
_current_settings = load_settings()
_settings_lock = threading.Lock()

_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_INVALID_PATH_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')

# The draw form historically used short keys while persisted settings use
# proc_* keys. Normalize at every backend boundary so preview and export share
# one contract, while old saved sessions remain readable.
_DRAW_OVERRIDE_ALIASES = {
    "proc_b": "proc_surface_defect",
    "N_mode": "proc_N_mode",
    "N_manual": "proc_N_manual",
    "DN": "proc_DN",
    "signature": "proc_signature",
}

_DRAW_OVERRIDE_KEYS = {
    "proc_c_single", "proc_c_assembly", "proc_surface_defect",
    "proc_ranking", "proc_N_mode", "proc_N_manual", "proc_DN",
    "proc_signature", "proc_vendor",
    "chamfer_mode", "chamfer_left", "chamfer_right",
    "CA_mode", "CA1", "CA2", "ca_ratio",
    "t_tol", "sag_tol",
    "dia_tol_pos_upper", "dia_tol_pos_lower",
    "dia_tol_nonpos_upper", "dia_tol_nonpos_lower",
    "cemented_ref_lens", "coat_preset",
    "coat_s1_wave1", "coat_s1_wave2", "coat_s2_wave1", "coat_s2_wave2",
    "coat_s1_ravg1", "coat_s1_ravg2", "coat_s2_ravg1", "coat_s2_ravg2",
    "coat_s1_angle1", "coat_s1_angle2", "coat_s2_angle1", "coat_s2_angle2",
    *(f"ca_mode_{i}" for i in range(1, 4)),
    *(f"ca_{i}_{side}" for i in range(1, 4) for side in ("left", "right")),
    *(f"chamfer_mode_{i}" for i in range(1, 4)),
    *(f"chamfer_{i}_{side}" for i in range(1, 4) for side in ("left", "right")),
}

_NUMERIC_DRAW_OVERRIDE_KEYS = {
    "ca_ratio", "CA1", "CA2", "proc_N_manual",
    "chamfer_left", "chamfer_right",
    "t_tol", "sag_tol",
    "dia_tol_pos_upper", "dia_tol_pos_lower",
    "dia_tol_nonpos_upper", "dia_tol_nonpos_lower",
    *(f"ca_{i}_{side}" for i in range(1, 4) for side in ("left", "right")),
    *(f"chamfer_{i}_{side}" for i in range(1, 4) for side in ("left", "right")),
}


def _normalize_drawing_overrides(overrides, *, keep_unknown=False):
    """Return canonical drawing override keys without mutating the input."""
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        raise ValueError("自定义加工参数必须是对象")

    normalized = dict(overrides) if keep_unknown else {
        key: value for key, value in overrides.items()
        if key in _DRAW_OVERRIDE_KEYS or key in _DRAW_OVERRIDE_ALIASES
    }
    for legacy_key, canonical_key in _DRAW_OVERRIDE_ALIASES.items():
        if canonical_key not in normalized and legacy_key in normalized:
            normalized[canonical_key] = normalized[legacy_key]
        normalized.pop(legacy_key, None)

    for key in _NUMERIC_DRAW_OVERRIDE_KEYS:
        if key not in normalized or normalized[key] in (None, ""):
            continue
        normalized[key] = _coerce_finite_float(normalized[key], key)

    if "cemented_ref_lens" in normalized:
        value = _coerce_finite_float(normalized["cemented_ref_lens"], "胶合定位镜片")
        if not value.is_integer():
            raise ValueError("胶合定位镜片必须是整数")
        normalized["cemented_ref_lens"] = int(value)
    return normalized


def _normalize_page_overrides(page_overrides):
    if page_overrides in (None, {}):
        return {}
    if not isinstance(page_overrides, dict):
        raise ValueError("逐页加工参数 page_overrides 必须是对象")

    normalized = {}
    for page_key, page_values in page_overrides.items():
        if not isinstance(page_values, dict):
            raise ValueError(f"第 {page_key} 页加工参数必须是对象")
        normalized[str(page_key)] = _normalize_drawing_overrides(page_values)
    return normalized


def _validate_path_component(value, label):
    component = str(value).strip()
    if not component:
        raise ValueError(f"{label}不能为空")
    if component in (".", "..") or component.endswith((" ", ".")):
        raise ValueError(f"{label} '{value}' 不是有效的 Windows 名称")
    if _INVALID_PATH_CHARS.search(component):
        raise ValueError(f"{label} '{value}' 包含 Windows 文件名禁用字符")
    if component.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"{label} '{value}' 是 Windows 保留名称")
    return component


def _resolve_output_subdirectory(output_dir, relative_folder, label):
    """Resolve a user-entered relative folder while keeping it under output_dir."""
    raw = str(relative_folder).strip()
    if not raw:
        raise ValueError(f"{label}不能为空")
    if os.path.isabs(raw) or os.path.splitdrive(raw)[0]:
        raise ValueError(f"{label}必须是所选导出目录下的相对路径")

    parts = [part for part in re.split(r"[\\/]", raw) if part]
    if not parts or any(part in (".", "..") for part in parts):
        raise ValueError(f"{label}不能包含 '.' 或 '..' 路径段")
    for part in parts:
        _validate_path_component(part, label)

    root = os.path.abspath(output_dir)
    resolved = os.path.abspath(os.path.join(root, *parts))
    if os.path.commonpath([root, resolved]) != root:
        raise ValueError(f"{label}超出所选导出目录")
    return resolved


def _pdf_filename(value, label):
    stem = _validate_path_component(value, label)
    if "/" in stem or "\\" in stem:
        raise ValueError(f"{label}不能包含路径分隔符")
    return f"{stem}.pdf"


def _xlsx_filename(value):
    filename = _validate_path_component(value, "Excel 文件名")
    if "/" in filename or "\\" in filename:
        raise ValueError("Excel 文件名不能包含路径分隔符")
    if not filename.lower().endswith(".xlsx"):
        raise ValueError("Excel 文件名必须以 .xlsx 结尾")
    return filename


def _merge_settings(updates):
    """Merge updates into current settings and persist."""
    global _current_settings
    with _settings_lock:
        _current_settings.update(updates)
        save_settings(_current_settings)
    return _current_settings


# ══════════════════════════════════════════════════════════════════════
#  Pure business logic (extracted from original Tkinter _do_export)
# ══════════════════════════════════════════════════════════════════════

def batch_export_data_list(data_list, out_dir, settings):
    """
    Batch export PDFs replicating the original _do_export behaviour exactly:
    - Creates two sub-folders per row: Save PDF / Mfr PDF
    - Save PDF: named by PartName, table keeps PartName
    - Mfr PDF:  named by PartNo,  table hides PartName
    - Folder names can be customised via data.save_pdf_folder / data.mfr_pdf_folder
    - All items (single & cemented) go through export_cemented_pdf()
    Returns dict with success_save, success_mfr, errors, total.
    """
    success_save = 0
    success_mfr = 0
    errors = []
    planned_paths = set()

    for d in data_list:
        validation_errors = validate_cemented_lenses(d.lenses)
        if validation_errors:
            item_name = d.part_name or d.part_no or "未命名镜片"
            errors.append(f"[数据校验] {item_name}: {'; '.join(validation_errors)}")
            continue

        try:
            local_settings = settings.copy()
            if getattr(d, "proc_overrides", None):
                local_settings.update(_normalize_drawing_overrides(d.proc_overrides))
            _validate_cemented_drawing_options(local_settings, d.lenses)
            _page_ov = _normalize_page_overrides(
                getattr(d, "page_overrides", None) or {}
            )
        except ValueError as exc:
            item_name = d.part_name or d.part_no or "未命名镜片"
            errors.append(f"[加工参数校验] {item_name}: {exc}")
            continue

        save_folder = d.save_pdf_folder if getattr(d, "save_pdf_folder", "") else "Save PDF"
        mfr_folder = d.mfr_pdf_folder if getattr(d, "mfr_pdf_folder", "") else "Mfr PDF"
        try:
            save_dir = _resolve_output_subdirectory(out_dir, save_folder, "存档 PDF 文件夹")
            mfr_dir = _resolve_output_subdirectory(out_dir, mfr_folder, "编码 PDF 文件夹")
            fname_save = _pdf_filename(d.part_name, "PartName")
            fname_mfr = _pdf_filename(d.part_no, "PartNo")
            fpath_save = os.path.join(save_dir, fname_save)
            fpath_mfr = os.path.join(mfr_dir, fname_mfr)

            item_paths = [os.path.normcase(os.path.abspath(fpath_save)),
                          os.path.normcase(os.path.abspath(fpath_mfr))]
            if len(set(item_paths)) != len(item_paths) or any(
                    path in planned_paths for path in item_paths):
                raise ValueError("本批次存在重复输出路径，请检查 PartName、PartNo 和文件夹")
            planned_paths.update(item_paths)
        except ValueError as exc:
            item_name = d.part_name or d.part_no or "未命名镜片"
            errors.append(f"[输出路径校验] {item_name}: {exc}")
            continue

        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(mfr_dir, exist_ok=True)

        try:
            export_cemented_pdf(d, local_settings, fpath_save, hide_partname=False, page_overrides=_page_ov)
            success_save += 1
        except Exception as e:
            errors.append(f"[{save_folder}] {d.part_name}: {e}")

        try:
            export_cemented_pdf(d, local_settings, fpath_mfr, hide_partname=True, page_overrides=_page_ov)
            success_mfr += 1
        except Exception as e:
            errors.append(f"[{mfr_folder}] {d.part_no}: {e}")

    return {
        "success_save": success_save,
        "success_mfr": success_mfr,
        "errors": errors,
        "total": len(data_list),
    }


def _cemented_data_from_row_dict(row):
    """Build CementedLensData from a frontend row dict (used by batch export)."""
    def present(key):
        value = row.get(key)
        return value is not None and (not isinstance(value, str) or value.strip() != "")

    def required_text(key, label):
        value = str(row.get(key, "")).strip()
        if not value:
            raise ValueError(f"{label}不能为空")
        return value

    def required_number(key, label):
        if not present(key):
            raise ValueError(f"{label}不能为空")
        return _coerce_finite_float(row.get(key), label)

    lenses = [SingleLensData(
        glass=required_text("glass1", "Glass1"),
        T=required_number("T1", "镜片1 T1"),
        R_left=required_number("R1", "镜片1 R1"),
        R_right=required_number("R2", "镜片1 R2"),
        MD=required_number("MD1", "镜片1 MD1"),
        AD_left=required_number("AD1", "镜片1 AD1"),
        AD_right=required_number("AD2", "镜片1 AD2"),
    )]

    has_g2 = any(present(key) for key in ("glass2", "T2", "R3", "MD2", "AD3"))
    has_g3 = any(present(key) for key in ("glass3", "T3", "R4", "MD3", "AD4"))
    if has_g3 and not has_g2:
        raise ValueError("镜片3已有数据，但镜片2为空；胶合镜片必须按顺序填写")

    if has_g2:
        lenses.append(SingleLensData(
            glass=required_text("glass2", "Glass2"),
            T=required_number("T2", "镜片2 T2"),
            R_left=lenses[0].R_right,
            R_right=required_number("R3", "镜片2 R3"),
            MD=required_number("MD2", "镜片2 MD2"),
            AD_left=lenses[0].AD_right,
            AD_right=required_number("AD3", "镜片2 AD3"),
        ))
    if has_g3:
        lenses.append(SingleLensData(
            glass=required_text("glass3", "Glass3"),
            T=required_number("T3", "镜片3 T3"),
            R_left=lenses[1].R_right,
            R_right=required_number("R4", "镜片3 R4"),
            MD=required_number("MD3", "镜片3 MD3"),
            AD_left=lenses[1].AD_right,
            AD_right=required_number("AD4", "镜片3 AD4"),
        ))

    # 提取逐行自定义加工参数（JSON 字符串）
    custom_proc = None
    raw = row.get("custom_proc", "")
    if raw and isinstance(raw, str) and raw.strip():
        try:
            custom_proc = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"自定义加工参数 JSON 无效: {exc}")
        if not isinstance(custom_proc, dict):
            raise ValueError("自定义加工参数必须是 JSON 对象")
    elif isinstance(raw, dict):
        custom_proc = raw.copy()

    page_overrides = {}
    if custom_proc:
        page_overrides = _normalize_page_overrides(custom_proc.pop("page_overrides", {}))
        custom_proc = _normalize_drawing_overrides(custom_proc)

    return CementedLensData(
        part_name=row.get("part_name", ""),
        part_no=row.get("part_no", ""),
        lenses=lenses,
        save_pdf_folder=row.get("save_pdf_folder", "Save PDF") or "Save PDF",
        mfr_pdf_folder=row.get("mfr_pdf_folder", "Mfr PDF") or "Mfr PDF",
        proc_overrides=custom_proc,
        page_overrides=page_overrides,
    )


def _serialized_custom_proc(cemented_data):
    overrides = _normalize_drawing_overrides(
        getattr(cemented_data, "proc_overrides", None) or {}
    )
    page_overrides = _normalize_page_overrides(
        getattr(cemented_data, "page_overrides", None) or {}
    )
    if page_overrides:
        overrides["page_overrides"] = page_overrides
    if not overrides:
        return ""
    return json.dumps(overrides, ensure_ascii=False, separators=(",", ":"))


def _safe_float(data, key, default):
    """Safely convert a request parameter to float with a clear error message."""
    val = data.get(key, default)
    try:
        result = float(val)
    except (ValueError, TypeError):
        raise ValueError(f"参数 {key} 的值 '{val}' 无效，请输入数字")
    if not math.isfinite(result):
        raise ValueError(f"参数 {key} 必须是有限数值")
    return result


def _safe_int(data, key, default):
    value = _safe_float(data, key, default)
    if not value.is_integer():
        raise ValueError(f"参数 {key} 必须是整数")
    return int(value)


def _coerce_finite_float(value, label):
    """Convert an arbitrary JSON value to a finite float with field context."""
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{label} 的值 '{value}' 无效，请输入数字")
    if not math.isfinite(result):
        raise ValueError(f"{label} 必须是有限数值")
    return result


def _params_from_request():
    """Extract lens parameters from JSON request body."""
    data = request.get_json(force=True) or {}
    return {
        "T": _safe_float(data, "T", DEFAULTS["T"]),
        "R1": _safe_float(data, "R1", DEFAULTS["R1"]),
        "R2": _safe_float(data, "R2", DEFAULTS["R2"]),
        "MD": _safe_float(data, "MD", DEFAULTS["MD"]),
        "AD1": _safe_float(data, "AD1", DEFAULTS["AD1"]),
        "AD2": _safe_float(data, "AD2", DEFAULTS["AD2"]),
        "CA1": data.get("CA1"),
        "CA2": data.get("CA2"),
        "CA_mode": data.get("CA_mode", "auto"),  # "auto" or "manual"
        "ca_ratio": _safe_float(data, "ca_ratio", 0.94),
        "part_name": data.get("part_name", "singlelen"),
        "part_no": data.get("part_no", "100.2.00888"),
        "glass_name": data.get("glass_name", "H-K9L"),
        "coat_s1_wave1": data.get("coat_s1_wave1", "420-680"),
        "coat_s1_wave2": data.get("coat_s1_wave2", "680-850"),
        "coat_s2_wave1": data.get("coat_s2_wave1", "420-680"),
        "coat_s2_wave2": data.get("coat_s2_wave2", "680-850"),
        "coat_s1_ravg1": data.get("coat_s1_ravg1", "0.4"),
        "coat_s1_ravg2": data.get("coat_s1_ravg2", "0.8"),
        "coat_s2_ravg1": data.get("coat_s2_ravg1", "0.4"),
        "coat_s2_ravg2": data.get("coat_s2_ravg2", "0.8"),
        "coat_s1_angle1": data.get("coat_s1_angle1", "0-15"),
        "coat_s1_angle2": data.get("coat_s1_angle2", "0-15"),
        "coat_s2_angle1": data.get("coat_s2_angle1", "0-15"),
        "coat_s2_angle2": data.get("coat_s2_angle2", "0-15"),
        "coat_preset": data.get("coat_preset", _current_settings.get("coat_preset", "SQ-A1")),
        "proc_c_single": data.get("proc_c_single", _current_settings.get("proc_c_single", "60\u2033")),
        "proc_c_assembly": data.get("proc_c_assembly", _current_settings.get("proc_c_assembly", "60\u2033")),
        "proc_b": data.get(
            "proc_surface_defect",
            data.get("proc_b", _current_settings.get("proc_surface_defect", "60/40")),
        ),
        "N_mode": data.get(
            "proc_N_mode", data.get("N_mode", _current_settings.get("proc_N_mode", "auto"))
        ),
        "N_manual": data.get(
            "proc_N_manual",
            data.get("N_manual", _current_settings.get("proc_N_manual", "1.5")),
        ),
        "DN": data.get("proc_DN", data.get("DN", _current_settings.get("proc_DN", "0.3"))),
        "signature": data.get(
            "proc_signature",
            data.get("signature", _current_settings.get("proc_signature", "l.y.h")),
        ),
        "proc_vendor": data.get("proc_vendor", _current_settings.get("proc_vendor", "CDGM")),
        "proc_ranking": data.get("proc_ranking", _current_settings.get("proc_ranking", "01")),
        # Chamfer overrides from draw module
        "chamfer_mode": data.get("chamfer_mode", _current_settings.get("chamfer_mode", "auto")),
        "chamfer_left": _safe_float(data, "chamfer_left", _current_settings.get("chamfer_left", 0.2)),
        "chamfer_right": _safe_float(data, "chamfer_right", _current_settings.get("chamfer_right", 0.4)),
        # Tolerance overrides from draw module
        "t_tol": _safe_float(data, "t_tol", _current_settings.get("t_tol", 0.02)),
        "sag_tol": _safe_float(data, "sag_tol", _current_settings.get("sag_tol", 0.02)),
        "dia_tol_pos_upper": _safe_float(data, "dia_tol_pos_upper", _current_settings.get("dia_tol_pos_upper", _current_settings.get("dia_tol_upper", 0.010))),
        "dia_tol_pos_lower": _safe_float(data, "dia_tol_pos_lower", _current_settings.get("dia_tol_pos_lower", _current_settings.get("dia_tol_lower", 0.025))),
        "dia_tol_nonpos_upper": _safe_float(data, "dia_tol_nonpos_upper", _current_settings.get("dia_tol_nonpos_upper", 0.05)),
        "dia_tol_nonpos_lower": _safe_float(data, "dia_tol_nonpos_lower", _current_settings.get("dia_tol_nonpos_lower", 0.10)),
        "cemented_ref_lens": _safe_int(data, "cemented_ref_lens", _current_settings.get("cemented_ref_lens", 2)),
        "filepath": data.get("filepath", ""),
    }


def _validate_single_drawing_options(p, ad_left, ad_right):
    """Validate process controls that can make a geometrically valid drawing misleading."""
    errors = []
    ratio = p["ca_ratio"]
    if not 0 < ratio <= 1:
        errors.append("CA 自动系数必须大于 0 且不大于 1")

    for key, label in (
        ("t_tol", "厚度公差"),
        ("sag_tol", "矢高公差"),
        ("dia_tol_pos_upper", "直径定位上偏差"),
        ("dia_tol_pos_lower", "直径定位下偏差"),
        ("dia_tol_nonpos_upper", "直径非定位上偏差"),
        ("dia_tol_nonpos_lower", "直径非定位下偏差"),
    ):
        if p[key] < 0:
            errors.append(f"{label}不能为负数")

    if p.get("chamfer_mode") not in ("auto", "manual"):
        errors.append("倒角模式无效")
    elif p.get("chamfer_mode") == "manual":
        if p["chamfer_left"] < 0 or p["chamfer_right"] < 0:
            errors.append("手动倒角不能为负数")

    if p.get("CA_mode") not in ("auto", "manual"):
        errors.append("CA 模式无效")
    elif p.get("CA_mode") == "manual":
        for key, label, ad_value in (
            ("CA1", "CA1", ad_left),
            ("CA2", "CA2", ad_right),
        ):
            raw = p.get(key)
            if raw in (None, ""):
                errors.append(f"手动模式下必须填写 {label}")
                continue
            try:
                value = _coerce_finite_float(raw, label)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if value <= 0:
                errors.append(f"{label} 必须大于 0")
            elif value > ad_value:
                errors.append(f"{label} 不能大于对应 AD ({ad_value:g} mm)")

    if p.get("N_mode") not in ("auto", "manual"):
        errors.append("N 模式无效")
    elif p.get("N_mode") == "manual":
        try:
            n_value = _coerce_finite_float(p.get("N_manual"), "N")
            if n_value <= 0:
                errors.append("N 必须大于 0")
        except ValueError as exc:
            errors.append(str(exc))

    return errors


def _build_proc_params(p):
    """Build proc_params dict for export functions."""
    return {
        "part_name": p["part_name"],
        "part_no": p["part_no"],
        "glass_name": p["glass_name"],
        "coat_s1_wave1": p["coat_s1_wave1"],
        "coat_s1_wave2": p["coat_s1_wave2"],
        "coat_s2_wave1": p["coat_s2_wave1"],
        "coat_s2_wave2": p["coat_s2_wave2"],
        "coat_s1_ravg1": p["coat_s1_ravg1"],
        "coat_s1_ravg2": p["coat_s1_ravg2"],
        "coat_s2_ravg1": p["coat_s2_ravg1"],
        "coat_s2_ravg2": p["coat_s2_ravg2"],
        "coat_s1_angle1": p["coat_s1_angle1"],
        "coat_s1_angle2": p["coat_s1_angle2"],
        "coat_s2_angle1": p["coat_s2_angle1"],
        "coat_s2_angle2": p["coat_s2_angle2"],
        "coat_preset": p.get("coat_preset", "SQ-A1"),
        "proc_c_single": p["proc_c_single"],
        "proc_c_assembly": p["proc_c_assembly"],
        "proc_surface_defect": p["proc_b"],
        "proc_N_mode": p["N_mode"],
        "proc_N_manual": p["N_manual"],
        "proc_DN": p["DN"],
        "proc_signature": p["signature"],
        "proc_vendor": p["proc_vendor"],
        "proc_ranking": p.get("proc_ranking", "01"),
        # Chamfer
        "chamfer_mode": p.get("chamfer_mode", "auto"),
        "chamfer_left": p.get("chamfer_left", 0.2),
        "chamfer_right": p.get("chamfer_right", 0.4),
        # Tolerances
        "t_tol": p.get("t_tol", 0.02),
        "sag_tol": p.get("sag_tol", 0.02),
        "dia_tol_pos_upper": p.get("dia_tol_pos_upper", 0.010),
        "dia_tol_pos_lower": p.get("dia_tol_pos_lower", 0.025),
        "dia_tol_nonpos_upper": p.get("dia_tol_nonpos_upper", 0.05),
        "dia_tol_nonpos_lower": p.get("dia_tol_nonpos_lower", 0.10),
        "cemented_ref_lens": p.get("cemented_ref_lens", 2),
    }


# ══════════════════════════════════════════════════════════════════════
#  Preview helpers (fixed-DPI rendering for overlay positioning)
# ══════════════════════════════════════════════════════════════════════

PREVIEW_DPI = 100  # 固定 DPI，保证像素→mm 可预测
IMG_W = int(11.69 * PREVIEW_DPI)   # ≈ 1169 px
IMG_H = int(8.27 * PREVIEW_DPI)    # ≈ 827 px

def _fig_to_preview_response(fig, is_cemented_single=False, field_values=None):
    """将 Figure 渲染为 PNG + 用 BBox 提取的精确字段坐标，返回 API 响应 dict"""
    if field_values is None:
        field_values = {}

    # 1. 提取精确 BBox 位置（在 PREVIEW_DPI 下渲染）
    actual_positions = extract_field_positions(fig, PREVIEW_DPI)

    # 2. 渲染 PNG（DPI 已恢复为原值，savefig 会再次渲染）
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=PREVIEW_DPI, pad_inches=0)
    buf.seek(0)
    img_b64 = base64.b64encode(buf.read()).decode("utf-8")

    # 3. 读取实际 PNG 尺寸
    buf.seek(0)
    pil_img = Image.open(buf)
    img_w, img_h = pil_img.size

    # 4. 合并：用实际 BBox 位置 + field_values
    fields_raw = get_preview_field_metadata(is_cemented_single=is_cemented_single)
    fields = []
    for f in fields_raw:
        if f["id"] in actual_positions:
            pos = actual_positions[f["id"]]
            fields.append({
                "id": f["id"],
                "label": f["label"],
                "left_pct": pos["left_pct"],
                "top_pct": pos["top_pct"],
                "w_pct": pos["w_pct"],
                "h_pct": pos["h_pct"],
                "source": f["source"],
                "value": field_values.get(f["id"], ""),
            })
        else:
            # fallback: 硬编码坐标
            fields.append({
                "id": f["id"],
                "label": f["label"],
                "left_pct": round(f["x_mm"] / 297.0 * 100, 2),
                "top_pct": round((210.0 - f["y_mm"]) / 210.0 * 100, 2),
                "w_pct": round(f["w_mm"] / 297.0 * 100, 2),
                "h_pct": round(f["h_mm"] / 210.0 * 100, 2),
                "source": f["source"],
                "value": field_values.get(f["id"], ""),
            })
    return {"image": img_b64, "fields": fields, "img_w": img_w, "img_h": img_h}


def _extract_tagged_field_values(fig):
    """Read editable values from the rendered Figure itself."""
    values = {}
    for axis in fig.axes:
        for text_obj in axis.texts:
            field_id = getattr(text_obj, "_field_id", None)
            if field_id:
                values[field_id] = text_obj.get_text()
    return values

# ══════════════════════════════════════════════════════════════════════
#  Page Routes
# ══════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("launcher.html")


@app.route("/draw")
def draw_page():
    return render_template("draw.html")


@app.route("/batch")
def batch_page():
    return render_template("batch.html")


@app.route("/settings")
def settings_page():
    return render_template("settings.html")


# ══════════════════════════════════════════════════════════════════════
#  API Routes
# ══════════════════════════════════════════════════════════════════════

@app.route("/api/defaults", methods=["GET"])
def api_defaults():
    """Return backend default parameter values for frontend reset."""
    from config import DEFAULTS
    return jsonify({
        "T": DEFAULTS["T"],
        "R1": DEFAULTS["R1"],
        "R2": DEFAULTS["R2"],
        "MD": DEFAULTS["MD"],
        "AD1": DEFAULTS["AD1"],
        "AD2": DEFAULTS["AD2"],
    })


@app.route("/api/preview", methods=["POST"])
def api_preview():
    """Generate a preview PNG (full single-lens PDF page) and return as base64."""
    try:
        p = _params_from_request()
        T, R1, R2, MD, AD1, AD2 = p["T"], p["R1"], p["R2"], p["MD"], p["AD1"], p["AD2"]

        errors = validate(T, R1, R2, MD, AD1, AD2)
        errors.extend(_validate_single_drawing_options(p, AD1, AD2))
        if errors:
            return jsonify({"success": False, "error": "; ".join(errors)})

        cL, cR = auto_chamfer(MD, R1, R2) if p.get("chamfer_mode", _current_settings.get("chamfer_mode", "auto")) == "auto" else (
            p.get("chamfer_left", _current_settings.get("chamfer_left", 0.2)),
            p.get("chamfer_right", _current_settings.get("chamfer_right", 0.4)),
        )

        # CA: auto mode → None (auto_CA uses ca_ratio from settings), manual → float value
        ca_ratio = p.get("ca_ratio", _current_settings.get("ca_ratio", 0.94))
        if p.get("CA_mode") == "manual":
            ca1 = float(p["CA1"]) if p.get("CA1") not in (None, "") else None
            ca2 = float(p["CA2"]) if p.get("CA2") not in (None, "") else None
        else:
            ca1 = None  # auto_CA will be called in _build_single_page_figure
            ca2 = None
        proc_params = _build_proc_params(p)

        # Read tolerance from request params (fallback to settings)
        _t_tol = p.get("t_tol", _current_settings.get("t_tol", 0.02))
        _sag_tol = p.get("sag_tol", _current_settings.get("sag_tol", 0.02))
        _dia_pos_upper = p.get("dia_tol_pos_upper", _current_settings.get("dia_tol_pos_upper", _current_settings.get("dia_tol_upper", 0.010)))
        _dia_pos_lower = p.get("dia_tol_pos_lower", _current_settings.get("dia_tol_pos_lower", _current_settings.get("dia_tol_lower", 0.025)))

        # Inject form ca_ratio into settings copy so figure builder uses current value
        _preview_settings = _current_settings.copy()
        _preview_settings["ca_ratio"] = ca_ratio

        fig = _build_single_page_figure(
            T, R1, R2, MD, AD1, AD2,
            _current_settings["J_multiplier"],
            _current_settings["ct_offset_J"],
            _current_settings["et_offset_J"],
            _current_settings["sag_offset_J"],
            _current_settings["dia_offset_J"],
            _current_settings["ad_offset_J"],
            _current_settings["spray_gap_J"],
            cL, cR,
            _t_tol,
            _sag_tol,
            _current_settings["font_size"],
            _current_settings["arrow_scale"],
            _current_settings["r_offset_J"],
            _dia_pos_upper,
            _dia_pos_lower,
            proc_params=proc_params,
            settings=_preview_settings,
            ca1=ca1, ca2=ca2,
        )

        try:
            resp = _fig_to_preview_response(
                fig,
                is_cemented_single=False,
                field_values=_extract_tagged_field_values(fig),
            )
        finally:
            plt.close(fig)
        return jsonify({"success": True, **resp})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/export", methods=["POST"])
def api_export():
    """Export PDF to the given path."""
    try:
        p = _params_from_request()
        filepath = p.get("filepath", "")
        if not filepath:
            return jsonify({"success": False, "error": "No filepath provided"})
        # Path safety: reject path traversal attempts
        if ".." in filepath.replace("\\", "/").split("/"):
            return jsonify({"success": False, "error": "Invalid filepath: path traversal not allowed"})

        T, R1, R2, MD, AD1, AD2 = p["T"], p["R1"], p["R2"], p["MD"], p["AD1"], p["AD2"]
        errors = validate(T, R1, R2, MD, AD1, AD2)
        errors.extend(_validate_single_drawing_options(p, AD1, AD2))
        if errors:
            return jsonify({"success": False, "error": "; ".join(errors)})

        cL, cR = auto_chamfer(MD, R1, R2) if p.get("chamfer_mode", _current_settings.get("chamfer_mode", "auto")) == "auto" else (
            p.get("chamfer_left", _current_settings.get("chamfer_left", 0.2)),
            p.get("chamfer_right", _current_settings.get("chamfer_right", 0.4)),
        )

        # CA: auto mode → None (auto_CA uses ca_ratio from settings), manual → float value
        ca_ratio = p.get("ca_ratio", _current_settings.get("ca_ratio", 0.94))
        if p.get("CA_mode") == "manual":
            ca1 = float(p["CA1"]) if p.get("CA1") not in (None, "") else None
            ca2 = float(p["CA2"]) if p.get("CA2") not in (None, "") else None
        else:
            ca1 = None  # auto_CA will be called in _build_single_page_figure
            ca2 = None
        proc_params = _build_proc_params(p)

        _t_tol = p.get("t_tol", _current_settings.get("t_tol", 0.02))
        _sag_tol = p.get("sag_tol", _current_settings.get("sag_tol", 0.02))
        _dia_pos_upper = p.get("dia_tol_pos_upper", _current_settings.get("dia_tol_pos_upper", _current_settings.get("dia_tol_upper", 0.010)))
        _dia_pos_lower = p.get("dia_tol_pos_lower", _current_settings.get("dia_tol_pos_lower", _current_settings.get("dia_tol_lower", 0.025)))

        # Inject form ca_ratio into settings copy so figure builder uses current value
        _export_settings = _current_settings.copy()
        _export_settings["ca_ratio"] = ca_ratio

        export_pdf(
            T, R1, R2, MD, AD1, AD2,
            _current_settings["J_multiplier"],
            _current_settings["ct_offset_J"],
            _current_settings["et_offset_J"],
            _current_settings["sag_offset_J"],
            _current_settings["dia_offset_J"],
            _current_settings["ad_offset_J"],
            _current_settings["spray_gap_J"],
            cL, cR,
            _t_tol,
            _sag_tol,
            _current_settings["font_size"],
            _current_settings["arrow_scale"],
            _current_settings["r_offset_J"],
            filepath,
            _dia_pos_upper,
            _dia_pos_lower,
            proc_params, _export_settings, ca1=ca1, ca2=ca2,
        )

        return jsonify({"success": True, "path": filepath})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


def _cemented_data_from_request(data):
    """Build CementedLensData from JSON request."""
    raw_lenses = data.get("lenses", [])
    if not isinstance(raw_lenses, list):
        raise ValueError("lenses 必须是镜片数组")
    if not 1 <= len(raw_lenses) <= 3:
        raise ValueError("镜片数量必须为 1~3 片")

    lenses = []
    for index, item in enumerate(raw_lenses, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"镜片{index}参数格式无效")
        lenses.append(SingleLensData(
            glass=item.get("glass", ""),
            T=_coerce_finite_float(item.get("T", 0), f"镜片{index} T"),
            R_left=_coerce_finite_float(item.get("R_left", 0), f"镜片{index} 左曲率"),
            R_right=_coerce_finite_float(item.get("R_right", 0), f"镜片{index} 右曲率"),
            MD=_coerce_finite_float(item.get("MD", 0), f"镜片{index} MD"),
            AD_left=_coerce_finite_float(item.get("AD_left", 0), f"镜片{index} 左 AD"),
            AD_right=_coerce_finite_float(item.get("AD_right", 0), f"镜片{index} 右 AD"),
        ))

    errors = validate_cemented_lenses(lenses)
    if errors:
        raise ValueError("; ".join(errors))

    return CementedLensData(
        part_name=data.get("part_name", "cemented"),
        part_no=data.get("part_no", ""),
        lenses=lenses,
    )


def _cemented_augmented_settings(data):
    """Create a local settings copy with proc overrides from cemented request.
    Does NOT modify global _current_settings — overrides are session-local.
    Accepts both canonical proc_* keys and legacy draw-form aliases.
    """
    settings = _current_settings.copy()
    settings.update(_normalize_drawing_overrides(data))
    return settings


def _validate_cemented_drawing_options(settings, lenses):
    """Validate and normalize custom drawing controls for cemented lenses."""
    errors = []

    def finite_setting(key, label, default=None):
        raw = settings.get(key, default)
        try:
            value = _coerce_finite_float(raw, label)
        except ValueError as exc:
            errors.append(str(exc))
            return None
        settings[key] = value
        return value

    ratio = finite_setting("ca_ratio", "CA 自动系数", 0.94)
    if ratio is not None and not 0 < ratio <= 1:
        errors.append("CA 自动系数必须大于 0 且不大于 1")

    for key, label, default in (
        ("t_tol", "厚度公差", 0.02),
        ("sag_tol", "矢高公差", 0.02),
        ("dia_tol_pos_upper", "直径定位上偏差", 0.010),
        ("dia_tol_pos_lower", "直径定位下偏差", 0.025),
        ("dia_tol_nonpos_upper", "直径非定位上偏差", 0.05),
        ("dia_tol_nonpos_lower", "直径非定位下偏差", 0.10),
    ):
        value = finite_setting(key, label, default)
        if value is not None and value < 0:
            errors.append(f"{label}不能为负数")

    if len(lenses) == 1:
        settings["cemented_ref_lens"] = 1
    else:
        ref_raw = settings.get("cemented_ref_lens", 2)
        try:
            ref_value = _coerce_finite_float(ref_raw, "胶合定位镜片")
            if not ref_value.is_integer():
                errors.append("胶合定位镜片必须是整数")
            elif not 1 <= int(ref_value) <= len(lenses):
                errors.append(f"胶合定位镜片必须在 1~{len(lenses)} 之间")
            else:
                settings["cemented_ref_lens"] = int(ref_value)
        except ValueError as exc:
            errors.append(str(exc))

    n_mode = settings.get("proc_N_mode", "auto")
    if n_mode not in ("auto", "manual"):
        errors.append("N 模式无效")
    elif n_mode == "manual":
        n_value = finite_setting("proc_N_manual", "N")
        if n_value is not None and n_value <= 0:
            errors.append("N 必须大于 0")

    global_chamfer_mode = settings.get("chamfer_mode", "auto")
    if global_chamfer_mode not in ("auto", "manual"):
        errors.append("倒角模式无效")
    elif global_chamfer_mode == "manual":
        for key, label in (("chamfer_left", "左侧倒角"),
                           ("chamfer_right", "右侧倒角")):
            value = finite_setting(key, label)
            if value is not None and value < 0:
                errors.append(f"{label}不能为负数")

    for index, lens in enumerate(lenses, start=1):
        ca_mode = settings.get(f"ca_mode_{index}", "auto")
        if ca_mode not in ("auto", "manual"):
            errors.append(f"镜片{index} CA 模式无效")
        elif ca_mode == "manual":
            for side, ad_value, side_label in (
                ("left", lens.AD_left, "左"),
                ("right", lens.AD_right, "右"),
            ):
                key = f"ca_{index}_{side}"
                raw = settings.get(key)
                if raw in (None, ""):
                    errors.append(f"手动模式下必须填写镜片{index} {side_label} CA")
                    continue
                value = finite_setting(key, f"镜片{index} {side_label} CA")
                if value is None:
                    continue
                if value <= 0:
                    errors.append(f"镜片{index} {side_label} CA 必须大于 0")
                elif value > ad_value:
                    errors.append(
                        f"镜片{index} {side_label} CA 不能大于对应 AD ({ad_value:g} mm)"
                    )

        chamfer_mode = settings.get(f"chamfer_mode_{index}", "auto")
        if chamfer_mode not in ("auto", "manual"):
            errors.append(f"镜片{index}倒角模式无效")
        elif chamfer_mode == "manual":
            for side, side_label in (("left", "左"), ("right", "右")):
                key = f"chamfer_{index}_{side}"
                raw = settings.get(key)
                if raw in (None, ""):
                    errors.append(f"手动模式下必须填写镜片{index}{side_label}侧倒角")
                    continue
                value = finite_setting(key, f"镜片{index}{side_label}侧倒角")
                if value is not None and value < 0:
                    errors.append(f"镜片{index}{side_label}侧倒角不能为负数")

    if errors:
        raise ValueError("; ".join(errors))


@app.route("/api/preview/cemented", methods=["POST"])
def api_preview_cemented():
    """Generate all cemented lens preview pages (assembly + individual) as base64."""
    try:
        data = request.get_json(force=True) or {}
        cemented_data = _cemented_data_from_request(data)

        if len(cemented_data.lenses) < 2:
            return jsonify({"success": False, "error": "胶合镜片至少需要2片"})

        # Use augmented settings with draw-module proc overrides (does not mutate global)
        local_settings = _cemented_augmented_settings(data)
        _validate_cemented_drawing_options(local_settings, cemented_data.lenses)

        # 提取逐页覆盖参数
        page_overrides = _normalize_page_overrides(
            data.get("page_overrides", None) or {}
        )

        # Build all figures: [(label, fig), ...]
        figures = build_cemented_preview_figures(cemented_data, local_settings, page_overrides=page_overrides)

        images = []
        labels = []
        image_sizes = []   # 每页 PNG 实际像素尺寸 [{w, h}, ...]
        fields_by_page = []  # 每页的字段数组（与 images 平行）
        for label, fig in figures:
            try:
                buf = io.BytesIO()
                fig.savefig(buf, format="png", dpi=PREVIEW_DPI, pad_inches=0)
                buf.seek(0)
                img_b64 = base64.b64encode(buf.read()).decode("utf-8")
                images.append(img_b64)

                # 读取实际 PNG 尺寸用于前端精确校正
                buf.seek(0)
                pil_img = Image.open(buf)
                image_sizes.append({"w": pil_img.size[0], "h": pil_img.size[1]})

                labels.append(label)
                # 仅单片页附带字段坐标（组装页为空数组）
                is_single = label.startswith("镜片")
                if is_single:
                    field_values = _extract_tagged_field_values(fig)
                    # 用 BBox 提取精确位置
                    actual_positions = extract_field_positions(fig, PREVIEW_DPI)
                    raw = get_preview_field_metadata(is_cemented_single=True)
                    page_fields = []
                    for f in raw:
                        if f["id"] in actual_positions:
                            pos = actual_positions[f["id"]]
                            page_fields.append({
                                "id": f["id"], "label": f["label"],
                                "left_pct": pos["left_pct"],
                                "top_pct": pos["top_pct"],
                                "w_pct": pos["w_pct"],
                                "h_pct": pos["h_pct"],
                                "source": f["source"],
                                "value": field_values.get(f["id"], ""),
                            })
                        else:
                            page_fields.append({
                                "id": f["id"], "label": f["label"],
                                "left_pct": round(f["x_mm"] / 297.0 * 100, 2),
                                "top_pct": round((210.0 - f["y_mm"]) / 210.0 * 100, 2),
                                "w_pct": round(f["w_mm"] / 297.0 * 100, 2),
                                "h_pct": round(f["h_mm"] / 210.0 * 100, 2),
                                "source": f["source"],
                                "value": field_values.get(f["id"], ""),
                            })
                    fields_by_page.append(page_fields)
                else:
                    fields_by_page.append([])

            finally:
                plt.close(fig)

        return jsonify({"success": True, "images": images, "labels": labels,
                        "fields_by_page": fields_by_page, "image_sizes": image_sizes})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/export/cemented", methods=["POST"])
def api_export_cemented():
    """Export cemented lens PDF to the given path."""
    try:
        data = request.get_json(force=True) or {}
        filepath = data.get("filepath", "")
        if not filepath:
            return jsonify({"success": False, "error": "No filepath provided"})
        # Path safety: reject path traversal attempts
        if ".." in filepath.replace("\\", "/").split("/"):
            return jsonify({"success": False, "error": "Invalid filepath: path traversal not allowed"})

        cemented_data = _cemented_data_from_request(data)
        if len(cemented_data.lenses) < 2:
            return jsonify({"success": False, "error": "胶合镜片至少需要2片"})

        # Use augmented settings with draw-module proc overrides (does not mutate global)
        local_settings = _cemented_augmented_settings(data)
        _validate_cemented_drawing_options(local_settings, cemented_data.lenses)
        page_overrides = _normalize_page_overrides(
            data.get("page_overrides", None) or {}
        )
        export_cemented_pdf(cemented_data, local_settings, filepath, page_overrides=page_overrides)
        return jsonify({"success": True, "path": filepath})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    return jsonify(_current_settings)


@app.route("/api/settings", methods=["POST"])
def api_settings_save():
    try:
        updates = request.get_json(force=True) or {}
        normalized = validate_settings_updates(updates)
        _merge_settings(normalized)
        return jsonify({"success": True, "settings": _current_settings})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/batch/parse", methods=["POST"])
def api_batch_parse():
    """Parse uploaded Excel/CSV file and return lens data list."""
    try:
        from batch_import import read_excel, read_csv_file

        if "file" not in request.files:
            return jsonify({"success": False, "error": "No file uploaded"})

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"success": False, "error": "Empty filename"})

        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in (".csv", ".xls", ".xlsx"):
            return jsonify({"success": False, "error": "仅支持 .csv、.xls、.xlsx 文件"})

        fd, tmp_path = tempfile.mkstemp(prefix="lensdrawing_batch_", suffix=ext)
        os.close(fd)
        try:
            file.save(tmp_path)
            if ext == ".csv":
                rows, warnings = read_csv_file(tmp_path)
            else:
                rows, warnings = read_excel(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        items = []
        for row in rows:
            lenses = row.lenses
            items.append({
                "part_name": row.part_name,
                "part_no": row.part_no,
                "glass1": lenses[0].glass if len(lenses) > 0 else "",
                "glass2": lenses[1].glass if len(lenses) > 1 else "",
                "glass3": lenses[2].glass if len(lenses) > 2 else "",
                "T1": lenses[0].T,
                "T2": lenses[1].T if len(lenses) > 1 else "",
                "T3": lenses[2].T if len(lenses) > 2 else "",
                "R1": lenses[0].R_left,
                "R2": lenses[0].R_right,
                "R3": lenses[1].R_right if len(lenses) > 1 else "",
                "R4": lenses[2].R_right if len(lenses) > 2 else "",
                "MD1": lenses[0].MD,
                "MD2": lenses[1].MD if len(lenses) > 1 else "",
                "MD3": lenses[2].MD if len(lenses) > 2 else "",
                "AD1": lenses[0].AD_left,
                "AD2": lenses[0].AD_right,
                "AD3": lenses[1].AD_right if len(lenses) > 1 else "",
                "AD4": lenses[2].AD_right if len(lenses) > 2 else "",
                "lens_type": row.lens_type,
                "save_pdf_folder": row.save_pdf_folder,
                "mfr_pdf_folder": row.mfr_pdf_folder,
                "custom_proc": _serialized_custom_proc(row),
            })

        resp = {"success": True, "data": items, "count": len(items)}
        if warnings:
            resp["warnings"] = warnings
        return jsonify(resp)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/validate", methods=["POST"])
def api_validate():
    """Validate parameters and return errors (if any)."""
    try:
        p = _params_from_request()
        T, R1, R2, MD, AD1, AD2 = p["T"], p["R1"], p["R2"], p["MD"], p["AD1"], p["AD2"]
        errors = validate(T, R1, R2, MD, AD1, AD2)
        errors.extend(_validate_single_drawing_options(p, AD1, AD2))
        return jsonify({"valid": len(errors) == 0, "errors": errors})
    except Exception as e:
        return jsonify({"valid": False, "errors": [str(e)]})


@app.route("/api/batch/export", methods=["POST"])
def api_batch_export():
    """Batch export PDFs from editor rows (single / doublet / triplet).
    Replicates original _do_export: dual folders, hide_partname, all via export_cemented_pdf.
    """
    try:
        data = request.get_json(force=True) or {}
        rows = data.get("rows", [])
        output_dir = data.get("output_dir", "")
        if not output_dir:
            return jsonify({"success": False, "error": "No output directory specified"})
        if not rows:
            return jsonify({"success": False, "error": "No rows to export"})

        # Invalid rows are reported individually; valid rows still export.
        data_list = []
        row_errors = []
        for index, row in enumerate(rows, start=1):
            try:
                data_list.append(_cemented_data_from_row_dict(row))
            except (TypeError, ValueError) as exc:
                row_errors.append(f"[第 {index} 行数据] {exc}")
        result = batch_export_data_list(data_list, output_dir, _current_settings)
        errors = row_errors + result["errors"]

        return jsonify({
            "success": True,
            "total": len(rows),
            "success_save": result["success_save"],
            "success_mfr": result["success_mfr"],
            "failed_count": len(rows) - min(result["success_save"], result["success_mfr"]),
            "errors": errors,
            "output_dir": output_dir,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/batch/export-excel", methods=["POST"])
def api_batch_export_excel():
    """Export current table data as a re-importable Excel file."""
    try:
        data = request.get_json(force=True) or {}
        rows = data.get("rows", [])
        output_dir = data.get("output_dir", "")
        filename = data.get("filename", "batch_export.xlsx")
        if not output_dir:
            return jsonify({"success": False, "error": "No output directory specified"})
        if not rows:
            return jsonify({"success": False, "error": "No rows to export"})

        filepath = os.path.join(os.path.abspath(output_dir), _xlsx_filename(filename))
        export_batch_excel(filepath, rows)
        return jsonify({"success": True, "path": filepath})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/save-text-file", methods=["POST"])
def api_save_text_file():
    """Save text content to the given file path."""
    try:
        data = request.get_json(force=True) or {}
        filepath = data.get("path", "")
        content = data.get("content", "")
        if not filepath:
            return jsonify({"success": False, "error": "No path provided"})
        if not str(filepath).lower().endswith(".csv"):
            return jsonify({"success": False, "error": "模板文件必须使用 .csv 扩展名"})
        if not isinstance(content, str) or len(content.encode("utf-8")) > 10 * 1024 * 1024:
            return jsonify({"success": False, "error": "模板内容无效或超过 10 MB"})
        with open(filepath, "w", encoding="utf-8-sig") as f:
            f.write(content)
        return jsonify({"success": True, "path": filepath})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ══════════════════════════════════════════════════════════════════════
#  Entry Point
# ══════════════════════════════════════════════════════════════════════

def get_free_port(host="127.0.0.1"):
    """Get a free port by binding to port 0 and keeping the socket open.
    Returns (port, sock) — caller must close sock when done.
    """
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, 0))
    port = sock.getsockname()[1]
    return port, sock


def run_server_in_thread(host="127.0.0.1", port=0):
    """Start Flask server in a background thread. Returns actual port."""
    import threading
    sock = None
    if port == 0:
        port, sock = get_free_port(host)
    def _run():
        try:
            if sock:
                sock.close()  # Release port just before Flask binds
            app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)
        except Exception:
            pass
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return port


if __name__ == "__main__":
    run_server_in_thread(port=5000)
