"""Layout version history on the filesystem.

Deliberately mirrors the `layout_version` table from PLAN.md — version_no,
parent_version_id, json, user_comment, change_summary — so Phase 2 can move it
into Postgres without changing the shape of anything above it. A revision is
always a new version with a parent, never an edit in place; that is what makes
rollback free.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Version:
    version_no: int
    parent_version_id: int | None
    user_comment: str | None
    change_summary: str | None
    created_at: str
    # What the design session that produced this version cost, in USD (None for
    # versions no model was asked for: settings re-applied, rollbacks).
    cost_usd: float | None = None
    kind: str | None = None         # "design" | "revise" | "settings" | "rollback"

    @property
    def filename(self) -> str:
        return f"v{self.version_no:03d}.json"

    @property
    def raw_filename(self) -> str:
        return f"v{self.version_no:03d}.raw.json"


class Store:
    def __init__(self, album_dir: Path):
        self.dir = album_dir
        self.layouts = album_dir / "layouts"
        self.layouts.mkdir(parents=True, exist_ok=True)
        self.index_path = album_dir / "versions.json"

    def _index(self) -> list[dict]:
        if not self.index_path.exists():
            return []
        return json.loads(self.index_path.read_text())

    def versions(self) -> list[Version]:
        known = set(Version.__dataclass_fields__)
        return [Version(**{k: x for k, x in v.items() if k in known}) for v in self._index()]

    def latest(self) -> Version | None:
        vs = self.versions()
        return vs[-1] if vs else None

    def get(self, version_no: int | None = None) -> tuple[Version, dict]:
        vs = self.versions()
        if not vs:
            raise SystemExit("no layout versions yet — run `plan` first")
        v = vs[-1] if version_no is None else next(
            (x for x in vs if x.version_no == version_no), None
        )
        if v is None:
            raise SystemExit(f"no version {version_no}")
        return v, json.loads((self.layouts / v.filename).read_text())

    def add(
        self, layout: dict, *, parent: int | None,
        user_comment: str | None = None, change_summary: str | None = None,
        raw: dict | None = None, cost_usd: float | None = None, kind: str | None = None,
    ) -> Version:
        """`raw` is the layout as the model returned it, before the user's
        settings were applied. Kept beside the version so the settings can be
        re-applied later — bringing back a photograph that was left out, say —
        without asking the model again."""
        vs = self.versions()
        v = Version(
            version_no=(vs[-1].version_no + 1) if vs else 1,
            parent_version_id=parent,
            user_comment=user_comment,
            change_summary=change_summary,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            cost_usd=(round(cost_usd, 4) if cost_usd is not None else None),
            kind=kind,
        )
        (self.layouts / v.filename).write_text(
            json.dumps(layout, ensure_ascii=False, indent=2)
        )
        if raw is not None:
            (self.layouts / v.raw_filename).write_text(
                json.dumps(raw, ensure_ascii=False, indent=2))
        self.index_path.write_text(
            json.dumps([asdict(x) for x in vs + [v]], ensure_ascii=False, indent=2)
        )
        return v

    def rollback(self, to_version: int) -> Version:
        """Rolling back writes a NEW version with the old content.

        History is append-only. The user can always get back to whatever they
        rolled away from, which is the whole reason for keeping versions.
        """
        old, layout = self.get(to_version)
        return self.add(
            layout, parent=self.latest().version_no if self.latest() else None,
            user_comment=f"rollback to v{to_version}",
            change_summary=f"restored the layout from version {to_version}",
            raw=self.raw(to_version), kind="rollback",
        )

    def raw(self, version_no: int | None = None) -> dict | None:
        """The model's own layout for a version, before the user's settings."""
        try:
            v, _ = self.get(version_no)
        except SystemExit:
            return None
        f = self.layouts / v.raw_filename
        return json.loads(f.read_text()) if f.exists() else None

    def history(self) -> str:
        lines = []
        for v in self.versions():
            head = f"v{v.version_no:03d}  {v.created_at}"
            if v.parent_version_id:
                head += f"  (from v{v.parent_version_id:03d})"
            lines.append(head)
            if v.user_comment:
                lines.append(f'         comment: "{v.user_comment}"')
            if v.change_summary:
                lines.append(f"         changed: {v.change_summary}")
        return "\n".join(lines) or "(no versions)"
