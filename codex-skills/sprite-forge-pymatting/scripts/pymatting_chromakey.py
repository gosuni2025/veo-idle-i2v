#!/usr/bin/env python3
"""Recover transparent sprites from magenta-background PNGs with PyMatting FG."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

try:
    from pymatting import estimate_foreground_ml
except ImportError as exc:  # pragma: no cover - depends on local setup
    raise SystemExit(
        "Missing pymatting. Install dependencies with: "
        "python3 -m pip install -r codex-skills/sprite-forge-pymatting/requirements.txt"
    ) from exc


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def main() -> int:
    args = parse_args()
    ffmpeg = require_binary("ffmpeg")
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"Input does not exist: {input_path}")

    out_dir = Path(args.output_dir).expanduser().resolve()
    raw_dir = out_dir / "frames_raw"
    keyed_dir = out_dir / "frames_chromakey_alpha"
    sprite_dir = out_dir / "frames_pymatting_fg"
    reset_dir(raw_dir)
    reset_dir(keyed_dir)
    reset_dir(sprite_dir)

    frames = collect_frames(input_path)
    if not frames:
        raise SystemExit(f"No supported image files found: {input_path}")

    stats: list[dict[str, object]] = []
    for index, frame_path in enumerate(frames):
        frame_name = frame_path.name if len(frames) > 1 else "frame_0001.png"
        raw_path = raw_dir / frame_name
        keyed_path = keyed_dir / frame_name
        out_path = sprite_dir / frame_name

        Image.open(frame_path).convert("RGB").save(raw_path)
        extract_alpha_with_ffmpeg(ffmpeg, raw_path, keyed_path, args)
        stats.append(process_with_pymatting(raw_path, keyed_path, out_path, args))

        if args.copy_to and len(frames) == 1:
            copy_to = Path(args.copy_to).expanduser().resolve()
            copy_to.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out_path, copy_to)

        if args.verbose or index == 0 or index == len(frames) - 1:
            print(f"Processed {index + 1}/{len(frames)}: {frame_path}")

    contact_path = out_dir / "contact_sheet_pymatting_fg.png"
    make_contact_sheet(sprite_dir, contact_path)
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(input_path),
        "out_dir": str(out_dir),
        "raw_dir": str(raw_dir),
        "chromakey_alpha_dir": str(keyed_dir),
        "sprite_dir": str(sprite_dir),
        "contact_sheet": str(contact_path),
        "frame_count": len(frames),
        "bg_color": args.bg_color,
        "chromakey_similarity": args.similarity,
        "chromakey_blend": args.blend,
        "pymatting": {
            "regularization": args.pymatting_regularization,
            "small_iterations": args.pymatting_small_iterations,
            "big_iterations": args.pymatting_big_iterations,
            "small_size": args.pymatting_small_size,
            "gradient_weight": args.pymatting_gradient_weight,
        },
        "stats": summarize_stats(stats),
        "frames": stats,
    }
    (out_dir / "spriteify-meta.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Sprite frames: {sprite_dir}")
    print(f"Contact sheet: {contact_path}")
    print(f"Metadata: {out_dir / 'spriteify-meta.json'}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Raw magenta PNG, image file, or folder of image files.")
    parser.add_argument("--output-dir", required=True, help="Output run directory.")
    parser.add_argument("--copy-to", help="Optional destination for a single processed PNG.")
    parser.add_argument("--bg-color", default="0xff00ff", help="FFmpeg chromakey color.")
    parser.add_argument("--similarity", type=float, default=0.24, help="FFmpeg chromakey similarity.")
    parser.add_argument("--blend", type=float, default=0.10, help="FFmpeg chromakey blend.")
    parser.add_argument("--pymatting-regularization", type=float, default=1e-5)
    parser.add_argument("--pymatting-small-iterations", type=int, default=6)
    parser.add_argument("--pymatting-big-iterations", type=int, default=1)
    parser.add_argument("--pymatting-small-size", type=int, default=64)
    parser.add_argument("--pymatting-gradient-weight", type=float, default=1.0)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def collect_frames(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in IMAGE_EXTS else []
    return sorted(item for item in path.iterdir() if item.is_file() and item.suffix.lower() in IMAGE_EXTS)


def extract_alpha_with_ffmpeg(ffmpeg: str, raw_path: Path, keyed_path: Path, args: argparse.Namespace) -> None:
    filter_expr = f"chromakey={args.bg_color}:{args.similarity}:{args.blend},format=rgba"
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(raw_path),
        "-vf",
        filter_expr,
        str(keyed_path),
    ]
    subprocess.run(command, check=True)


def process_with_pymatting(
    raw_path: Path,
    keyed_path: Path,
    out_path: Path,
    args: argparse.Namespace,
) -> dict[str, object]:
    raw = np.asarray(Image.open(raw_path).convert("RGB"), dtype=np.float32) / 255.0
    keyed = np.asarray(Image.open(keyed_path).convert("RGBA"), dtype=np.float32) / 255.0
    alpha = np.clip(keyed[:, :, 3], 0.0, 1.0)
    foreground = estimate_foreground_ml(
        raw,
        alpha,
        regularization=args.pymatting_regularization,
        n_small_iterations=args.pymatting_small_iterations,
        n_big_iterations=args.pymatting_big_iterations,
        small_size=args.pymatting_small_size,
        gradient_weight=args.pymatting_gradient_weight,
    )
    foreground = np.clip(foreground, 0.0, 1.0)
    rgba = np.dstack([foreground, alpha])
    Image.fromarray((rgba * 255.0).astype(np.uint8), mode="RGBA").save(out_path)
    return {
        "frame": raw_path.name,
        "output": str(out_path),
        "transparent_pixels": int(np.count_nonzero(alpha <= 0.0)),
        "semi_alpha_pixels": int(np.count_nonzero((alpha > 0.0) & (alpha < 1.0))),
        "opaque_pixels": int(np.count_nonzero(alpha >= 1.0)),
        "alpha_min": float(alpha.min()),
        "alpha_max": float(alpha.max()),
    }


def make_contact_sheet(frame_dir: Path, out_path: Path) -> None:
    frames = sorted(frame_dir.glob("*.png"))
    if not frames:
        return
    sample_count = min(25, len(frames))
    indices = np.linspace(0, len(frames) - 1, sample_count, dtype=int)
    thumb_w, thumb_h = 180, 270
    columns = min(5, sample_count)
    rows = int(np.ceil(sample_count / columns))
    pad = 8
    sheet = Image.new("RGB", (columns * (thumb_w + pad) + pad, rows * (thumb_h + pad) + pad), "#303030")
    for sheet_index, frame_index in enumerate(indices):
        rgba = Image.open(frames[frame_index]).convert("RGBA")
        bg = checkerboard(rgba.size)
        bg.alpha_composite(rgba)
        thumb = ImageOps.contain(bg.convert("RGB"), (thumb_w, thumb_h), method=Image.Resampling.LANCZOS)
        x = pad + (sheet_index % columns) * (thumb_w + pad)
        y = pad + (sheet_index // columns) * (thumb_h + pad)
        sheet.paste(thumb, (x, y))
    sheet.save(out_path)


def checkerboard(size: tuple[int, int], tile_size: int = 16) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size, "#737b88ff")
    draw = ImageDraw.Draw(image)
    for y in range(0, height, tile_size):
        for x in range(0, width, tile_size):
            if ((x // tile_size) + (y // tile_size)) % 2 == 0:
                draw.rectangle((x, y, x + tile_size - 1, y + tile_size - 1), fill="#87909cff")
    return image


def summarize_stats(stats: list[dict[str, object]]) -> dict[str, object]:
    keys = ("transparent_pixels", "semi_alpha_pixels", "opaque_pixels")
    return {key: int(sum(int(item[key]) for item in stats)) for key in keys}


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def require_binary(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"Missing required executable: {name}")
    return found


if __name__ == "__main__":
    raise SystemExit(main())
