#!/usr/bin/env python3
"""Bake foreground-color recovery variants for chroma-keyed frames.

The purple outline is partly an alpha problem and partly a foreground-color
contamination problem. This script keeps the existing chroma-key alpha, then
tries two foreground recovery paths:

1. Analytic unmixing against the known magenta key color.
2. PyMatting's multilevel foreground estimation.

It also writes a hybrid that combines PyMatting foreground recovery with the
previous outline-alpha frame set.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

try:
    from pymatting import estimate_foreground_ml
except ImportError as exc:  # pragma: no cover - depends on local env
    raise SystemExit("Missing pymatting. Run with .venv-ml/bin/python or install pymatting first.") from exc


PNG_GLOB = "frame_*.png"
ROOT = Path("outputs/chroma-key-research/ffmpeg_chromakey_024_010")
RAW_DIR = Path("outputs/postprocess/character-animation-with-subtle-202606112011/frames_raw")
CHROMA_DIR = ROOT / "frames_transparent"
OUTLINE_DIR = ROOT / "frames_outline_alpha"

METHODS = {
    "unmix": {
        "label": "Formula Unmix",
        "dir": ROOT / "frames_unmix_foreground",
        "alpha_dir": CHROMA_DIR,
        "kind": "unmix",
    },
    "pymatting": {
        "label": "PyMatting FG",
        "dir": ROOT / "frames_pymatting_foreground",
        "alpha_dir": CHROMA_DIR,
        "kind": "pymatting",
    },
    "pymatting_outline": {
        "label": "PyMatting + Outline Alpha",
        "dir": ROOT / "frames_pymatting_outline_hybrid",
        "alpha_dir": OUTLINE_DIR,
        "kind": "pymatting",
    },
}


def main() -> int:
    args = parse_args()
    root = resolve_path(args.root)
    raw_dir = resolve_path(args.raw_dir)
    chroma_dir = resolve_path(args.chroma_dir)
    outline_dir = resolve_path(args.outline_dir)

    methods = select_methods(args.methods, root, chroma_dir, outline_dir)
    raw_frames = sorted(raw_dir.glob(PNG_GLOB))
    if args.max_frames:
        raw_frames = raw_frames[: args.max_frames]
    if not raw_frames:
        raise SystemExit(f"No raw frames found under {raw_dir}")

    for method in methods.values():
        method["dir"].mkdir(parents=True, exist_ok=True)
        for old_frame in method["dir"].glob(PNG_GLOB):
            old_frame.unlink()

    key_rgb = np.array(parse_hex_rgb(args.key_color), dtype=np.float32) / 255.0
    frame_stats = {name: [] for name in methods}

    for index, raw_path in enumerate(raw_frames):
        raw = load_rgb(raw_path)
        chroma_rgba = load_rgba(chroma_dir / raw_path.name)
        chroma_alpha = chroma_rgba[:, :, 3]
        pymatting_cache: dict[str, np.ndarray] = {}

        for name, method in methods.items():
            alpha_rgba = load_rgba(method["alpha_dir"] / raw_path.name)
            alpha = alpha_rgba[:, :, 3]
            base_rgb = chroma_rgba[:, :, :3]
            if method["kind"] == "unmix":
                recovered, stats = recover_formula_unmix(raw, base_rgb, alpha, key_rgb, args)
            else:
                cache_key = str(method["alpha_dir"])
                if cache_key not in pymatting_cache:
                    pymatting_cache[cache_key] = estimate_foreground_ml(
                        raw,
                        alpha,
                        regularization=args.pymatting_regularization,
                        n_small_iterations=args.pymatting_small_iterations,
                        n_big_iterations=args.pymatting_big_iterations,
                        small_size=args.pymatting_small_size,
                        gradient_weight=args.pymatting_gradient_weight,
                    )
                recovered, stats = recover_from_estimated_foreground(
                    pymatting_cache[cache_key], base_rgb, alpha, key_rgb, args
                )

            out = np.dstack([np.clip(recovered * 255.0, 0, 255), np.clip(alpha * 255.0, 0, 255)])
            Image.fromarray(out.astype(np.uint8), mode="RGBA").save(method["dir"] / raw_path.name)
            frame_stats[name].append(stats)

        if index == 0 or (index + 1) % 25 == 0 or index == len(raw_frames) - 1:
            print(f"Processed {index + 1}/{len(raw_frames)}")

    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "raw_dir": str(raw_dir),
        "chroma_dir": str(chroma_dir),
        "outline_dir": str(outline_dir),
        "frame_count": len(raw_frames),
        "key_rgb": key_rgb.tolist(),
        "methods": {
            name: {
                "label": method["label"],
                "dir": str(method["dir"]),
                "alpha_dir": str(method["alpha_dir"]),
                "kind": method["kind"],
                "stats": summarize_stats(frame_stats[name]),
            }
            for name, method in methods.items()
        },
        "parameters": vars(args),
    }
    metadata_path = root / "foreground-recovery-meta.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    comparison_path = root / "comparison_foreground_recovery.jpg"
    make_comparison_sheet(root, methods, comparison_path, args.compare_frame)
    contact_path = root / "contact_sheet_foreground_recovery.jpg"
    make_contact_sheet(root, methods, contact_path)

    print(f"Metadata: {metadata_path}")
    print(f"Comparison: {comparison_path}")
    print(f"Contact sheet: {contact_path}")
    for method in methods.values():
        print(f"{method['label']}: {method['dir']}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate foreground recovery comparison frame sets.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--raw-dir", default=str(RAW_DIR))
    parser.add_argument("--chroma-dir", default=str(CHROMA_DIR))
    parser.add_argument("--outline-dir", default=str(OUTLINE_DIR))
    parser.add_argument("--methods", default="unmix,pymatting,pymatting_outline")
    parser.add_argument("--key-color", default="ff00ff")
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--edge-radius", type=int, default=4)
    parser.add_argument("--alpha-floor", type=float, default=0.08)
    parser.add_argument("--spill-margin", type=float, default=0.035)
    parser.add_argument("--spill-scale", type=float, default=0.18)
    parser.add_argument("--semi-threshold", type=float, default=0.985)
    parser.add_argument("--unmix-amount", type=float, default=0.82)
    parser.add_argument("--pymatting-amount", type=float, default=0.88)
    parser.add_argument("--despill-amount", type=float, default=0.52)
    parser.add_argument("--neutralize-amount", type=float, default=0.08)
    parser.add_argument("--pymatting-regularization", type=float, default=1e-5)
    parser.add_argument("--pymatting-small-iterations", type=int, default=6)
    parser.add_argument("--pymatting-big-iterations", type=int, default=1)
    parser.add_argument("--pymatting-small-size", type=int, default=64)
    parser.add_argument("--pymatting-gradient-weight", type=float, default=1.0)
    parser.add_argument("--compare-frame", type=int, default=96)
    return parser.parse_args()


def select_methods(
    value: str,
    root: Path,
    chroma_dir: Path,
    outline_dir: Path,
) -> dict[str, dict]:
    selected: dict[str, dict] = {}
    for name in [item.strip() for item in value.split(",") if item.strip()]:
        if name not in METHODS:
            raise SystemExit(f"Unknown method {name!r}. Choices: {', '.join(METHODS)}")
        method = dict(METHODS[name])
        method["dir"] = root / method["dir"].name
        if name == "pymatting_outline":
            method["alpha_dir"] = outline_dir
        else:
            method["alpha_dir"] = chroma_dir
        selected[name] = method
    return selected


def recover_formula_unmix(
    raw: np.ndarray,
    base_rgb: np.ndarray,
    alpha: np.ndarray,
    key_rgb: np.ndarray,
    args: argparse.Namespace,
) -> tuple[np.ndarray, dict]:
    safe_alpha = np.maximum(alpha, args.alpha_floor)
    unmixed = (raw - (1.0 - alpha[:, :, None]) * key_rgb) / safe_alpha[:, :, None]
    unmixed = np.clip(unmixed, 0.0, 1.0)
    edge_weight, stats = recovery_weight(base_rgb, alpha, args, amount=args.unmix_amount)
    recovered = blend(base_rgb, unmixed, edge_weight)
    recovered = despill_and_neutralize(recovered, alpha, edge_weight, args)
    return recovered, stats


def recover_from_estimated_foreground(
    foreground: np.ndarray,
    base_rgb: np.ndarray,
    alpha: np.ndarray,
    key_rgb: np.ndarray,
    args: argparse.Namespace,
) -> tuple[np.ndarray, dict]:
    del key_rgb
    foreground = np.clip(foreground, 0.0, 1.0)
    edge_weight, stats = recovery_weight(base_rgb, alpha, args, amount=args.pymatting_amount)
    recovered = blend(base_rgb, foreground, edge_weight)
    recovered = despill_and_neutralize(recovered, alpha, edge_weight, args)
    return recovered, stats


def recovery_weight(
    base_rgb: np.ndarray,
    alpha: np.ndarray,
    args: argparse.Namespace,
    amount: float,
) -> tuple[np.ndarray, dict]:
    foreground = alpha > 0.005
    edge = outline_near_transparency(foreground, args.edge_radius)
    semi = foreground & (alpha < args.semi_threshold)

    red = base_rgb[:, :, 0]
    green = base_rgb[:, :, 1]
    blue = base_rgb[:, :, 2]
    spill = np.clip((np.minimum(red, blue) - green - args.spill_margin) / args.spill_scale, 0.0, 1.0)
    spill_edge = edge & (spill > 0)

    semi_weight = np.where(semi, np.clip((args.semi_threshold - alpha) / args.semi_threshold, 0.0, 1.0), 0.0)
    edge_weight = np.where(edge, np.maximum(semi_weight, spill * 0.9), semi_weight * 0.65)
    edge_weight *= clamp01(amount)
    edge_weight = np.where(foreground, edge_weight, 0.0)

    stats = {
        "edge_pixels": int(np.count_nonzero(edge)),
        "semi_pixels": int(np.count_nonzero(semi)),
        "spill_edge_pixels": int(np.count_nonzero(spill_edge)),
        "weighted_pixels": int(np.count_nonzero(edge_weight > 0.01)),
    }
    return edge_weight[:, :, None], stats


def despill_and_neutralize(
    rgb: np.ndarray,
    alpha: np.ndarray,
    weight: np.ndarray,
    args: argparse.Namespace,
) -> np.ndarray:
    out = rgb.copy()
    weighted = weight[:, :, 0] > 0.01
    if not np.any(weighted):
        return out

    red = out[:, :, 0]
    green = out[:, :, 1]
    blue = out[:, :, 2]
    spill = np.maximum(0.0, np.minimum(red, blue) - green - args.spill_margin)
    subtract = spill * clamp01(args.despill_amount) * weight[:, :, 0]
    out[:, :, 0] = np.where(weighted, np.maximum(green, red - subtract), red)
    out[:, :, 2] = np.where(weighted, np.maximum(green, blue - subtract), blue)

    if args.neutralize_amount > 0:
        amount = clamp01(args.neutralize_amount)
        luma = out[:, :, 0] * 0.299 + out[:, :, 1] * 0.587 + out[:, :, 2] * 0.114
        neutral_weight = weight * amount
        out = out * (1.0 - neutral_weight) + luma[:, :, None] * neutral_weight

    out = np.where((alpha > 0.005)[:, :, None], out, rgb)
    return np.clip(out, 0.0, 1.0)


def outline_near_transparency(foreground: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return foreground
    transparent = ~foreground
    source = Image.fromarray(transparent.astype(np.uint8) * 255, mode="L")
    dilated_transparent = np.asarray(source.filter(ImageFilter.MaxFilter(radius * 2 + 1))) > 0
    return foreground & dilated_transparent


def blend(base: np.ndarray, recovered: np.ndarray, weight: np.ndarray) -> np.ndarray:
    return base * (1.0 - weight) + recovered * weight


def make_comparison_sheet(root: Path, methods: dict[str, dict], out_path: Path, frame_no: int) -> None:
    rows = [
        ("Chromakey", root / "frames_transparent"),
        ("Outline Alpha", root / "frames_outline_alpha"),
    ] + [(method["label"], method["dir"]) for method in methods.values()]
    backgrounds = [
        ("dark", (24, 25, 28)),
        ("green", (38, 130, 84)),
        ("sky", (95, 146, 196)),
        ("red", (150, 49, 55)),
    ]
    thumb_w, thumb_h = 320, 180
    label_h = 28
    sheet = Image.new("RGB", (len(backgrounds) * thumb_w, len(rows) * (thumb_h + label_h)), (18, 19, 22))
    draw = ImageDraw.Draw(sheet)
    for row_index, (label, folder) in enumerate(rows):
        y = row_index * (thumb_h + label_h)
        frame_path = folder / f"frame_{frame_no:04d}.png"
        if not frame_path.exists():
            continue
        rgba = Image.open(frame_path).convert("RGBA")
        for column_index, (bg_label, color) in enumerate(backgrounds):
            x = column_index * thumb_w
            bg = Image.new("RGBA", rgba.size, color + (255,))
            bg.alpha_composite(rgba)
            thumb = bg.convert("RGB").resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
            sheet.paste(thumb, (x, y + label_h))
            draw.text((x + 8, y + 7), f"{label} / {bg_label}", fill=(235, 235, 235))
    sheet.save(out_path, quality=92)


def make_contact_sheet(root: Path, methods: dict[str, dict], out_path: Path) -> None:
    columns = [
        ("Chromakey", root / "frames_transparent"),
        ("Outline", root / "frames_outline_alpha"),
    ] + [(method["label"], method["dir"]) for method in methods.values()]
    sample_frames = [0, 48, 96, 144, 191]
    thumb_w, thumb_h = 256, 144
    label_h = 24
    sheet = Image.new("RGB", (len(columns) * thumb_w, len(sample_frames) * (thumb_h + label_h)), (18, 19, 22))
    draw = ImageDraw.Draw(sheet)
    for row_index, frame_no in enumerate(sample_frames):
        y = row_index * (thumb_h + label_h)
        for column_index, (label, folder) in enumerate(columns):
            x = column_index * thumb_w
            frame_path = folder / f"frame_{frame_no:04d}.png"
            if not frame_path.exists():
                continue
            rgba = Image.open(frame_path).convert("RGBA")
            bg = checkerboard(rgba.size)
            bg.alpha_composite(rgba)
            thumb = ImageOps.contain(bg.convert("RGB"), (thumb_w, thumb_h), method=Image.Resampling.LANCZOS)
            sheet.paste(thumb, (x, y + label_h))
            draw.text((x + 7, y + 6), f"{label} f{frame_no:04d}", fill=(235, 235, 235))
    sheet.save(out_path, quality=92)


def checkerboard(size: tuple[int, int]) -> Image.Image:
    width, height = size
    tile = 24
    y_indices, x_indices = np.indices((height, width))
    pattern = ((x_indices // tile + y_indices // tile) % 2).astype(np.uint8)
    light = np.array([72, 78, 86, 255], dtype=np.uint8)
    dark = np.array([38, 43, 49, 255], dtype=np.uint8)
    pixels = np.where(pattern[:, :, None] == 1, light, dark)
    return Image.fromarray(pixels, mode="RGBA")


def load_rgb(path: Path) -> np.ndarray:
    if not path.exists():
        raise SystemExit(f"Missing frame: {path}")
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0


def load_rgba(path: Path) -> np.ndarray:
    if not path.exists():
        raise SystemExit(f"Missing frame: {path}")
    return np.asarray(Image.open(path).convert("RGBA"), dtype=np.float32) / 255.0


def summarize_stats(stats: list[dict]) -> dict:
    if not stats:
        return {}
    keys = stats[0].keys()
    return {
        key: {
            "min": min(item[key] for item in stats),
            "max": max(item[key] for item in stats),
            "mean": sum(item[key] for item in stats) / len(stats),
        }
        for key in keys
    }


def parse_hex_rgb(value: str) -> tuple[int, int, int]:
    cleaned = value.strip().lstrip("#")
    if len(cleaned) != 6:
        raise SystemExit(f"Expected 6-digit RGB hex, got {value!r}")
    try:
        return int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16)
    except ValueError as exc:
        raise SystemExit(f"Invalid RGB hex: {value!r}") from exc


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


if __name__ == "__main__":
    raise SystemExit(main())
