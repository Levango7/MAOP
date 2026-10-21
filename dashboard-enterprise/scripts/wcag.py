#!/usr/bin/env python3
"""WCAG 2.1 contrast gate for design tokens.

Vendored from the design-md skill (scripts/wcag.py) and extended for this repo:
  * --tokens is repeatable; tokens.css contributes dark tokens, themes.css
    contributes the [data-theme="light"] overrides (merged over dark).
  * --pairs-only skips the skill's naming-convention auto pairs. Our token
    names differ on purpose (--brand-text is the brand-as-text token,
    --border-control is the interactive boundary; see DESIGN.md).
  * explicit pairs whose token is missing count as FAIL, not SKIP, so deleting
    or renaming a token cannot silently shrink the gate.
  * --min-pairs N fails the run when fewer than N pairs were evaluated.
  * --self-test proves the colour math and parser are alive.

Exit codes: 0 all pass, 1 contrast failure, 2 gate could not run.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------- color math


def hex_to_rgb(value: str):
    s = value.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"not a #RRGGBB color: {value!r}")
    return tuple(int(s[i : i + 2], 16) for i in (0, 2, 4))


def relative_luminance(rgb) -> float:
    def chan(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (chan(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(rgb1, rgb2) -> float:
    l1, l2 = relative_luminance(rgb1), relative_luminance(rgb2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------- parsers

CSS_HEADER_RE = re.compile(r"(?::root)?\[data-theme=['\"]light['\"]\]")
CSS_COLOR_RE = re.compile(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;")
CSS_RGBA_RE = re.compile(
    r"--([a-z0-9-]+)\s*:\s*rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)"
)
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)


def parse_css(text: str) -> dict:
    """Return {"dark": {...}, "light": {...}} from a tokens.css/themes.css.

    Opaque tokens map to "#rrggbb"; translucent tokens (soft backgrounds) map
    to {"rgba": (r, g, b), "alpha": a} so they can be composited explicitly.
    """
    # Comments mention [data-theme="light"] and carry commented-out tokens;
    # strip them before splitting or they create phantom themes/tokens.
    text = CSS_COMMENT_RE.sub("", text)
    m = CSS_HEADER_RE.search(text)
    dark_part = text[: m.start()] if m else text
    light_part = text[m.start() :] if m else ""

    def collect(block: str) -> dict:
        out = {k: v.lower() for k, v in CSS_COLOR_RE.findall(block)}
        for name, r, g, b, a in CSS_RGBA_RE.findall(block):
            out[name] = {"rgba": (int(r), int(g), int(b)), "alpha": float(a)}
        return out

    out = {}
    dark = collect(dark_part)
    light = collect(light_part)
    if dark:
        out["dark"] = dark
    if light:
        out["light"] = light
    return out


def parse_json_tokens(text: str) -> dict:
    data = json.loads(text)
    themes: dict[str, dict[str, str]] = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        tokens = {}
        for name, spec in value.items():
            if isinstance(spec, dict) and isinstance(spec.get("value"), str):
                v = spec["value"].strip()
                if re.fullmatch(r"#[0-9a-fA-F]{6}", v):
                    tokens[name] = v.lower()
        if tokens:
            theme = "light" if "light" in key.lower() else "dark"
            themes.setdefault(theme, {}).update(tokens)
    return themes


def load_themes(paths: list[Path]) -> dict:
    themes: dict[str, dict[str, str]] = {}
    for path in paths:
        text = path.read_text(encoding="utf-8")
        parsed = parse_css(text) if path.suffix.lower() == ".css" else parse_json_tokens(text)
        for theme, tokens in parsed.items():
            themes.setdefault(theme, {}).update(tokens)
    # CSS cascade semantics: light inherits every token it does not override.
    if themes.get("light") and themes.get("dark"):
        themes["light"] = {**themes["dark"], **themes["light"]}
    return {k: v for k, v in themes.items() if v}


# ---------------------------------------------------------------- checking


def blend(fg_rgb, alpha: float, bg_rgb):
    """Composite fg at `alpha` over bg (like rgba over a solid background)."""
    return tuple(fg_rgb[i] * alpha + bg_rgb[i] * (1 - alpha) for i in range(3))


def resolve(token: str, colors: dict):
    """Resolve "tok" (opaque) or "soft:base" (translucent over base) to rgb."""
    if ":" in token:
        name, base = token.split(":", 1)
        spec = colors[name]
        if not isinstance(spec, dict) or "rgba" not in spec:
            raise KeyError(f"{name} is not a translucent token")
        return blend(spec["rgba"], spec["alpha"], resolve(base, colors))
    spec = colors[token]
    if isinstance(spec, dict):
        raise KeyError(f"{token} is translucent; use {token}:<base>")
    return hex_to_rgb(spec)


def check_theme(theme: str, colors: dict, pairs, out_lines: list) -> tuple[int, int, int]:
    passed = failed = checked = 0
    out_lines.append(f"\n== theme: {theme} ==")
    out_lines.append(f"{'foreground':>18} {'background':>14} {'ratio':>6} {'min':>4}  result  use")
    for fg_tok, bg_tok, minimum, use in pairs:
        try:
            fg_rgb, bg_rgb = resolve(fg_tok, colors), resolve(bg_tok, colors)
        except KeyError as e:
            # An explicit pair that cannot resolve means the contract itself broke.
            failed += 1
            out_lines.append(f"{fg_tok:>18} {bg_tok:>14} {'—':>6} {'—':>4}  FAIL    missing token: {e}")
            continue
        ratio = contrast_ratio(fg_rgb, bg_rgb)
        ok = ratio >= minimum - 1e-9
        passed += ok
        failed += (not ok)
        checked += 1
        marker = "PASS" if ok else "FAIL"
        out_lines.append(f"{fg_tok:>18} {bg_tok:>14} {ratio:>6.2f} {minimum:>4.1f}  {marker}    {use}")
    out_lines.append(f"-- {theme}: {passed} pass / {failed} fail / {checked} checked")
    return passed, failed, checked


def self_test() -> int:
    assert abs(contrast_ratio(hex_to_rgb("#ffffff"), hex_to_rgb("#000000")) - 21.0) < 0.01
    assert abs(contrast_ratio(hex_to_rgb("#ffffff"), hex_to_rgb("#ffffff")) - 1.0) < 0.001
    assert contrast_ratio(hex_to_rgb("#777777"), hex_to_rgb("#ffffff")) < 4.5
    synth = (
        ":root { --bg: #000000; --text: #ffffff; }\n"
        "[data-theme=\"light\"] { --bg: #ffffff; --text: #000000; }\n"
    )
    parsed = parse_css(synth)
    assert parsed["dark"]["text"] == "#ffffff", parsed
    assert parsed["light"]["bg"] == "#ffffff", parsed
    # Translucent soft tokens must composite over their base before measuring.
    synth_soft = ":root { --bg: #ffffff; --soft: rgba(0, 0, 0, 0.5); }"
    soft_colors = parse_css(synth_soft)["dark"]
    assert resolve("soft:bg", soft_colors) == (127.5, 127.5, 127.5), soft_colors
    # A real failing pair must be reported as FAIL, and a missing token must
    # fail too (never a silent SKIP that shrinks the gate).
    black = {"bg": "#000000", "surface": "#000000", "text-faint": "#333333"}
    lines: list[str] = []
    _, failed, checked = check_theme("synthetic-low", black, [("text-faint", "bg", 4.5, "pairs-file")], lines)
    assert (checked, failed) == (1, 1), lines
    _, failed_missing, _ = check_theme("synthetic-missing", black, [("nope", "bg", 4.5, "pairs-file")], lines)
    assert failed_missing == 1, lines
    print("self-test: OK (math + parser + failing/missing pair detection)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="WCAG 2.1 contrast gate for design tokens")
    ap.add_argument("--tokens", type=Path, action="append", default=[], help="tokens.css / themes.css / design-tokens.json (repeatable)")
    ap.add_argument("--pair", action="append", default=[], help='FG:BG[:MIN] e.g. "#E6E9EF:#0A0C10:4.5"')
    ap.add_argument("--pairs", type=Path, help="pairs file: each line 'FG BG [MIN]', '//' comment")
    ap.add_argument("--pairs-only", action="store_true", help="skip naming-convention auto pairs")
    ap.add_argument("--min-pairs", type=int, default=0, help="fail if fewer than N pairs were evaluated")
    ap.add_argument("--theme", help="only check this theme (dark/light)")
    ap.add_argument("--json-out", type=Path, help="write machine-readable report")
    ap.add_argument("--self-test", action="store_true", help="verify math/parser and exit")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    themes = load_themes(args.tokens)
    if args.theme:
        themes = {k: v for k, v in themes.items() if k == args.theme}

    explicit_raw = []
    for spec in args.pair:
        parts = spec.split(":")
        fg, bg = parts[0], parts[1]
        minimum = float(parts[2]) if len(parts) > 2 else 4.5
        explicit_raw.append((fg, bg, minimum, "explicit"))
    if args.pairs:
        for line in args.pairs.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            parts = line.split()
            fg, bg = parts[0], parts[1]
            minimum = float(parts[2]) if len(parts) > 2 else 4.5
            explicit_raw.append((fg, bg, minimum, "pairs-file"))

    if not explicit_raw:
        print("no pairs supplied — refusing to report a vacuous pass", file=sys.stderr)
        return 2
    if args.pairs_only and not themes:
        print("no themes/tokens parsed — check --tokens", file=sys.stderr)
        return 2

    out_lines = []
    report = {"themes": {}, "explicit": [], "pairs": len(explicit_raw)}
    total_fail = 0
    total_checked = 0

    if themes:
        for theme, colors in themes.items():
            p, f, c = check_theme(theme, colors, explicit_raw, out_lines)
            report["themes"][theme] = {"pass": p, "fail": f, "checked": c}
            total_fail += f
            total_checked += c
    else:
        for fg, bg, minimum, use in explicit_raw:
            try:
                ratio = contrast_ratio(hex_to_rgb(fg), hex_to_rgb(bg))
            except ValueError as e:
                print(f"skip {fg}:{bg} ({e})", file=sys.stderr)
                continue
            ok = ratio >= minimum - 1e-9
            total_fail += (not ok)
            total_checked += 1
            out_lines.append(f"{fg} on {bg}: {ratio:.2f} (min {minimum}) -> {'PASS' if ok else 'FAIL'}")
            report["explicit"].append({"fg": fg, "bg": bg, "ratio": round(ratio, 3), "min": minimum, "pass": ok})

    print("\n".join(out_lines))
    print(f"\nTOTAL: {total_checked} pairs checked, {'ALL PASS' if total_fail == 0 else str(total_fail) + ' FAIL'}")

    if args.json_out:
        report.update({"total_fail": total_fail, "total_checked": total_checked})
        args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"report written: {args.json_out}")

    if args.min_pairs and total_checked < args.min_pairs:
        print(f"GATE ERROR: only {total_checked} pairs checked, expected >= {args.min_pairs}", file=sys.stderr)
        return 2
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
