"""Multimodal space registry (M2.2 B3).

Why this exists
---------------
ADR-022 D5 promised that "a new space is a new table + a new migration, and
the enterprise edition adds providers/modalities without changing this
protocol". Until B3 that promise was only half true: ``MultimodalStore``
hard-coded the table name, the dimension and the two built-in modalities
(``multimodal_entries`` / 512 / text+image), so a second space — audio, a
different CLIP dimension — could not be added without editing the core.

This module is the declarative half of the fix: a :class:`MultimodalSpaceSpec`
plus a process-local registry. The built-in CLIP space registers itself at
import with **exactly** today's values, so existing deployments are unchanged;
everything else (audio, video, a second CLIP dimension) is a registration away.

Scope boundary (ADR-016/022-D5): the *implementations* of audio/video
providers live in the private enterprise repo (MAOS). This module is the
mechanism and the contract, not the models — no Whisper, CLAP or ffmpeg code
belongs here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from maop.core.memory.multimodal_embed import MULTIMODAL_DIM
from maop.core.memory.multimodal_schema import MM_TABLE, pg_ddl

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_SPACE_NAME",
    "MultimodalSpaceSpec",
    "default_space_spec",
    "get_multimodal_space",
    "list_multimodal_spaces",
    "register_multimodal_space",
    "render_pg_migration",
    "reset_multimodal_spaces",
]

#: Name of the built-in CLIP space. Kept as ``multimodal-512`` (not
#: ``clip-512``) because ``stats()["space"]`` has reported that string since
#: B1 — renaming it would be a behaviour change for every consumer.
DEFAULT_SPACE_NAME = "multimodal-512"

#: Backends a space may target. ``auto`` follows the process-wide backend
#: selection (``MAOP_DB_BACKEND`` / ``MAOP_MULTIMODAL_BACKEND``), resolved by
#: the service layer exactly like the default space is.
_BACKENDS = ("auto", "sqlite", "pg", "postgresql")


@dataclass(frozen=True)
class MultimodalSpaceSpec:
    """Declarative description of one multimodal vector space.

    Attributes
    ----------
    name:
        Process-unique key, also reported as ``stats()["space"]``.
    table_name:
        Physical table (SQLite file, PG schema). Two spaces in the same
        database must use different names or they would silently share rows.
    dimension:
        Vector width. Drives ``vector(N)`` in the PG DDL and the dimension
        check on the write path.
    modalities:
        The modality strings this space accepts (open set — the core ships
        ``text``/``image``; the enterprise repo registers ``audio`` etc.).
    backend:
        ``auto`` (follow the process-wide backend) or an explicit target.
    model_id:
        Provenance of the embedding model (ADR-022 D9: a model change is a
        new space). Recorded in ``stats()``.
    """

    name: str
    table_name: str
    dimension: int = MULTIMODAL_DIM
    modalities: tuple[str, ...] = ("text", "image")
    backend: str = "auto"
    model_id: str = ""
    description: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.table_name:
            raise ValueError("space name and table_name are required")
        if self.dimension <= 0:
            raise ValueError(f"space {self.name!r} dimension must be positive")
        if self.backend not in _BACKENDS:
            raise ValueError(
                f"space {self.name!r} backend {self.backend!r} not in {_BACKENDS}"
            )
        if not self.modalities:
            raise ValueError(f"space {self.name!r} must accept at least one modality")
        for modality in self.modalities:
            if not modality or not modality.replace("_", "").replace("-", "").isalnum():
                raise ValueError(f"space {self.name!r} has invalid modality {modality!r}")

    def accepts(self, modality: str) -> bool:
        """Whether *modality* is one of this space's registered modalities."""
        return modality in self.modalities

    def supported_modalities(self) -> str:
        """Human-readable list for error messages."""
        return ", ".join(sorted(self.modalities))


def default_space_spec() -> MultimodalSpaceSpec:
    """The built-in CLIP space — byte-for-byte the pre-B3 configuration."""
    return MultimodalSpaceSpec(
        name=DEFAULT_SPACE_NAME,
        table_name=MM_TABLE,
        dimension=MULTIMODAL_DIM,
        modalities=("text", "image"),
        backend="auto",
        model_id="openai/clip-vit-base-patch32",
        description="CLIP text+image space (ADR-022)",
    )


_SPACES: dict[str, MultimodalSpaceSpec] = {DEFAULT_SPACE_NAME: default_space_spec()}


def register_multimodal_space(spec: MultimodalSpaceSpec) -> MultimodalSpaceSpec:
    """Register (or re-declare identically) a space; returns the spec.

    Re-registering a name with a **different** definition raises instead of
    silently overwriting: an already-built store would keep serving the old
    table/dimension while the registry claims otherwise — the exact "config
    says one thing, runtime does another" failure this repo's gates exist to
    prevent. Tests replace spaces via :func:`reset_multimodal_spaces`.
    """
    existing = _SPACES.get(spec.name)
    if existing is not None and existing != spec:
        raise ValueError(
            f"multimodal space {spec.name!r} is already registered with a "
            f"different definition (table={existing.table_name!r}, "
            f"dim={existing.dimension}). Reset the registry first, or pick a "
            f"new space name."
        )
    _SPACES[spec.name] = spec
    return spec


def get_multimodal_space(name: str | None = None) -> MultimodalSpaceSpec:
    """Look up a space; ``None`` selects the built-in default space.

    Unknown names raise with the available spaces listed (a typo must not
    silently fall back to the CLIP space).
    """
    if name is None:
        return _SPACES[DEFAULT_SPACE_NAME]
    spec = _SPACES.get(name)
    if spec is None:
        raise KeyError(
            f"unknown multimodal space {name!r}; available: "
            f"{', '.join(sorted(_SPACES))}"
        )
    return spec


def list_multimodal_spaces() -> list[MultimodalSpaceSpec]:
    """All registered spaces, sorted by name (also the capability probe for
    the enterprise edition: "does this MAOP know about audio-512?")."""
    return [_SPACES[name] for name in sorted(_SPACES)]


def reset_multimodal_spaces() -> None:
    """Drop all registrations and restore the built-in default (test isolation)."""
    _SPACES.clear()
    _SPACES[DEFAULT_SPACE_NAME] = default_space_spec()


# ── migration rendering (ADR-022 D5: "a new space is a new migration") ─────


def render_pg_migration(
    spec: MultimodalSpaceSpec, revision: str, down_revision: str
) -> str:
    """Render an alembic revision that creates *spec*'s table.

    The enterprise edition (MAOS) calls this instead of hand-writing DDL, so
    the SQLite runtime schema and the PG migration cannot drift — the same
    failure the ``multimodal_schema`` single-source module exists to prevent.
    Index names are derived from the table name, so two spaces in one
    database never collide on them.
    """
    if not _revision_ok(revision) or not _revision_ok(down_revision):
        raise ValueError("revision ids must match ^[A-Za-z0-9_.-]+$")
    ddl = pg_ddl(table=spec.table_name, dim=spec.dimension)
    statements = [s.strip() for s in ddl.split(";") if s.strip()]
    body = "\n\n".join(f'    op.execute(sa.text(\n        """{stmt}"""\n    ))' for stmt in statements)
    drop = f'    op.execute(sa.text("DROP TABLE IF EXISTS {spec.table_name}"))'
    return f'''"""Create the {spec.name} multimodal space.

Generated by ``maop.core.memory.multimodal_spaces.render_pg_migration`` from
the space spec — do not hand-edit; re-generate instead.

Space: {spec.name} (table={spec.table_name}, dim={spec.dimension},
modalities={list(spec.modalities)}, model={spec.model_id or "unspecified"}).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "{revision}"
down_revision = "{down_revision}"
branch_labels = None
depends_on = None


def upgrade() -> None:
{body}


def downgrade() -> None:
{drop}
'''



_REVISION_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _revision_ok(value: str) -> bool:
    return bool(_REVISION_RE.match(value))
