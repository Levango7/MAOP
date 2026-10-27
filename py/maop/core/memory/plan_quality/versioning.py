"""F2-04-R06 — model version management.

Split from plan_quality.py into plan_quality/versioning.py.
Parent module re-exports via forwarding import; __all__ unchanged.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maop.core.memory.plan_quality.features import PlanQualityModel

logger = logging.getLogger(__name__)

__all__ = ["PlanVersion", "PlanVersionManager"]


@dataclass
class PlanVersion:
    """One immutable entry in the version chain (F2-04-R06)."""

    version_id: str
    created_at: float
    n_samples: int
    train_loss: float
    promoted: bool = False
    """If True, this version is the canonical one served by default."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "created_at": self.created_at,
            "n_samples": self.n_samples,
            "train_loss": self.train_loss,
            "promoted": self.promoted,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlanVersion:
        return cls(**d)


class PlanVersionManager:
    """Persisted version chain stored as JSON (``data/plan_quality_versions.json``).

    Each training run appends a new version.  ``promote(version_id)`` flips
    the active pointer; ``rollback()`` is simply "promote the previous version".
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._path = self._root / "data" / "plan_quality_versions.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # -- persistence helpers -------------------------------------------

    def _load_chain(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _save_chain(self, chain: list[dict[str, Any]]) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(chain, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    # -- public --------------------------------------------------------

    def append(self, model: PlanQualityModel, n_samples: int) -> PlanVersion:
        """Snapshot *model* as a new version and persist it."""
        vid = model.snapshot_version()
        ver = PlanVersion(
            version_id=vid,
            created_at=time.time(),
            n_samples=n_samples,
            train_loss=model._train_loss,
        )
        chain = self._load_chain()
        chain.append(ver.to_dict())

        # Persist model weights alongside the chain entry.
        models_dir = self._path.parent / "plan_quality_models"
        models_dir.mkdir(parents=True, exist_ok=True)
        m_path = models_dir / f"{vid}.json"
        m_path.write_text(json.dumps(model.to_dict(), ensure_ascii=False), encoding="utf-8")

        # Auto-promote the very first version.
        if len(chain) == 1:
            chain[0]["promoted"] = True
        self._save_chain(chain)
        return ver

    def promote(self, version_id: str) -> bool:
        """Promote *version_id* to active (F2-04-R05 / R06)."""
        chain = self._load_chain()
        found = False
        for entry in chain:
            is_match = entry.get("version_id") == version_id
            entry["promoted"] = is_match
            if is_match:
                found = True
        if found:
            self._save_chain(chain)
            logger.info("[pq] promoted version %s", version_id)
        return found

    def rollback(self) -> PlanVersion | None:
        """Re-promote the version immediately before the currently promoted one."""
        chain = self._load_chain()
        idx = next((i for i, e in enumerate(chain) if e.get("promoted")), -1)
        if idx <= 0:
            return None
        prev = chain[idx - 1]
        self.promote(prev["version_id"])
        return PlanVersion.from_dict(prev)

    def active_version(self) -> PlanVersion | None:
        """Return the currently promoted version, or None."""
        for e in self._load_chain():
            if e.get("promoted"):
                return PlanVersion.from_dict(e)
        return None

    def load_model(self, version_id: str) -> PlanQualityModel | None:
        """Load persisted weights for *version_id*."""
        m_path = self._path.parent / "plan_quality_models" / f"{version_id}.json"
        if not m_path.exists():
            return None
        try:
            data = json.loads(m_path.read_text(encoding="utf-8"))
            return PlanQualityModel.from_dict(data)
        except (json.JSONDecodeError, OSError):
            return None

    def list_versions(self) -> list[PlanVersion]:
        return [PlanVersion.from_dict(e) for e in self._load_chain()]
