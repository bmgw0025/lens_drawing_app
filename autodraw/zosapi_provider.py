from __future__ import annotations

import hashlib
import math
import msvcrt
import os
import tempfile
import time
import winreg
from pathlib import Path
from typing import Any

from .models import ExtractedSystem, SurfaceRecord


DEFAULT_INSTALL_DIR = Path(r"C:\Program Files\Ansys Zemax OpticStudio 2022 R2.01")


class ZosApiError(RuntimeError):
    pass


class ZosApiSessionLock:
    """Machine-local process lock so only one OpticStudio API session runs at a time."""

    def __init__(self, timeout_seconds: float = 120.0):
        self.timeout_seconds = timeout_seconds
        self.path = Path(tempfile.gettempdir()) / "lens_drawing_zosapi.lock"
        self.stream = None

    def __enter__(self) -> "ZosApiSessionLock":
        self.stream = self.path.open("a+b")
        self.stream.seek(0, os.SEEK_END)
        if self.stream.tell() == 0:
            self.stream.write(b"0")
            self.stream.flush()
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                return self
            except OSError as exc:
                if time.monotonic() >= deadline:
                    self.stream.close()
                    self.stream = None
                    raise ZosApiError(
                        f"等待其他 Agent 释放 ZOS-API 会话超时 ({self.timeout_seconds:g}s)"
                    ) from exc
                time.sleep(0.2)

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.stream is None:
            return
        try:
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.stream.close()
            self.stream = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _getattr_default(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name)
    except Exception:
        return default


def _float_or(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _unit_factor(unit_name: str) -> float | None:
    normalized = unit_name.lower().replace(" ", "")
    factors = {
        "millimeters": 1.0,
        "millimeter": 1.0,
        "mm": 1.0,
        "centimeters": 10.0,
        "centimeter": 10.0,
        "cm": 10.0,
        "meters": 1000.0,
        "meter": 1000.0,
        "m": 1000.0,
        "inches": 25.4,
        "inch": 25.4,
        "in": 25.4,
    }
    return factors.get(normalized)


def _solve_name(cell: Any) -> str:
    solve = _getattr_default(cell, "Solve", "Unknown")
    return str(solve)


def _read_explicit_aperture(surface: Any) -> tuple[str, float | None]:
    try:
        aperture = surface.ApertureData
        aperture_type = str(aperture.CurrentType)
        settings = aperture.CurrentTypeSettings
    except Exception:
        return "Unavailable", None

    lowered = aperture_type.lower()
    if "circularaperture" in lowered and "obscuration" not in lowered:
        maximum = _float_or(_getattr_default(settings, "MaximumRadius"), 0.0)
        return aperture_type, maximum if maximum > 0 else None
    if lowered.endswith("none") or lowered == "none":
        return aperture_type, None
    return aperture_type, None


def _read_tilt_decenter(surface: Any) -> dict[str, float]:
    try:
        data = surface.TiltDecenterData
    except Exception:
        return {}
    names = (
        "BeforeSurfaceDecenterX",
        "BeforeSurfaceDecenterY",
        "BeforeSurfaceTiltX",
        "BeforeSurfaceTiltY",
        "BeforeSurfaceTiltZ",
        "AfterSurfaceDecenterX",
        "AfterSurfaceDecenterY",
        "AfterSurfaceTiltX",
        "AfterSurfaceTiltY",
        "AfterSurfaceTiltZ",
    )
    values = {}
    for name in names:
        value = _float_or(_getattr_default(data, name), 0.0)
        if abs(value) > 1e-12:
            values[name] = value
    return values


class NativeZosApiProvider:
    """Read-only OpticStudio bridge for one file per process."""

    def __init__(self, install_dir: str | os.PathLike[str] | None = None):
        self.install_dir = Path(install_dir or os.environ.get("ZEMAX_INSTALL_DIR") or DEFAULT_INSTALL_DIR)
        self.app = None
        self.system = None
        self.connection = None
        self.ZOSAPI = None
        self.session_lock = None

    def __enter__(self) -> "NativeZosApiProvider":
        self.session_lock = ZosApiSessionLock()
        self.session_lock.__enter__()
        try:
            import clr

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Zemax") as key:
                zemax_root = winreg.QueryValueEx(key, "ZemaxRoot")[0]
            nethelper = Path(zemax_root) / "ZOS-API" / "Libraries" / "ZOSAPI_NetHelper.dll"
            if not nethelper.is_file():
                raise ZosApiError(f"未找到 ZOSAPI_NetHelper.dll: {nethelper}")

            clr.AddReference(str(nethelper))
            import ZOSAPI_NetHelper

            if not ZOSAPI_NetHelper.ZOSAPI_Initializer.Initialize(str(self.install_dir)):
                raise ZosApiError(f"ZOS-API 初始化失败: {self.install_dir}")
            resolved = Path(ZOSAPI_NetHelper.ZOSAPI_Initializer.GetZemaxDirectory())
            clr.AddReference(str(resolved / "ZOSAPI.dll"))
            clr.AddReference(str(resolved / "ZOSAPI_Interfaces.dll"))
            import ZOSAPI

            self.ZOSAPI = ZOSAPI
            self.connection = ZOSAPI.ZOSAPI_Connection()
            self.app = self.connection.CreateNewApplication()
            if self.app is None:
                raise ZosApiError("CreateNewApplication 未返回 OpticStudio 实例")
            if not self.app.IsValidLicenseForAPI:
                raise ZosApiError("当前 OpticStudio 许可证不允许使用 ZOS-API")
            self.system = self.app.PrimarySystem
            if self.system is None:
                raise ZosApiError("无法取得 PrimarySystem")
            return self
        except OSError as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise ZosApiError("无法从 HKCU\\Software\\Zemax 定位 ZemaxRoot") from exc
        except Exception as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if self.system is not None:
                try:
                    self.system.Close(False)
                except Exception:
                    pass
        finally:
            if self.app is not None:
                try:
                    self.app.CloseApplication()
                except Exception:
                    pass
        self.system = None
        self.app = None
        self.connection = None
        if self.session_lock is not None:
            self.session_lock.__exit__(exc_type, exc, traceback)
            self.session_lock = None

    def extract(self, source_file: str | os.PathLike[str]) -> ExtractedSystem:
        source = Path(source_file).resolve()
        if source.suffix.lower() != ".zmx":
            raise ZosApiError("当前原型仅接受 .zmx 文件")
        if not source.is_file():
            raise ZosApiError(f"ZMX 文件不存在: {source}")
        if self.system is None or self.app is None:
            raise ZosApiError("ZOS-API provider 尚未连接")

        source_hash_before = _sha256(source)
        self.system.LoadFile(str(source), False)
        try:
            self.system.UpdateStatus()
        except Exception:
            pass

        mode = str(self.system.Mode)
        units = str(self.system.SystemData.Units.LensUnits)
        lde = self.system.LDE
        surfaces = []
        for index in range(lde.NumberOfSurfaces):
            surface = lde.GetSurfaceAt(index)
            aperture_type, aperture_radius = _read_explicit_aperture(surface)
            surfaces.append(
                SurfaceRecord(
                    index=index,
                    type_name=str(_getattr_default(surface, "TypeName", "")),
                    comment=str(_getattr_default(surface, "Comment", "")),
                    radius=_float_or(_getattr_default(surface, "Radius"), math.inf),
                    thickness=_float_or(_getattr_default(surface, "Thickness"), math.inf),
                    material=str(_getattr_default(surface, "Material", "")).strip(),
                    semi_diameter=_float_or(_getattr_default(surface, "SemiDiameter")),
                    mechanical_semi_diameter=_float_or(
                        _getattr_default(surface, "MechanicalSemiDiameter")
                    ),
                    aperture_type=aperture_type,
                    explicit_aperture_radius=aperture_radius,
                    coating=str(_getattr_default(surface, "Coating", "")).strip(),
                    is_stop=bool(_getattr_default(surface, "IsStop", False)),
                    is_object=bool(_getattr_default(surface, "IsObject", index == 0)),
                    is_image=bool(
                        _getattr_default(surface, "IsImage", index == lde.NumberOfSurfaces - 1)
                    ),
                    solves={
                        "radius": _solve_name(surface.RadiusCell),
                        "thickness": _solve_name(surface.ThicknessCell),
                        "material": _solve_name(surface.MaterialCell),
                        "semi_diameter": _solve_name(surface.SemiDiameterCell),
                        "mechanical_semi_diameter": _solve_name(
                            surface.MechanicalSemiDiameterCell
                        ),
                    },
                    tilt_decenter=_read_tilt_decenter(surface),
                )
            )

        title = str(_getattr_default(self.system.SystemData, "Title", "")).strip()
        mce = _getattr_default(self.system, "MCE")
        configuration_count = int(_getattr_default(mce, "NumberOfConfigurations", 1) or 1)
        current_configuration = int(_getattr_default(mce, "CurrentConfiguration", 1) or 1)
        app_version = str(
            _getattr_default(self.app, "ZemaxVersion", "OpticStudio 2022 R2.01")
        )
        source_hash_after = _sha256(source)
        if source_hash_after != source_hash_before:
            raise ZosApiError("源 ZMX 在只读提取会话中发生变化，已拒绝继续出图")
        return ExtractedSystem(
            source_file=str(source),
            source_sha256=source_hash_before,
            source_size=source.stat().st_size,
            provider="native-pythonnet-zosapi",
            opticstudio_version=app_version,
            license_status=str(self.app.LicenseStatus),
            mode=mode,
            lens_units=units,
            unit_to_mm=_unit_factor(units),
            title=title,
            configuration_count=configuration_count,
            current_configuration=current_configuration,
            surfaces=surfaces,
        )
