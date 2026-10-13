"""Build the B4 retrieval-quality fixture: 12 synthetic images + real CLIP vectors.

Why this exists
---------------
B4 measures cross-modal retrieval quality with NDCG, which needs a *labelled*
image set and real CLIP vectors for it. The vectors cannot be produced inside
CI (no torch there), so — following the B1 ``clip_gold.json`` precedent — they
are computed once on a machine that has the weights cached and **frozen into
the repository**. This script is that step.

What it does
------------
1. Generates 12 deterministic PNGs (4 colours x 3 shapes) with Pillow.
2. Embeds them (and the query texts) with the pinned CLIP revision.
3. Writes ``py/tests/fixtures/clip_eval.json`` (vectors + labels + revision).
4. Prints a sanity matrix (text x image cosine) so the grades can be checked
   against what the model actually discriminates — grades that do not match
   the model's ability make the metric unfalsifiable.

Usage::

    python scripts/build_clip_eval_fixture.py            # regenerate
    python scripts/build_clip_eval_fixture.py --check    # verify only (no torch)

``--check`` validates the existing fixture (hashes, dimension, revision,
label integrity) without importing torch, so it is safe on CI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = REPO / "py"
FIXTURE_DIR = PY / "tests" / "fixtures" / "multimodal_eval"
FIXTURE_JSON = PY / "tests" / "fixtures" / "clip_eval.json"

# Deterministic corpus: 4 colours x 3 shapes. Shape names double as the
# label vocabulary; one distractor-free, low-entropy design keeps the PNG
# bytes stable across Pillow patch versions (the image keys are byte hashes).
COLOURS = ("red", "blue", "green", "yellow")
SHAPES = ("circle", "square", "triangle")

# Query texts. ``exact`` queries name one colour+shape; ``paraphrase`` queries
# reword two of them (held-out variants — they are NOT indexed as entries, so
# no provider can self-hit on an identical string).
QUERIES: dict[str, dict] = {
    "a red circle": {"colour": "red", "shape": "circle", "kind": "exact"},
    "a blue square": {"colour": "blue", "shape": "square", "kind": "exact"},
    "a green triangle": {"colour": "green", "shape": "triangle", "kind": "exact"},
    "a yellow circle": {"colour": "yellow", "shape": "circle", "kind": "exact"},
    "the shape that is red and round": {
        "colour": "red", "shape": "circle", "kind": "paraphrase",
    },
    "a photo of a blue square": {
        "colour": "blue", "shape": "square", "kind": "paraphrase",
    },
    "something yellow with three sides": {
        "colour": "yellow", "shape": "triangle", "kind": "paraphrase",
    },
    "a round thing coloured green": {
        "colour": "green", "shape": "circle", "kind": "paraphrase",
    },
}

GRADE_EXACT = 2      # right colour and right shape
GRADE_PARTIAL = 1    # right colour, wrong shape (colour is the stronger cue)


def _image_name(colour: str, shape: str) -> str:
    return f"{colour}_{shape}.png"


def _labels_for(query: str) -> dict[str, int]:
    """Graded relevance: 2 = exact, 1 = same colour other shape, 0 = rest."""
    spec = QUERIES[query]
    labels: dict[str, int] = {}
    for colour in COLOURS:
        for shape in SHAPES:
            name = _image_name(colour, shape)
            if colour == spec["colour"] and shape == spec["shape"]:
                labels[name] = GRADE_EXACT
            elif colour == spec["colour"]:
                labels[name] = GRADE_PARTIAL
            else:
                labels[name] = 0
    return labels


def generate_images() -> list[tuple[str, Path, str]]:
    """Render the corpus; returns (name, path, sha256-of-bytes) triples."""
    from PIL import Image, ImageDraw

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    rgb = {
        "red": (220, 40, 40),
        "blue": (40, 80, 220),
        "green": (40, 170, 90),
        "yellow": (230, 210, 60),
    }
    out: list[tuple[str, Path, str]] = []
    for colour in COLOURS:
        for shape in SHAPES:
            img = Image.new("RGB", (224, 224), (255, 255, 255))
            draw = ImageDraw.Draw(img)
            box = [28, 28, 196, 196]
            if shape == "circle":
                draw.ellipse(box, fill=rgb[colour])
            elif shape == "square":
                draw.rectangle(box, fill=rgb[colour])
            else:  # triangle
                draw.polygon([(112, 28), (28, 196), (196, 196)], fill=rgb[colour])
            path = FIXTURE_DIR / _image_name(colour, shape)
            img.save(path, format="PNG", optimize=False)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            out.append((path.name, path, digest))
    return out


def embed_all(image_paths: list[Path]) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
    """Real CLIP vectors for the images and the query texts."""
    sys.path.insert(0, str(PY))
    from maop.core.memory.multimodal_embed import (
        DEFAULT_CLIP_REVISION,
        ClipEmbedding,
    )

    provider = ClipEmbedding(revision=DEFAULT_CLIP_REVISION)
    images = {
        hashlib.sha256(p.read_bytes()).hexdigest(): provider.embed_image(p)
        for p in image_paths
    }
    texts = {q: provider.embed(q) for q in QUERIES}
    return texts, images


def write_fixture(
    images: list[tuple[str, Path, str]], texts: dict, image_vecs: dict
) -> None:
    payload = {
        "model_id": "openai/clip-vit-base-patch32",
        "revision": _pinned_revision(),
        "dimension": len(next(iter(image_vecs.values()))),
        "pillow_version": _pillow_version(),
        "generator": "scripts/build_clip_eval_fixture.py",
        # id -> {label, colours/shape metadata} so the harness can grade.
        "corpus": [
            {
                "id": name,
                "colour": name.split("_")[0],
                "shape": name.split("_")[1].removesuffix(".png"),
                "sha256": digest,
            }
            for name, _path, digest in images
        ],
        "queries": [
            {
                "text": q,
                "kind": spec["kind"],
                "labels": _labels_for(q),
            }
            for q, spec in QUERIES.items()
        ],
        "texts": texts,
        "images": image_vecs,
    }
    FIXTURE_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _pinned_revision() -> str:
    sys.path.insert(0, str(PY))
    from maop.core.memory.multimodal_embed import DEFAULT_CLIP_REVISION

    return DEFAULT_CLIP_REVISION


def _pillow_version() -> str:
    try:
        import PIL

        return PIL.__version__
    except Exception:  # pragma: no cover - Pillow missing on --check hosts
        return "unknown"


def sanity_matrix(payload: dict) -> str:
    """text x image cosine matrix — lets a reviewer see what the model separates."""
    import math

    def cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    id_by_hash = {c["sha256"]: c["id"] for c in payload["corpus"]}
    names = [id_by_hash[h] for h in payload["images"]]
    lines = ["| query | " + " | ".join(n.replace(".png", "") for n in names) + " |",
             "|" + "---|" * (len(names) + 1)]
    for q in payload["queries"]:
        vec = payload["texts"][q["text"]]
        scores = [cosine(vec, payload["images"][h]) for h in payload["images"]]
        best = max(range(len(scores)), key=lambda i: scores[i])
        cells = [
            f"**{s:.3f}**" if i == best else f"{s:.3f}" for i, s in enumerate(scores)
        ]
        lines.append(f"| {q['text']} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def check_fixture() -> int:
    """Validate the committed fixture without torch (CI-safe)."""
    if not FIXTURE_JSON.exists():
        print(f"FAIL: {FIXTURE_JSON} missing — run the builder")
        return 1
    payload = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    problems: list[str] = []
    if payload.get("revision") != _pinned_revision():
        problems.append("revision drifted from DEFAULT_CLIP_REVISION")
    dim = payload.get("dimension")
    if dim != 512:
        problems.append(f"dimension {dim} != 512")
    hashes = {c["sha256"] for c in payload["corpus"]}
    if hashes != set(payload["images"]):
        problems.append("image vector keys do not match the corpus hashes")
    for q in payload["queries"]:
        grades = [g for g in q["labels"].values() if g >= GRADE_EXACT]
        if not grades:
            problems.append(f"query {q['text']!r} has no grade>=2 label")
        if q["text"] not in payload["texts"]:
            problems.append(f"query {q['text']!r} has no vector")
    for name, path, digest in [
        (n, FIXTURE_DIR / n, None) for n in (c["id"] for c in payload["corpus"])
    ]:
        if not path.exists():
            problems.append(f"image {name} missing on disk")
        elif hashlib.sha256(path.read_bytes()).hexdigest() not in hashes:
            problems.append(f"image {name} bytes drifted from the fixture hash")
    if problems:
        print("FAIL: " + "; ".join(problems))
        return 1
    print(f"OK: fixture valid ({len(payload['corpus'])} images, "
          f"{len(payload['queries'])} queries, dim={dim}, "
          f"revision={payload['revision'][:12]}…)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify only (no torch)")
    args = parser.parse_args()
    if args.check:
        return check_fixture()

    images = generate_images()
    texts, image_vecs = embed_all([p for _n, p, _d in images])
    write_fixture(images, texts, image_vecs)
    payload = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    print(f"wrote {FIXTURE_JSON} ({len(images)} images, {len(texts)} queries)")
    print(f"pillow={payload['pillow_version']} revision={payload['revision'][:12]}…")
    print()
    print(sanity_matrix(payload))
    print()
    print("CHECKLIST before committing a regenerated fixture:")
    print("  1. sanity matrix agrees with the grades (exact-match cell is bold)")
    print("  2. re-measure the NDCG discrimination thresholds in the tests")
    print("  3. record the new numbers in docs/audits/m22-b4-retrieval-quality.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
