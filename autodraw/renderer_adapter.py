from __future__ import annotations

import importlib
import os
import re
from pathlib import Path
from typing import Any

from settings import get_agent_default_settings

from .models import DrawingDraft
from .process_requirements import ApprovedProcessPatch
from .runtime import install_root, is_frozen
from .runtime import renderer_source_manifest as runtime_renderer_source_manifest


DEFAULT_RENDERER_ROOT = install_root()


class RendererError(RuntimeError):
    pass


def _native_renderer_root(renderer_root: str | os.PathLike[str]) -> Path:
    root = Path(renderer_root).resolve()
    expected = install_root().resolve()
    if root != expected:
        raise RendererError(
            f"V4 Agent 只能使用当前运行时内置绘图引擎: {expected}; 收到 {root}"
        )
    return root


def _output_folder(value: Any, label: str) -> str:
    name = str(value or "").strip()
    if (
        not name
        or name in {".", ".."}
        or Path(name).name != name
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
        or name.endswith((" ", "."))
    ):
        raise RendererError(f"{label} 不是安全的单层文件夹名: {name!r}")
    return name


def renderer_source_manifest(
    renderer_root: str | os.PathLike[str] = DEFAULT_RENDERER_ROOT,
) -> dict[str, str]:
    _native_renderer_root(renderer_root)
    return runtime_renderer_source_manifest()


def _load_renderer(renderer_root: str | os.PathLike[str]):
    root = _native_renderer_root(renderer_root)
    if not is_frozen():
        required = ("batch_import.py", "main.py", "settings.py", "config.py")
        missing = [name for name in required if not (root / name).is_file()]
        if missing:
            raise RendererError(f"V4 绘图引擎目录缺少文件: {', '.join(missing)}")
    batch_import = importlib.import_module("batch_import")
    main = importlib.import_module("main")
    settings = importlib.import_module("settings")
    config = importlib.import_module("config")
    return batch_import, main, settings, config


def _build_render_inputs(
    draft: DrawingDraft,
    renderer_root: str | os.PathLike[str],
    process_patch: ApprovedProcessPatch | None,
):
    batch_import, main, settings_module, config = _load_renderer(renderer_root)
    row = draft.row
    if draft.lenses:
        lenses = [
            batch_import.SingleLensData(
                glass=lens.glass,
                T=lens.T,
                R_left=lens.R_left,
                R_right=lens.R_right,
                MD=lens.MD,
                AD_left=lens.AD_left,
                AD_right=lens.AD_right,
            )
            for lens in draft.lenses
        ]
    else:
        lens_count = len([key for key in row if key.startswith("Glass")])
        lenses = [
            batch_import.SingleLensData(
                glass=row[f"Glass{index}"],
                T=row[f"T{index}"],
                R_left=row[f"R{index}"],
                R_right=row[f"R{index + 1}"],
                MD=row[f"MD{index}"],
                AD_left=row[f"AD{index}"],
                AD_right=row[f"AD{index + 1}"],
            )
            for index in range(1, lens_count + 1)
        ]
    errors = config.validate_cemented_lenses(lenses)
    if errors:
        raise RendererError("现有绘图引擎几何校验失败: " + "; ".join(errors))

    effective_settings = get_agent_default_settings()
    page_overrides: dict[str, dict[str, Any]] = {}
    if process_patch is not None:
        effective_settings.update(process_patch.global_overrides)
        effective_settings.update(process_patch.group_overrides.get(str(draft.group_index), {}))
        page_overrides = process_patch.page_overrides.get(str(draft.group_index), {})
    effective_settings.update(settings_module.validate_settings_updates(
        {key: value for key, value in effective_settings.items() if key in settings_module.DEFAULT_SETTINGS}
    ))
    drawing = batch_import.CementedLensData(
        part_name=row["PartName"],
        part_no=row["PartNo"],
        lenses=lenses,
    )
    return main, drawing, effective_settings, page_overrides


def preflight_draft(
    draft: DrawingDraft,
    renderer_root: str | os.PathLike[str] = DEFAULT_RENDERER_ROOT,
    process_patch: ApprovedProcessPatch | None = None,
) -> None:
    if draft.status != "accepted":
        raise RendererError(f"group {draft.group_index} 尚未通过自动接受规则")
    main, drawing, effective_settings, page_overrides = _build_render_inputs(
        draft, renderer_root, process_patch
    )
    validator = getattr(main, "_validate_all_lens_page_settings", None)
    if validator is None:
        raise RendererError("绘图引擎缺少加工要求预检接口")
    validator(drawing.lenses, effective_settings, page_overrides)


def render_draft(
    draft: DrawingDraft,
    output_dir: str | os.PathLike[str],
    renderer_root: str | os.PathLike[str] = DEFAULT_RENDERER_ROOT,
    process_patch: ApprovedProcessPatch | None = None,
) -> dict[str, str | int]:
    if draft.status != "accepted":
        raise RendererError(f"group {draft.group_index} 尚未通过自动接受规则")
    row = draft.row
    main, drawing, effective_settings, page_overrides = _build_render_inputs(
        draft, renderer_root, process_patch
    )
    destination = Path(output_dir).resolve() / "drawings"
    save_dir = destination / _output_folder(
        row.get("SavePdfFolder", "Save PDF"), "SavePdfFolder"
    )
    mfr_dir = destination / _output_folder(
        row.get("MfrPdfFolder", "Mfr PDF"), "MfrPdfFolder"
    )
    save_dir.mkdir(parents=True, exist_ok=True)
    mfr_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / f"{row['PartName']}.pdf"
    mfr_path = mfr_dir / f"{row['PartNo']}.pdf"
    if save_path.resolve() == mfr_path.resolve():
        raise RendererError("存档 PDF 与编码 PDF 输出路径不能相同")
    main.export_cemented_pdf(
        drawing,
        effective_settings,
        str(save_path),
        hide_partname=False,
        page_overrides=page_overrides,
    )
    main.export_cemented_pdf(
        drawing,
        effective_settings,
        str(mfr_path),
        hide_partname=True,
        page_overrides=page_overrides,
    )
    return {
        "group_index": draft.group_index,
        "save_pdf": str(save_path),
        "mfr_pdf": str(mfr_path),
    }
