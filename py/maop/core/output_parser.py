"""MAOP Output Parser — Extract structured data from LLM responses.

Handles:
  - JSON extraction from freeform text (embedded JSON objects/arrays)
  - Markdown code block extraction (```json ... ```)
  - Function call result extraction
  - Pydantic model validation against expected schemas

Usage::

    from maop.core.output_parser import OutputParser

    parser = OutputParser()
    data = parser.extract_json('Here is the result: {"x": 1}')
    validated = parser.validate(data, MyPydanticModel)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ParseResult(BaseModel):
    success: bool = True
    data: Any = None
    format: str = ""
    error: str = ""


class OutputParser:
    """Extract and validate structured data from LLM text output."""

    def extract_json(self, text: str) -> ParseResult:
        """Extract the first valid JSON object or array from text.

        Tries in order:
          1. Markdown code block (```json ... ```)
          2. Raw text parse (the entire text is JSON)
          3. Embedded JSON (first { ... } or [ ... ] in text)
        """
        if not text or not text.strip():
            return ParseResult(success=False, error="Empty text")

        result = self._try_code_block(text)
        if result is not None:
            return result

        result = self._try_raw_json(text)
        if result is not None:
            return result

        result = self._try_embedded_json(text)
        if result is not None:
            return result

        return ParseResult(success=False, error="No JSON found in text")

    def _try_code_block(self, text: str) -> ParseResult | None:
        pattern = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)
        for m in pattern.finditer(text):
            content = m.group(1).strip()
            try:
                data = json.loads(content)
                return ParseResult(success=True, data=data, format="code_block")
            except (json.JSONDecodeError, ValueError):
                continue
        return None

    def _try_raw_json(self, text: str) -> ParseResult | None:
        stripped = text.strip()
        if stripped.startswith(("{", "[")):
            try:
                data = json.loads(stripped)
                return ParseResult(success=True, data=data, format="raw")
            except (json.JSONDecodeError, ValueError):
                pass
        return None

    def _try_embedded_json(self, text: str) -> ParseResult | None:
        for pattern in [
            re.compile(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', re.DOTALL),
            re.compile(r'\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]', re.DOTALL),
        ]:
            for m in pattern.finditer(text):
                candidate = m.group(0)
                try:
                    data = json.loads(candidate)
                    return ParseResult(success=True, data=data, format="embedded")
                except (json.JSONDecodeError, ValueError):
                    continue
        brace_result = self._try_balanced_braces(text)
        if brace_result is not None:
            return brace_result
        return None

    def _try_balanced_braces(self, text: str) -> ParseResult | None:
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start_idx = text.find(start_char)
            if start_idx == -1:
                continue
            depth = 0
            for i in range(start_idx, len(text)):
                if text[i] == start_char:
                    depth += 1
                elif text[i] == end_char:
                    depth -= 1
                if depth == 0:
                    candidate = text[start_idx:i + 1]
                    try:
                        data = json.loads(candidate)
                        return ParseResult(success=True, data=data, format="embedded")
                    except (json.JSONDecodeError, ValueError):
                        break
        return None

    def extract_code_blocks(self, text: str, language: str = "") -> list[str]:
        """Extract all code blocks, optionally filtered by language."""
        if language:
            pattern = re.compile(rf"```{language}\s*\n?(.*?)\n?\s*```", re.DOTALL)
        else:
            pattern = re.compile(r"```(?:\w+)?\s*\n?(.*?)\n?\s*```", re.DOTALL)
        return [m.group(1).strip() for m in pattern.finditer(text)]

    def extract_function_results(self, text: str) -> list[ParseResult]:
        """Extract function call results from text.

        Looks for patterns like:
          - ``<function_result>...</function_result>``
          - ``Function result: {...}``
          - JSON objects with ``success`` / ``output`` / ``error`` keys
        """
        results = []

        tag_pattern = re.compile(
            r"<function_result>(.*?)</function_result>", re.DOTALL,
        )
        for m in tag_pattern.finditer(text):
            content = m.group(1).strip()
            pr = self.extract_json(content)
            if pr.success:
                results.append(pr)
            else:
                results.append(ParseResult(success=True, data=content, format="function_tag"))

        fn_pattern = re.compile(r"Function result:\s*(\{.*?\})", re.DOTALL)
        for m in fn_pattern.finditer(text):
            try:
                data = json.loads(m.group(1))
                results.append(ParseResult(success=True, data=data, format="function_prefix"))
            except (json.JSONDecodeError, ValueError):
                pass

        return results

    def validate(self, data: Any, model: type[T]) -> tuple[T | None, str]:
        """Validate data against a Pydantic model.

        Returns (model_instance, error_message).  On success, error is "".
        """
        if data is None:
            return None, "No data to validate"
        try:
            if isinstance(data, dict):
                instance = model.model_validate(data)
            elif isinstance(data, str):
                parsed = json.loads(data)
                instance = model.model_validate(parsed)
            else:
                instance = model.model_validate(data)
            return instance, ""
        except ValidationError as exc:
            error_msg = str(exc)
            logger.debug("[output_parser] Validation failed: %s", error_msg)
            return None, error_msg
        except (json.JSONDecodeError, ValueError) as exc:
            return None, f"JSON parse error: {exc}"

    def extract_and_validate(self, text: str, model: type[T]) -> tuple[T | None, ParseResult]:
        """Convenience: extract JSON from text and validate against model."""
        pr = self.extract_json(text)
        if not pr.success:
            return None, pr
        instance, error = self.validate(pr.data, model)
        if error:
            pr.success = False
            pr.error = f"Validation error: {error}"
            return None, pr
        return instance, pr
