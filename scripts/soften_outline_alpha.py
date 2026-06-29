#!/usr/bin/env python3
"""Create a softer alpha-outline variant from transparent PNG frames.

This keeps the original keyed frames intact and writes a second frame set where
only the foreground pixels nearest transparent background receive a lower alpha
cap plus conservative magenta decontamination.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps


PNG_GLOB = "frame_*.png"
DEFAULT_INPUT_DIR = Path("outputs/chroma-key-research/ffmpeg_chromakey_024_010/frames_transparent")
DEFAULT_OUTPUT_DIR = Path("outputs/chroma-key-research/ffmpeg_chromakey_024_010/frames_outline_alpha")
DEFAULT_METADATA = Path("outputs/chroma-key-research/ffmpeg_chromakey_024_010/outline-alpha-meta.json")
DEFAULT_CONTACT_SHEET = Path("outputs/chroma-key-research/ffmpeg_chromakey_024_010/contact_sheet_outline_alpha.jpg")


def main() -> int:
    args = parse_args()
    input_dir = resolve_path(args.input_dir)
    output_dir = resolve_path(args.output_dir)
    metadata_path = resolve_path(args.metadata)
    contact_sheet_path = resolve_path(args.contact_sheet)

    frames = sorted(input_dir.glob(PNG_GLOB))
    if not frames:
        raise SystemExit(f"No {PNG_GLOB} files found under {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    for old_frame in output_dir.glob(PNG_GLOB):
        old_frame.unlink()

    key_rgb = parse_hex_rgb(args.key_color)
    stats = []
    for index, frame in enumerate(frames):
        out_frame = output_dir / frame.name
        stats.append(process_frame(frame, out_frame, args, key_rgb))
        if index == 0 or (index + 1) % 25 == 0 or index == len(frames) - 1:
            print(f"Processed {index + 1}/{len(frames)}")

    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "frame_count": len(frames),
        "key_rgb": list(key_rgb),
        "edge_radius": args.edge_radius,
        "outer_alpha": args.outer_alpha,
        "inner_alpha": args.inner_alpha,
        "min_alpha": args.min_alpha,
        "spill_margin": args.spill_margin,
        "despill_amount": args.despill_amount,
        "decontaminate_amount": args.decontaminate_amount,
        "desaturate_amount": args.desaturate_amount,
        "stats": summarize_stats(stats),
        "note": (
            "Softens only foreground pixels adjacent to transparency. The color pass treats magenta "
            "edge pixels as background-contaminated and pulls them away from the key color."
        ),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    make_contact_sheet(output_dir, contact_sheet_path)

    print(f"Output frames: {output_dir}")
    print(f"Metadata: {metadata_path}")
    print(f"Contact sheet: {contact_sheet_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bake a subtle alpha outline into transparent PNG frames.")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR), help="Directory containing keyed RGBA PNG frames.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory to write softened RGBA frames.")
    parser.add_argument("--metadata", default=str(DEFAULT_METADATA), help="Metadata JSON path.")
    parser.add_argument("--contact-sheet", default=str(DEFAULT_CONTACT_SHEET), help="Contact sheet JPG path.")
    parser.add_argument("--key-color", default="ff00ff", help="Chroma key background color as hex RGB.")
    parser.add_argument("--edge-radius", type=int, default=2, help="Foreground outline width in pixels.")
    parser.add_argument("--outer-alpha", type=int, default=205, help="Maximum alpha on the outermost outline ring.")
    parser.add_argument("--inner-alpha", type=int, default=240, help="Maximum alpha on the innermost outline ring.")
    parser.add_argument("--min-alpha", type=int, default=6, help="Ignore pixels at or below this alpha.")
    parser.add_argument(
        "--spill-margin",
        type=int,
        default=8,
        help="Minimum min(red, blue) dominance over green before magenta cleanup applies.",
    )
    parser.add_argument(
        "--despill-amount",
        type=float,
        default=0.72,
        help="How strongly red/blue magenta spill is subtracted on outline pixels.",
    )
    parser.add_argument(
        "--decontaminate-amount",
        type=float,
        default=0.45,
        help="Blend amount for foreground color recovery using the known magenta key color.",
    )
    parser.add_argument(
        "--desaturate-amount",
        type=float,
        default=0.12,
        help="Small neutralization pass for remaining purple outline pixels.",
    )
    return parser.parse_args()


def process_frame(frame: Path, out_frame: Path, args: argparse.Namespace, key_rgb: tuple[int, int, int]) -> dict:
    image = Image.open(frame).convert("RGBA")
    rgba = np.asarray(image, dtype=np.float32).copy()
    rgb = rgba[:, :, :3]
    alpha = rgba[:, :, 3]

    foreground = alpha > args.min_alpha
    if not np.any(foreground):
        image.save(out_frame)
        return {"outline_pixels": 0, "spill_pixels": 0, "decontaminated_pixels": 0}

    rings = outline_rings(foreground, max(0, args.edge_radius))
    outline_mask = np.zeros(foreground.shape, dtype=bool)
    for ring_index, ring in enumerate(rings):
        if not np.any(ring):
            continue
        outline_mask |= ring
        if len(rings) == 1:
            cap = args.outer_alpha
        else:
            t = ring_index / (len(rings) - 1)
            cap = args.outer_alpha + (args.inner_alpha - args.outer_alpha) * t
        alpha[ring] = np.minimum(alpha[ring], cap)

    if np.any(outline_mask):
        cleanup_outline_color(rgb, alpha, outline_mask, key_rgb, args)

    rgba[:, :, :3] = np.clip(rgb, 0, 255)
    rgba[:, :, 3] = np.clip(alpha, 0, 255)
    Image.fromarray(rgba.astype(np.uint8), mode="RGBA").save(out_frame)

    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    spill_mask = outline_mask & ((np.minimum(red, blue) - green) >= args.spill_margin)
    return {
        "outline_pixels": int(np.count_nonzero(outline_mask)),
        "spill_pixels": int(np.count_nonzero(spill_mask)),
        "decontaminated_pixels": int(np.count_nonzero(outline_mask & (alpha < 252))),
    }


def cleanup_outline_color(
    rgb: np.ndarray,
    alpha: np.ndarray,
    outline_mask: np.ndarray,
    key_rgb: tuple[int, int, int],
    args: argparse.Namespace,
) -> None:
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    spill_strength = np.minimum(red, blue) - green
    spill_mask = outline_mask & (spill_strength >= args.spill_margin)
    if not np.any(spill_mask):
        return

    if args.decontaminate_amount > 0:
        key = np.array(key_rgb, dtype=np.float32)
        alpha_unit = np.clip(alpha / 255.0, 0.08, 1.0)
        recovered = (rgb - (1.0 - alpha_unit[:, :, None]) * key) / alpha_unit[:, :, None]
        recovered = np.clip(recovered, 0, 255)
        amount = clamp01(args.decontaminate_amount)
        rgb[spill_mask] = rgb[spill_mask] * (1.0 - amount) + recovered[spill_mask] * amount

    if args.despill_amount > 0:
        red = rgb[:, :, 0]
        green = rgb[:, :, 1]
        blue = rgb[:, :, 2]
        spill_strength = np.maximum(0, np.minimum(red, blue) - green - args.spill_margin)
        subtract = spill_strength * clamp01(args.despill_amount)
        rgb[:, :, 0] = np.where(spill_mask, np.maximum(green, red - subtract), red)
        rgb[:, :, 2] = np.where(spill_mask, np.maximum(green, blue - subtract), blue)

    if args.desaturate_amount > 0:
        amount = clamp01(args.desaturate_amount)
        luma = rgb[:, :, 0] * 0.299 + rgb[:, :, 1] * 0.587 + rgb[:, :, 2] * 0.114
        rgb[spill_mask] = rgb[spill_mask] * (1.0 - amount) + luma[spill_mask, None] * amount


def outline_rings(mask: np.ndarray, radius: int) -> list[np.ndarray]:
    if radius <= 0:
        return []

    source = Image.fromarray(mask.astype(np.uint8) * 255, mode="L")
    previous = mask
    rings: list[np.ndarray] = []
    for step in range(1, radius + 1):
        eroded = np.asarray(source.filter(ImageFilter.MinFilter(step * 2 + 1))) > 0
        ring = previous & ~eroded
        rings.append(ring)
        previous = eroded
    return rings


def make_contact_sheet(frame_dir: Path, out_path: Path) -> None:
    frames = sorted(frame_dir.glob(PNG_GLOB))
    if not frames:
        return

    sample_indices = np.linspace(0, len(frames) - 1, num=min(12, len(frames)), dtype=int)
    thumbs = []
    for index in sample_indices:
        rgba = Image.open(frames[index]).convert("RGBA")
        checker = checkerboard(rgba.size)
        checker.alpha_composite(rgba)
        thumb = ImageOps.contain(checker.convert("RGB"), (320, 180), method=Image.Resampling.LANCZOS)
        thumbs.append(thumb)

    columns = 4
    rows = int(np.ceil(len(thumbs) / columns))
    sheet = Image.new("RGB", (columns * 320, rows * 180), (28, 30, 34))
    for index, thumb in enumerate(thumbs):
        x = (index % columns) * 320
        y = (index // columns) * 180
        sheet.paste(thumb, (x, y))
    out_path.parent.mkdir(parents=True, exist_ok=True)
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
