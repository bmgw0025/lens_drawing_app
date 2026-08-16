from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SurfaceRecord:
    index: int
    type_name: str
    comment: str
    radius: float
    thickness: float
    material: str
    semi_diameter: float
    mechanical_semi_diameter: float
    aperture_type: str
    explicit_aperture_radius: float | None
    coating: str
    is_stop: bool
    is_object: bool
    is_image: bool
    solves: dict[str, str] = field(default_factory=dict)
    tilt_decenter: dict[str, float] = field(default_factory=dict)


@dataclass
class ExtractedSystem:
    source_file: str
    source_sha256: str
    source_size: int
    provider: str
    opticstudio_version: str
    license_status: str
    mode: str
    lens_units: str
    unit_to_mm: float | None
    title: str
    configuration_count: int
    current_configuration: int
    surfaces: list[SurfaceRecord]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Provenance:
    field: str
    source: str
    raw_value: Any
    converted_value: Any
    confidence: str


@dataclass
class LensGeometry:
    lens_position: int
    glass: str
    T: float | None
    R_left: float | None
    R_right: float | None
    MD: float | None
    AD_left: float | None
    AD_right: float | None
    left_surface: int
    right_surface: int


@dataclass
class DrawingDraft:
    group_index: int
    surface_range: list[int]
    row: dict[str, Any]
    provenance: list[Provenance]
    status: str
    confidence: str
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    topology: dict[str, Any] = field(default_factory=dict)
    lenses: list[LensGeometry] = field(default_factory=list)
    legacy_row_compatible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
