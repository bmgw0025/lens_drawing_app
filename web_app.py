# -*- coding: utf-8 -*-
"""
Lens Drawing Tool - Flask Web Backend
Wraps existing drawing/export logic as REST APIs for PyWebview frontend.
"""
import sys, os, io, base64, traceback, json
from datetime import datetime

from flask import Flask, render_template, request, jsonify, send_file

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
    _arrow, _sagitta, _calc_et, _ann_ct, _ann_total_length,
    _ann_et, _ann_diameter, _ann_diameter_left, _ann_sag1, _ann_sag2,
    _ann_ad1, _ann_ad2, _ann_r1, _ann_r2, _ann_chamfer_left,
    _ann_chamfer_right, _ann_spraying, _ann_optical_axis,
    draw_cemented_assembly, _build_assembly_page_figure, export_cemented_pdf,
    build_cemented_preview_figures, get_preview_field_metadata,
    extract_field_positions,
)
from geometry import build_profile
from config import DEFAULTS, validate, auto_chamfer, auto_CA, auto_N, auto_chamfer_by_dia
from settings import load_settings, save_settings, DEFAULT_SETTINGS
from batch_import import CementedLensData, SingleLensData, export_batch_excel

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB max upload

# In-memory settings (loaded at startup, persisted on change)
_current_settings = load_settings()


def _merge_settings(updates):
    """Merge updates into current settings and persist."""
    global _current_settings
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

    for d in data_list:
        save_folder = d.save_pdf_folder if getattr(d, "save_pdf_folder", "") else "Save PDF"
        mfr_folder = d.mfr_pdf_folder if getattr(d, "mfr_pdf_folder", "") else "Mfr PDF"
        save_dir = os.path.join(out_dir, save_folder)
        mfr_dir = os.path.join(out_dir, mfr_folder)
        os.makedirs(save_dir, exist_ok=True)
        os.makedirs(mfr_dir, exist_ok=True)

        fname_save = f"{d.part_name}.pdf" if d.part_name else "untitled.pdf"
        fpath_save = os.path.join(save_dir, fname_save)
        fname_mfr = f"{d.part_no}.pdf" if d.part_no else "untitled.pdf"
        fpath_mfr = os.path.join(mfr_dir, fname_mfr)

        try:
            # 逐行加工参数覆盖：创建本行的 local_settings
            local_settings = settings.copy()
            if getattr(d, "proc_overrides", None):
                local_settings.update(d.proc_overrides)
            export_cemented_pdf(d, local_settings, fpath_save, hide_partname=False)
            success_save += 1
        except Exception as e:
            errors.append(f"[{save_folder}] {d.part_name}: {e}")

        try:
            local_settings = settings.copy()
            if getattr(d, "proc_overrides", None):
                local_settings.update(d.proc_overrides)
            export_cemented_pdf(d, local_settings, fpath_mfr, hide_partname=True)
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
    lenses = []
    lenses.append(SingleLensData(
        glass=row.get("glass1", ""),
        T=float(row.get("T1", 0) or 0),
        R_left=float(row.get("R1", 0) or 0),
        R_right=float(row.get("R2", 0) or 0),
        MD=float(row.get("MD1", 0) or 0),
        AD_left=float(row.get("AD1", 0) or 0),
        AD_right=float(row.get("AD2", 0) or 0),
    ))

    has_g2 = row.get("glass2") and row.get("T2")
    has_g3 = row.get("glass3") and row.get("T3")

    if has_g2:
        lenses.append(SingleLensData(
            glass=row.get("glass2", ""),
            T=float(row.get("T2", 0) or 0),
            R_left=float(row.get("R2", 0) or 0),
            R_right=float(row.get("R3", 0) or 0),
            MD=float(row.get("MD2", 0) or 0),
            AD_left=float(row.get("AD2", 0) or 0),
            AD_right=float(row.get("AD3", 0) or 0),
        ))
    if has_g3:
        lenses.append(SingleLensData(
            glass=row.get("glass3", ""),
            T=float(row.get("T3", 0) or 0),
            R_left=float(row.get("R3", 0) or 0),
            R_right=float(row.get("R4", 0) or 0),
            MD=float(row.get("MD3", 0) or 0),
            AD_left=float(row.get("AD3", 0) or 0),
            AD_right=float(row.get("AD4", 0) or 0),
        ))

    # 提取逐行自定义加工参数（JSON 字符串）
    custom_proc = None
    raw = row.get("custom_proc", "")
    if raw and isinstance(raw, str) and raw.strip():
        try:
            custom_proc = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            custom_proc = None
    elif isinstance(raw, dict):
        custom_proc = raw

    return CementedLensData(
        part_name=row.get("part_name", ""),
        part_no=row.get("part_no", ""),
        lenses=lenses,
        save_pdf_folder=row.get("save_pdf_folder", "Save PDF") or "Save PDF",
        mfr_pdf_folder=row.get("mfr_pdf_folder", "Mfr PDF") or "Mfr PDF",
        proc_overrides=custom_proc,
    )


def _params_from_request():
    """Extract lens parameters from JSON request body."""
    data = request.get_json(force=True) or {}
    return {
        "T": float(data.get("T", DEFAULTS["T"])),
        "R1": float(data.get("R1", DEFAULTS["R1"])),
        "R2": float(data.get("R2", DEFAULTS["R2"])),
        "MD": float(data.get("MD", DEFAULTS["MD"])),
        "AD1": float(data.get("AD1", DEFAULTS["AD1"])),
        "AD2": float(data.get("AD2", DEFAULTS["AD2"])),
        "CA1": data.get("CA1"),
        "CA2": data.get("CA2"),
        "CA_mode": data.get("CA_mode", "auto"),  # "auto" or "manual"
        "ca_ratio": data.get("ca_ratio", 0.98),
        "part_name": data.get("part_name", "singlelen"),
        "part_no": data.get("part_no", "100.2.00888"),
        "glass_name": data.get("glass_name", "H-K9L"),
        "coat_s1_wave1": data.get("coat_s1_wave1", "420-680"),
        "coat_s1_wave2": data.get("coat_s1_wave2", "850/940"),
        "coat_s2_wave1": data.get("coat_s2_wave1", "420-680"),
        "coat_s2_wave2": data.get("coat_s2_wave2", "850/940"),
        "coat_s1_ravg1": data.get("coat_s1_ravg1", "0.5"),
        "coat_s1_ravg2": data.get("coat_s1_ravg2", "1"),
        "coat_s2_ravg1": data.get("coat_s2_ravg1", "0.5"),
        "coat_s2_ravg2": data.get("coat_s2_ravg2", "1"),
        "coat_s1_angle1": data.get("coat_s1_angle1", "0-22"),
        "coat_s1_angle2": data.get("coat_s1_angle2", "0-22"),
        "coat_s2_angle1": data.get("coat_s2_angle1", "0-22"),
        "coat_s2_angle2": data.get("coat_s2_angle2", "0-22"),
        "coat_preset": data.get("coat_preset", _current_settings.get("coat_preset", "SQ-A1")),
        "proc_c_single": data.get("proc_c_single", _current_settings.get("proc_c_single", "60\u2033")),
        "proc_c_assembly": data.get("proc_c_assembly", _current_settings.get("proc_c_assembly", "60\u2033")),
        "proc_b": data.get("proc_b", _current_settings.get("proc_surface_defect", "60/40")),
        "N_mode": data.get("N_mode", _current_settings.get("proc_N_mode", "auto")),
        "N_manual": data.get("N_manual", _current_settings.get("proc_N_manual", "1.5")),
        "DN": data.get("DN", _current_settings.get("proc_DN", "0.3")),
        "signature": data.get("signature", _current_settings.get("proc_signature", "l.y.h")),
        "proc_ranking": data.get("proc_ranking", _current_settings.get("proc_ranking", "01")),
        # Chamfer overrides from draw module
        "chamfer_mode": data.get("chamfer_mode", _current_settings.get("chamfer_mode", "auto")),
        "chamfer_left": float(data.get("chamfer_left", _current_settings.get("chamfer_left", 0.2)) or 0.2),
        "chamfer_right": float(data.get("chamfer_right", _current_settings.get("chamfer_right", 0.4)) or 0.4),
        # Tolerance overrides from draw module
        "t_tol": float(data.get("t_tol", _current_settings.get("t_tol", 0.02)) or 0.02),
        "sag_tol": float(data.get("sag_tol", _current_settings.get("sag_tol", 0.02)) or 0.02),
        "dia_tol_pos_upper": float(data.get("dia_tol_pos_upper", _current_settings.get("dia_tol_pos_upper", _current_settings.get("dia_tol_upper", 0.010))) or 0.010),
        "dia_tol_pos_lower": float(data.get("dia_tol_pos_lower", _current_settings.get("dia_tol_pos_lower", _current_settings.get("dia_tol_lower", 0.025))) or 0.025),
        "dia_tol_nonpos_upper": float(data.get("dia_tol_nonpos_upper", _current_settings.get("dia_tol_nonpos_upper", 0.05)) or 0.05),
        "dia_tol_nonpos_lower": float(data.get("dia_tol_nonpos_lower", _current_settings.get("dia_tol_nonpos_lower", 0.10)) or 0.10),
        "cemented_ref_lens": int(data.get("cemented_ref_lens", _current_settings.get("cemented_ref_lens", 2)) or 2),
    }


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
        "proc_b": p["proc_b"],
        "N_mode": p["N_mode"],
        "N_manual": p["N_manual"],
        "DN": p["DN"],
        "signature": p["signature"],
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
    from PIL import Image
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

@app.route("/api/preview", methods=["POST"])
def api_preview():
    """Generate a preview PNG (full single-lens PDF page) and return as base64."""
    try:
        p = _params_from_request()
        T, R1, R2, MD, AD1, AD2 = p["T"], p["R1"], p["R2"], p["MD"], p["AD1"], p["AD2"]

        errors = validate(T, R1, R2, MD, AD1, AD2)
        if errors:
            return jsonify({"success": False, "error": "; ".join(errors)})

        cL, cR = auto_chamfer(MD, R1, R2) if p.get("chamfer_mode", _current_settings.get("chamfer_mode", "auto")) == "auto" else (
            p.get("chamfer_left", _current_settings.get("chamfer_left", 0.2)),
            p.get("chamfer_right", _current_settings.get("chamfer_right", 0.4)),
        )

        # CA: auto mode → None (auto_CA uses ca_ratio from settings), manual → float value
        ca_ratio = p.get("ca_ratio", _current_settings.get("ca_ratio", 0.98))
        _current_settings["ca_ratio"] = ca_ratio  # sync to settings for downstream
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

        # 计算实际显示值（用于预览叠加层）
        _ca_ratio2 = p.get("ca_ratio", _current_settings.get("ca_ratio", 0.98))
        _ca1 = ca1 if ca1 is not None else auto_CA(AD1, _ca_ratio2)
        _ca2 = ca2 if ca2 is not None else auto_CA(AD2, _ca_ratio2)
        _n_mode = p.get("N_mode", _current_settings.get("proc_N_mode", "auto"))
        _n_val = float(p.get("N_manual", _current_settings.get("proc_N_manual", "1.5"))) if _n_mode == "manual" else auto_N(MD)
        field_values = {
            "vendor": _current_settings.get("proc_vendor", "CDGM"),
            "ranking": str(p.get("proc_ranking", _current_settings.get("proc_ranking", "01"))),
            "chamfer": f"{cL:.1f}",
            "ca1": f"{_ca1:.2f}",
            "ca2": f"{_ca2:.2f}",
            "c_val": str(p.get("proc_c_single", "60\u2033")),
            "n_val": str(_n_val),
            "dn_val": str(p.get("DN", "0.3")),
            "b_val": str(p.get("proc_b", "60/40")),
            "signature": str(p.get("signature", "l.y.h")),
        }

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
            settings=_current_settings,
            ca1=ca1, ca2=ca2,
        )

        resp = _fig_to_preview_response(fig, is_cemented_single=False, field_values=field_values)
        plt.close(fig)
        return jsonify({"success": True, **resp})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()})


@app.route("/api/export", methods=["POST"])
def api_export():
    """Export PDF to the given path."""
    try:
        p = _params_from_request()
        data = request.get_json(force=True) or {}
        filepath = data.get("filepath", "")
        if not filepath:
            return jsonify({"success": False, "error": "No filepath provided"})

        T, R1, R2, MD, AD1, AD2 = p["T"], p["R1"], p["R2"], p["MD"], p["AD1"], p["AD2"]
        errors = validate(T, R1, R2, MD, AD1, AD2)
        if errors:
            return jsonify({"success": False, "error": "; ".join(errors)})

        cL, cR = auto_chamfer(MD, R1, R2) if p.get("chamfer_mode", _current_settings.get("chamfer_mode", "auto")) == "auto" else (
            p.get("chamfer_left", _current_settings.get("chamfer_left", 0.2)),
            p.get("chamfer_right", _current_settings.get("chamfer_right", 0.4)),
        )

        # CA: auto mode → None (auto_CA uses ca_ratio from settings), manual → float value
        ca_ratio = p.get("ca_ratio", _current_settings.get("ca_ratio", 0.98))
        _current_settings["ca_ratio"] = ca_ratio  # sync to settings for downstream
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
            proc_params, _current_settings, ca1=ca1, ca2=ca2,
        )

        return jsonify({"success": True, "path": filepath})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()})


def _cemented_data_from_request(data):
    """Build CementedLensData from JSON request."""
    lenses = []
    for item in data.get("lenses", []):
        lenses.append(SingleLensData(
            glass=item.get("glass", ""),
            T=float(item.get("T", 0)),
            R_left=float(item.get("R_left", 0)),
            R_right=float(item.get("R_right", 0)),
            MD=float(item.get("MD", 0)),
            AD_left=float(item.get("AD_left", 0)),
            AD_right=float(item.get("AD_right", 0)),
        ))
    return CementedLensData(
        part_name=data.get("part_name", "cemented"),
        part_no=data.get("part_no", ""),
        lenses=lenses,
    )


def _cemented_augmented_settings(data):
    """Create a local settings copy with proc overrides from cemented request.
    Does NOT modify global _current_settings — overrides are session-local.
    Maps draw-module field names → settings keys.
    """
    settings = _current_settings.copy()
    mapping = {
        "proc_c_single": "proc_c_single",
        "proc_c_assembly": "proc_c_assembly",
        "proc_b": "proc_surface_defect",
        "proc_ranking": "proc_ranking",
        "N_mode": "proc_N_mode",
        "N_manual": "proc_N_manual",
        "DN": "proc_DN",
        "signature": "proc_signature",
        "chamfer_mode": "chamfer_mode",
        "chamfer_left": "chamfer_left",
        "chamfer_right": "chamfer_right",
        "t_tol": "t_tol",
        "sag_tol": "sag_tol",
        "dia_tol_pos_upper": "dia_tol_pos_upper",
        "dia_tol_pos_lower": "dia_tol_pos_lower",
        "dia_tol_nonpos_upper": "dia_tol_nonpos_upper",
        "dia_tol_nonpos_lower": "dia_tol_nonpos_lower",
        "cemented_ref_lens": "cemented_ref_lens",
        "coat_preset": "coat_preset",
    }
    for draw_key, settings_key in mapping.items():
        if draw_key in data:
            settings[settings_key] = data[draw_key]
    return settings


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

        # Build all figures: [(label, fig), ...]
        figures = build_cemented_preview_figures(cemented_data, local_settings)

        images = []
        labels = []
        image_sizes = []   # 每页 PNG 实际像素尺寸 [{w, h}, ...]
        fields_by_page = []  # 每页的字段数组（与 images 平行）
        for label, fig in figures:
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=PREVIEW_DPI, pad_inches=0)
            buf.seek(0)
            img_b64 = base64.b64encode(buf.read()).decode("utf-8")
            images.append(img_b64)

            # 读取实际 PNG 尺寸用于前端精确校正
            buf.seek(0)
            from PIL import Image
            pil_img = Image.open(buf)
            image_sizes.append({"w": pil_img.size[0], "h": pil_img.size[1]})

            labels.append(label)
            # 仅单片页附带字段坐标（组装页为空数组）
            is_single = label.startswith("镜片")
            if is_single:
                # 提取当前镜片索引并计算实际字段值
                lens_idx = int(label.replace("镜片", "")) - 1
                lens = cemented_data.lenses[lens_idx]
                _ca_ratio = local_settings.get("ca_ratio", 0.98)
                _ca1 = auto_CA(lens.AD_left, _ca_ratio)
                _ca2 = auto_CA(lens.AD_right, _ca_ratio)
                _n_mode = local_settings.get("proc_N_mode", "auto")
                _n_val = float(local_settings.get("proc_N_manual", "1.5")) if _n_mode == "manual" else auto_N(lens.MD)
                _chamfer_mode = local_settings.get("chamfer_mode", "auto")
                if _chamfer_mode == "auto":
                    _cL = _cR = auto_chamfer_by_dia(lens.MD)
                else:
                    _cL = local_settings.get("chamfer_left", 0.2)
                    _cR = local_settings.get("chamfer_right", 0.4)
                field_values = {
                    "vendor": local_settings.get("proc_vendor", "CDGM"),
                    "ranking": str(local_settings.get("proc_ranking", "01")),
                    "chamfer": f"{_cL:.1f}",
                    "ca1": f"{_ca1:.2f}",
                    "ca2": f"{_ca2:.2f}",
                    "c_val": str(local_settings.get("proc_c_single", "60\u2033")),
                    "n_val": str(_n_val),
                    "dn_val": str(local_settings.get("proc_DN", "0.3")),
                    "b_val": str(local_settings.get("proc_surface_defect", "60/40")),
                    "signature": str(local_settings.get("proc_signature", "l.y.h")),
                }
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

            plt.close(fig)

        return jsonify({"success": True, "images": images, "labels": labels,
                        "fields_by_page": fields_by_page, "image_sizes": image_sizes})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()})


@app.route("/api/export/cemented", methods=["POST"])
def api_export_cemented():
    """Export cemented lens PDF to the given path."""
    try:
        data = request.get_json(force=True) or {}
        filepath = data.get("filepath", "")
        if not filepath:
            return jsonify({"success": False, "error": "No filepath provided"})

        cemented_data = _cemented_data_from_request(data)
        if len(cemented_data.lenses) < 2:
            return jsonify({"success": False, "error": "胶合镜片至少需要2片"})

        # Use augmented settings with draw-module proc overrides (does not mutate global)
        local_settings = _cemented_augmented_settings(data)
        export_cemented_pdf(cemented_data, local_settings, filepath)
        return jsonify({"success": True, "path": filepath})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()})


@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    return jsonify(_current_settings)


@app.route("/api/settings", methods=["POST"])
def api_settings_save():
    try:
        updates = request.get_json(force=True) or {}
        _merge_settings(updates)
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

        # Save to temp
        ext = os.path.splitext(file.filename)[1].lower()
        tmp_path = os.path.join(PROJECT_ROOT, f"_temp_batch{ext}")
        file.save(tmp_path)

        if ext == ".csv":
            rows, warnings = read_csv_file(tmp_path)
        else:
            rows, warnings = read_excel(tmp_path)
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
                "T1": lenses[0].T if len(lenses) > 0 else 0,
                "T2": lenses[1].T if len(lenses) > 1 else 0,
                "T3": lenses[2].T if len(lenses) > 2 else 0,
                "R1": lenses[0].R_left if len(lenses) > 0 else 0,
                "R2": lenses[0].R_right if len(lenses) > 0 else 0,
                "R3": lenses[1].R_right if len(lenses) > 1 else 0,
                "R4": lenses[2].R_right if len(lenses) > 2 else 0,
                "MD1": lenses[0].MD if len(lenses) > 0 else 0,
                "MD2": lenses[1].MD if len(lenses) > 1 else 0,
                "MD3": lenses[2].MD if len(lenses) > 2 else 0,
                "AD1": lenses[0].AD_left if len(lenses) > 0 else 0,
                "AD2": lenses[0].AD_right if len(lenses) > 0 else 0,
                "AD3": lenses[1].AD_right if len(lenses) > 1 else 0,
                "AD4": lenses[2].AD_right if len(lenses) > 2 else 0,
                "lens_type": row.lens_type,
                "save_pdf_folder": row.save_pdf_folder,
                "mfr_pdf_folder": row.mfr_pdf_folder,
            })

        resp = {"success": True, "data": items, "count": len(items)}
        if warnings:
            resp["warnings"] = warnings
        return jsonify(resp)
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()})


@app.route("/api/validate", methods=["POST"])
def api_validate():
    """Validate parameters and return errors (if any)."""
    try:
        p = _params_from_request()
        T, R1, R2, MD, AD1, AD2 = p["T"], p["R1"], p["R2"], p["MD"], p["AD1"], p["AD2"]
        errors = validate(T, R1, R2, MD, AD1, AD2)
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

        # Convert row dicts -> CementedLensData list (single items become 1-lens CementedLensData)
        data_list = [_cemented_data_from_row_dict(r) for r in rows]

        result = batch_export_data_list(data_list, output_dir, _current_settings)

        return jsonify({
            "success": True,
            "total": result["total"],
            "success_save": result["success_save"],
            "success_mfr": result["success_mfr"],
            "failed_count": (result["total"] - result["success_save"]) + (result["total"] - result["success_mfr"]),
            "errors": result["errors"],
            "output_dir": output_dir,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()})


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

        filepath = os.path.join(output_dir, filename)
        export_batch_excel(filepath, rows)
        return jsonify({"success": True, "path": filepath})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()})


@app.route("/api/save-text-file", methods=["POST"])
def api_save_text_file():
    """Save text content to the given file path."""
    try:
        data = request.get_json(force=True) or {}
        filepath = data.get("path", "")
        content = data.get("content", "")
        if not filepath:
            return jsonify({"success": False, "error": "No path provided"})
        with open(filepath, "w", encoding="utf-8-sig") as f:
            f.write(content)
        return jsonify({"success": True, "path": filepath})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()})


# ══════════════════════════════════════════════════════════════════════
#  Entry Point
# ══════════════════════════════════════════════════════════════════════

def get_free_port(host="127.0.0.1"):
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def run_server_in_thread(host="127.0.0.1", port=0):
    """Start Flask server in a background thread. Returns actual port."""
    import threading
    if port == 0:
        port = get_free_port(host)
    t = threading.Thread(target=lambda: app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False), daemon=True)
    t.start()
    return port


if __name__ == "__main__":
    run_server_in_thread(port=5000)
