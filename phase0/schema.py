"""Layout JSON schema.

Phase 0 hand-writes this file; Phase 1 has the model emit it. Same validator
either way. On schema failure the rule is: reject and retry the whole call.
Never hand-repair model output — a repaired layout hides the fact that the
prompt is wrong.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

import templates_lib

CaptionPosition = Literal[
    "top-left", "top-right", "bottom-left", "bottom-right", "below", "none"
]


class Meta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    subtitle: str | None = None
    language: str = "de"
    format: str = "21x21"
    spread_count: int = Field(ge=1)


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    intro_text: str | None = None


class Slot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    photo_id: str
    focal: tuple[float, float] = (0.5, 0.5)
    caption: str | None = None
    caption_position: CaptionPosition = "bottom-left"
    # Set by the user through overrides.yaml, never by the planner. The model
    # is not asked for visual treatments: it cannot see the result.
    treatment: Literal["none", "frame", "rounded", "vignette", "soft-edge"] = "none"

    @field_validator("focal")
    @classmethod
    def _in_unit_square(cls, v):
        for c in v:
            if not 0.0 <= c <= 1.0:
                raise ValueError("focal coordinates are normalised to 0..1")
        return v


class SpreadText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eyebrow: str | None = None
    title: str | None = None
    subtitle: str | None = None
    body: str | None = None


class Spread(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1)
    section_id: str | None = None
    template: str
    slots: list[Slot] = Field(default_factory=list)
    text: SpreadText | None = None
    options: dict = Field(default_factory=dict)
    note: str | None = None

    @field_validator("template")
    @classmethod
    def _known_template(cls, v):
        if v not in templates_lib.REGISTRY:
            raise ValueError(f"unknown template {v!r}")
        return v

    @model_validator(mode="after")
    def _slot_count(self):
        want = templates_lib.REGISTRY[self.template].slot_count
        if len(self.slots) != want:
            raise ValueError(
                f"template {self.template!r} takes {want} slots, got {len(self.slots)}"
            )
        return self


class Cover(BaseModel):
    model_config = ConfigDict(extra="forbid")

    front_photo_id: str | None = None
    front_focal: tuple[float, float] = (0.5, 0.5)
    title: str | None = None
    subtitle: str | None = None
    spine_title: str | None = None
    back_text: str | None = None


class Layout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: Meta
    sections: list[Section] = Field(default_factory=list)
    spreads: list[Spread]
    cover: Cover | None = None
    change_summary: str | None = None

    @model_validator(mode="after")
    def _coherent(self):
        if len(self.spreads) != self.meta.spread_count:
            raise ValueError(
                f"meta.spread_count is {self.meta.spread_count} but there are "
                f"{len(self.spreads)} spreads"
            )
        seen = [s.index for s in self.spreads]
        if seen != sorted(seen) or len(set(seen)) != len(seen):
            raise ValueError("spread indices must be unique and ascending")
        ids = {s.id for s in self.sections}
        for sp in self.spreads:
            if sp.section_id and sp.section_id not in ids:
                raise ValueError(f"spread {sp.index} references unknown section {sp.section_id!r}")
        return self

    @property
    def page_count(self) -> int:
        return len(self.spreads) * 2
