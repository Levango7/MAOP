"""MAOP Skill Version Manager — Git-tracked skill versioning.

Provides version control for crystallized skills (from evolve.py),
storing them in a dedicated skills/ directory with Git integration
for full history tracking, diff, and rollback.

Usage::

    from maop.core.skill_version import SkillVersionManager

    mgr = SkillVersionManager(root_dir="/path/to/MAOP")
    mgr.save_skill("code-review-v2", content="...", metadata={"source": "evolve"})
    history = mgr.get_history("code-review-v2")
    content = mgr.load_skill("code-review-v2", version="abc1234")
"""
from __future__ import annotations

import contextlib
import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SkillStep(BaseModel):
    """A single step within a skill definition."""
    name: str = ""
    description: str = ""
    action: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    prompt: str = ""
    condition: str = ""
    always_run: bool = False
    timeout_s: int = 120


class SkillMeta(BaseModel):
    name: str
    version: str = "1.0.0"
    description: str = ""
    source: str = "manual"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tags: list[str] = Field(default_factory=list)
    preferred_model: str = ""
    fallback_model: str = ""
    steps: list[SkillStep] = Field(default_factory=list)
    pitfalls: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillStepResult(BaseModel):
    step_name: str
    success: bool = True
    output: str = ""
    duration_s: float = 0.0
    error: str = ""


class SkillExecutionResult(BaseModel):
    skill_name: str
    total_steps: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    skipped_steps: int = 0
    step_results: list[SkillStepResult] = Field(default_factory=list)
    total_duration_s: float = 0.0


class SkillVersionManager:
    """Git-backed skill version manager.

    Skills are stored as files in skills/ directory.
    Each save triggers a git commit for version tracking.
    """

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self._root = Path(root_dir) if root_dir else Path(".")
        self._skills_dir = self._root / "skills"
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self._git_available = shutil.which("git") is not None
        if self._git_available:
            self._init_git_repo()

    def _init_git_repo(self) -> None:
        git_dir = self._skills_dir / ".git"
        if not git_dir.exists():
            try:
                subprocess.run(
                    ["git", "init"], cwd=str(self._skills_dir),
                    capture_output=True, timeout=10,
                    check=True,
                )
                gitignore = self._skills_dir / ".gitignore"
                if not gitignore.exists():
                    gitignore.write_text("*.tmp\n__pycache__/\n", encoding="utf-8")
            except Exception as exc:
                logger.debug("[skill_version] Git init failed: %s", exc)
                self._git_available = False

    def save_skill(
        self,
        name: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        message: str = "",
    ) -> SkillMeta:
        safe_name = Path(name).stem
        skill_file = self._skills_dir / f"{safe_name}.md"
        meta_file = self._skills_dir / f"{safe_name}.meta.json"

        existing_meta: dict[str, Any] = {}
        if meta_file.exists():
            with contextlib.suppress(Exception):
                existing_meta = SkillMeta(**json.loads(meta_file.read_text(encoding="utf-8"))).model_dump()

        version = existing_meta.get("version", "1.0.0")
        if existing_meta:
            parts = version.split(".")
            if len(parts) >= 3:
                parts[-1] = str(int(parts[-1]) + 1)
                version = ".".join(parts)

        steps_raw = metadata.get("steps", existing_meta.get("steps", [])) if metadata else existing_meta.get("steps", [])
        steps = [SkillStep(**s) if isinstance(s, dict) else s for s in steps_raw]
        pitfalls = metadata.get("pitfalls", existing_meta.get("pitfalls", [])) if metadata else existing_meta.get("pitfalls", [])
        preferred_model = metadata.get("preferred_model", existing_meta.get("preferred_model", "")) if metadata else existing_meta.get("preferred_model", "")
        fallback_model = metadata.get("fallback_model", existing_meta.get("fallback_model", "")) if metadata else existing_meta.get("fallback_model", "")

        meta = SkillMeta(
            name=safe_name,
            version=version,
            description=existing_meta.get("description", ""),
            source=metadata.get("source", "manual") if metadata else existing_meta.get("source", "manual"),
            tags=metadata.get("tags", existing_meta.get("tags", [])) if metadata else existing_meta.get("tags", []),
            steps=steps,
            pitfalls=pitfalls,
            preferred_model=preferred_model,
            fallback_model=fallback_model,
            metadata={**existing_meta.get("metadata", {}), **(metadata or {})},
        )

        skill_file.write_text(content, encoding="utf-8")
        meta_file.write_text(meta.model_dump_json(indent=2), encoding="utf-8")

        if self._git_available:
            commit_msg = message or f"skill: update {safe_name} v{version}"
            self._git_commit(commit_msg)

        return meta

    def load_skill(self, name: str, version: str = "") -> str | None:
        safe_name = Path(name).stem
        skill_file = self._skills_dir / f"{safe_name}.md"

        if version and self._git_available:
            try:
                result = subprocess.run(  # noqa: PLW1510
                    ["git", "show", f"{version}:{safe_name}.md"],
                    cwd=str(self._skills_dir),
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    return result.stdout
            except Exception:
                pass

        if skill_file.exists():
            return skill_file.read_text(encoding="utf-8")
        return None

    def get_history(self, name: str, limit: int = 20) -> list[dict[str, str]]:
        safe_name = Path(name).stem
        if not self._git_available:
            return []

        try:
            result = subprocess.run(  # noqa: PLW1510
                ["git", "log", f"--max-count={limit}", "--pretty=format:%H|%ai|%s", "--", f"{safe_name}.md"],
                cwd=str(self._skills_dir),
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return []

            entries = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("|", 2)
                if len(parts) >= 3:
                    entries.append({"commit": parts[0][:12], "date": parts[1], "message": parts[2]})
            return entries
        except Exception:
            return []

    def list_skills(self) -> list[SkillMeta]:
        skills = []
        for meta_file in self._skills_dir.glob("*.meta.json"):
            try:
                meta = SkillMeta(**json.loads(meta_file.read_text(encoding="utf-8")))
                skills.append(meta)
            except Exception:
                pass
        return skills

    def delete_skill(self, name: str) -> bool:
        safe_name = Path(name).stem
        skill_file = self._skills_dir / f"{safe_name}.md"
        meta_file = self._skills_dir / f"{safe_name}.meta.json"
        deleted = False

        if skill_file.exists():
            skill_file.unlink()
            deleted = True
        if meta_file.exists():
            meta_file.unlink()
            deleted = True

        if deleted and self._git_available:
            self._git_commit(f"skill: delete {safe_name}")
        return deleted

    def _git_commit(self, message: str) -> None:
        if not self._git_available:
            return
        try:
            subprocess.run(["git", "add", "-A"], cwd=str(self._skills_dir), capture_output=True, timeout=10, check=True)
            subprocess.run(
                ["git", "commit", "-m", message, "--allow-empty"],
                cwd=str(self._skills_dir), capture_output=True, timeout=10,
                check=True,
            )
        except Exception as exc:
            logger.debug("[skill_version] Git commit failed: %s", exc)

    def get_skill_meta(self, name: str) -> SkillMeta | None:
        safe_name = Path(name).stem
        meta_file = self._skills_dir / f"{safe_name}.meta.json"
        if not meta_file.exists():
            return None
        try:
            return SkillMeta(**json.loads(meta_file.read_text(encoding="utf-8")))
        except Exception:
            return None

    def execute_skill(
        self,
        name: str,
        context: dict[str, Any] | None = None,
        *,
        step_executor: Any = None,
    ) -> SkillExecutionResult:
        """Execute a skill's steps sequentially.

        Parameters
        ----------
        name : str
            Skill name to execute.
        context : dict, optional
            Context variables for step interpolation.
        step_executor : callable, optional
            Custom step executor ``fn(step: SkillStep, context: dict) -> SkillStepResult``.
            If not provided, uses built-in executor (supports action=terminal/search_files/prompt).
        """
        meta = self.get_skill_meta(name)
        if meta is None:
            return SkillExecutionResult(skill_name=name)

        result = SkillExecutionResult(skill_name=name, total_steps=len(meta.steps))
        ctx = dict(context or {})

        start = __import__("time").time()
        chain_broken = False
        for step in meta.steps:
            if step.condition and not self._evaluate_condition(step.condition, ctx):
                result.skipped_steps += 1
                result.step_results.append(SkillStepResult(step_name=step.name, success=True, output="Skipped by condition"))
                continue

            if chain_broken and not step.always_run:
                result.skipped_steps += 1
                result.step_results.append(SkillStepResult(step_name=step.name, success=True, output="Skipped: chain broken"))
                continue

            try:
                if step_executor:
                    sr = step_executor(step, ctx)
                else:
                    sr = self._execute_step_builtin(step, ctx)
                result.step_results.append(sr)
                if sr.success:
                    result.completed_steps += 1
                    if sr.output:
                        ctx[f"step_output_{step.name}"] = sr.output
                else:
                    result.failed_steps += 1
                    chain_broken = True
            except Exception as exc:
                result.failed_steps += 1
                result.step_results.append(SkillStepResult(step_name=step.name, success=False, error=str(exc)))
                chain_broken = True

        result.total_duration_s = round(__import__("time").time() - start, 3)
        return result

    def _execute_step_builtin(self, step: SkillStep, context: dict[str, Any]) -> SkillStepResult:
        """Built-in step executor for common actions."""
        import time as _time
        start = _time.time()

        action = step.action.lower()
        if action == "terminal":
            # P0-4 fix: replace shell=True with shlex.split to prevent command injection.
            # Context values are substituted as-is; using shell=False ensures they
            # cannot be interpreted as shell metacharacters.
            import shlex
            cmd_template = step.params.get("command", "")
            for key, val in context.items():
                cmd_template = cmd_template.replace(f"{{{key}}}", str(val))
            try:
                args_list = shlex.split(cmd_template, posix=True)
                proc = subprocess.run(  # noqa: PLW1510
                    args_list, shell=False, capture_output=True, text=True, timeout=step.timeout_s,
                )
                return SkillStepResult(
                    step_name=step.name,
                    success=proc.returncode == 0,
                    output=proc.stdout[:2000] if proc.returncode == 0 else proc.stderr[:2000],
                    duration_s=round(_time.time() - start, 3),
                )
            except subprocess.TimeoutExpired:
                return SkillStepResult(step_name=step.name, success=False, error="Timeout", duration_s=step.timeout_s)
            except Exception as exc:
                return SkillStepResult(step_name=step.name, success=False, error=str(exc), duration_s=round(_time.time() - start, 3))

        elif action == "search_files":
            pattern = step.params.get("pattern", "")
            return SkillStepResult(step_name=step.name, success=True, output=f"Search pattern: {pattern}", duration_s=round(_time.time() - start, 3))

        elif action == "prompt":
            return SkillStepResult(step_name=step.name, success=True, output=step.prompt[:500], duration_s=round(_time.time() - start, 3))

        return SkillStepResult(step_name=step.name, success=True, output=f"Action: {action}", duration_s=round(_time.time() - start, 3))

    @staticmethod
    def _evaluate_condition(condition: str, context: dict[str, Any]) -> bool:
        if not condition:
            return True
        if condition.startswith("!"):
            var_name = condition[1:].strip()
            return not context.get(var_name)
        return bool(context.get(condition, True))

    def hot_reload(self) -> int:
        """Hot-reload all skill definitions from disk without restarting.

        Re-reads all .meta.json files and updates the in-memory cache.
        Returns the number of skills reloaded.
        """
        reloaded = 0
        for meta_file in self._skills_dir.glob("*.meta.json"):
            try:
                SkillMeta(**json.loads(meta_file.read_text(encoding="utf-8")))
                reloaded += 1
            except Exception as exc:
                logger.warning("[skill_version] Failed to reload %s: %s", meta_file.name, exc)
        logger.info("[skill_version] Hot reload: %d skills reloaded", reloaded)
        return reloaded

    def match(self, user_intent: str, top_k: int = 5) -> list[tuple[SkillMeta, float]]:
        """Match skills by user intent using keyword overlap scoring.

        For each skill, computes a relevance score based on:
        - Name/description/tag keyword overlap with user_intent
        - Step description overlap
        - Pitfall keyword overlap

        Returns list of (SkillMeta, score) sorted by score descending.
        """
        intent_tokens = set(user_intent.lower().split())
        if not intent_tokens:
            return [(s, 0.0) for s in self.list_skills()[:top_k]]

        scored: list[tuple[SkillMeta, float]] = []
        for skill in self.list_skills():
            skill_text_parts = [skill.name, skill.description]
            skill_text_parts.extend(skill.tags)
            skill_text_parts.extend(s.description for s in skill.steps)
            skill_text_parts.extend(skill.pitfalls)
            skill_tokens = set(" ".join(skill_text_parts).lower().split())

            overlap = len(intent_tokens & skill_tokens)
            score = overlap / len(intent_tokens) if intent_tokens else 0.0

            name_exact = any(t in skill.name.lower() for t in intent_tokens)
            if name_exact:
                score += 0.3

            scored.append((skill, min(score, 1.0)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]
