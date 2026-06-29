#!/usr/bin/env python3
"""Prepare and collect a CorridorKey green-screen proxy experiment.

CorridorKey currently ships green/blue-screen checkpoints, not a magenta-screen
checkpoint. For this project we create a physically plausible green proxy:

1. Use the current chroma alpha.
2. Use the formula-unmixed foreground estimate as a seed straight foreground.
3. Composite that foreground over green using the alpha.
4. Feed the green proxy + alpha hint to CorridorKey.

The collected result is written back as transparent PNG frames for the local
viewer.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")


ROOT = Path("outputs/chroma-key-research/ffmpeg_chromakey_024_010")
RAW_DIR = Path("outputs/postprocess/character-animation-with-subtle-202606112011/frames_raw")
ALPHA_DIR = ROOT / "frames_transparent"
SEED_FG_DIR = ROOT / "frames_unmix_foreground"
CORRIDOR_ROOT = Path("tools/ml-matting/CorridorKey")
CLIP_DIR = CORRIDOR_ROOT / "ClipsForInference" / "veo_magenta_green_proxy"
VIEWER_OUTPUT_DIR = ROOT / "frames_corridorkey_proxy"
PNG_GLOB = "frame_*.png"


def main() -> int:
    args = parse_args()
    if args.action in ("prepare", "all"):
        prepare_proxy(args)
    if args.action in ("collect", "all"):
        collect_result(args)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare or collect CorridorKey green proxy frames.")
    parser.add_argument("action", choices=("prepare", "collect", "all"))
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--raw-dir", default=str(RAW_DIR))
    parser.add_argument("--alpha-dir", default=str(ALPHA_DIR))
    parser.add_argument("--seed-fg-dir", default=str(SEED_FG_DIR))
    parser.add_argument("--corridor-root", default=str(CORRIDOR_ROOT))
    parser.add_argument("--clip-dir", default=str(CLIP_DIR))
    parser.add_argument("--viewer-output-dir", default=str(VIEWER_OUTPUT_DIR))
    parser.add_argument("--green", default="00ff00", help="Proxy screen color as RGB hex.")
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--hint-blur", type=float, default=1.0)
    parser.add_argument("--compare-frame", type=int, default=96)
    return parser.parse_args()


def prepare_proxy(args: argparse.Namespace) -> None:
    raw_dir = resolve_path(args.raw_dir)
    alpha_dir = resolve_path(args.alpha_dir)
    seed_fg_dir = resolve_path(args.seed_fg_dir)
    clip_dir = resolve_path(args.clip_dir)
    input_dir = clip_dir / "Input"
    alpha_hint_dir = clip_dir / "AlphaHint"
    mask_hint_dir = clip_dir / "VideoMamaMaskHint"
    output_dir = clip_dir / "Output"

    reset_dir(input_dir)
    reset_dir(alpha_hint_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    mask_hint_dir.mkdir(parents=True, exist_ok=True)

    frames = sorted(raw_dir.glob(PNG_GLOB))
    if args.max_frames:
        frames = frames[: args.max_frames]
    if not frames:
        raise SystemExit(f"No raw frames found under {raw_dir}")

    green = np.array(parse_hex_rgb(args.green), dtype=np.float32) / 255.0
    for index, raw_path in enumerate(frames):
        seed_rgba = np.asarray(Image.open(seed_fg_dir / raw_path.name).convert("RGBA"), dtype=np.float32) / 255.0
        alpha = np.asarray(Image.open(alpha_dir / raw_path.name).convert("RGBA"), dtype=np.float32)[:, :, 3] / 255.0
        seed_rgb = seed_rgba[:, :, :3]
        proxy_rgb = seed_rgb * alpha[:, :, None] + green * (1.0 - alpha[:, :, None])

        Image.fromarray(np.clip(proxy_rgb * 255.0, 0, 255).astype(np.uint8), mode="RGB").save(
            input_dir / raw_path.name
        )

        alpha_img = Image.fromarray(np.clip(alpha * 255.0, 0, 255).astype(np.uint8), mode="L")
        if args.hint_blur > 0:
            alpha_img = alpha_img.filter(ImageFilter.GaussianBlur(args.hint_blur))
        alpha_img.save(alpha_hint_dir / raw_path.name)

        if index == 0 or (index + 1) % 25 == 0 or index == len(frames) - 1:
            print(f"Prepared {index + 1}/{len(frames)}")

    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "clip_dir": str(clip_dir),
        "input_dir": str(input_dir),
        "alpha_hint_dir": str(alpha_hint_dir),
        "frame_count": len(frames),
        "green_rgb": green.tolist(),
        "hint_blur": args.hint_blur,
        "note": "Synthetic green-screen proxy for running CorridorKey's green checkpoint on magenta-source footage.",
    }
    (clip_dir / "PROXY_METADATA.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Prepared CorridorKey proxy clip: {clip_dir}")


def collect_result(args: argparse.Namespace) -> None:
    root = resolve_path(args.root)
    clip_dir = resolve_path(args.clip_dir)
    viewer_output_dir = resolve_path(args.viewer_output_dir)
    fg_dir = clip_dir / "Output" / "FG"
    matte_dir = clip_dir / "Output" / "Matte"
    comp_dir = clip_dir / "Output" / "Comp"

    if not fg_dir.exists() or not matte_dir.exists():
        raise SystemExit(f"CorridorKey output not found yet: {clip_dir / 'Output'}")

    import cv2

    reset_dir(viewer_output_dir)
    fg_frames = sorted(fg_dir.glob("*.exr"))
    if args.max_frames:
        fg_frames = fg_frames[: args.max_frames]
    if not fg_frames:
        raise SystemExit(f"No FG EXR frames found under {fg_dir}")

    for index, fg_path in enumerate(fg_frames):
        matte_path = matte_dir / fg_path.name
        fg_bgr = cv2.imread(str(fg_path), cv2.IMREAD_ANYDEPTH | cv2.IMREAD_COLOR)
        matte = cv2.imread(str(matte_path), cv2.IMREAD_ANYDEPTH | cv2.IMREAD_UNCHANGED)
        if fg_bgr is None or matte is None:
            raise SystemExit(f"Could not read CorridorKey EXR pair: {fg_path}, {matte_path}")

        fg_rgb = cv2.cvtColor(fg_bgr, cv2.COLOR_BGR2RGB)
        if matte.ndim == 3:
            matte = matte[:, :, 0]
        rgba = np.dstack([np.clip(fg_rgb, 0, 1), np.clip(matte, 0, 1)])
        out_name = f"frame_{index:04d}.png" if not fg_path.stem.startswith("frame_") else f"{fg_path.stem}.png"
        Image.fromarray(np.clip(rgba * 255.0, 0, 255).astype(np.uint8), mode="RGBA").save(
            viewer_output_dir / out_name
        )
        if index == 0 or (index + 1) % 25 == 0 or index == len(fg_frames) - 1:
            print(f"Collected {index + 1}/{len(fg_frames)}")

    make_corridorkey_comparison(root, viewer_output_dir, root / "comparison_corridorkey_proxy.jpg", args.compare_frame)
    make_corridorkey_contact(root, viewer_output_dir, root / "contact_sheet_corridorkey_proxy.jpg")

    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "clip_dir": str(clip_dir),
        "fg_dir": str(fg_dir),
        "matte_dir": str(matte_dir),
        "comp_dir": str(comp_dir),
        "viewer_output_dir": str(viewer_output_dir),
        "frame_count": len(fg_frames),
    }
    (root / "corridorkey-proxy-meta.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Collected CorridorKey PNG frames: {viewer_output_dir}")


def make_corridorkey_comparison(root: Path, corridor_dir: Path, out_path: Path, frame_no: int) -> None:
    rows = [
        ("Chromakey", root / "frames_transparent"),
        ("PyMatting + Outline", root / "frames_pymatting_outline_hybrid"),
        ("CorridorKey Proxy", corridor_dir),
    ]
    backgrounds = [
        ("dark", (24, 25, 28)),
        ("sky", (95, 146, 196)),
        ("green", (38, 130, 84)),
        ("red", (150, 49, 55)),
    ]
    thumb_w, thumb_h = 320, 180
    label_h = 28
    sheet = Image.new("RGB", (len(backgrounds) * thumb_w, len(rows) * (thumb_h + label_h)), (18, 19, 22))
    draw = ImageDraw.Draw(sheet)
    for row_index, (label, folder) in enumerate(rows):
        frame_path = folder / f"frame_{frame_no:04d}.png"
        if not frame_path.exists():
            continue
        rgba = Image.open(frame_path).convert("RGBA")
        y = row_index * (thumb_h + label_h)
        for col_index, (bg_label, color) in enumerate(backgrounds):
            x = col_index * thumb_w
            bg = Image.new("RGBA", rgba.size, color + (255,))
            bg.alpha_composite(rgba)
            thumb = bg.convert("RGB").resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
            sheet.paste(thumb, (x, y + label_h))
            draw.text((x + 8, y + 7), f"{label} / {bg_label}", fill=(235, 235, 235))
    sheet.save(out_path, quality=92)


def make_corridorkey_contact(root: Path, corridor_dir: Path, out_path: Path) -> None:
    columns = [
        ("Chromakey", root / "frames_transparent"),
        ("PyMatting + Outline", root / "frames_pymatting_outline_hybrid"),
        ("CorridorKey Proxy", corridor_dir),
    ]
    sample_frames = [0, 48, 96, 144, 191]
    thumb_w, thumb_h = 320, 180
    label_h = 26
    sheet = Image.new("RGB", (len(columns) * thumb_w, len(sample_frames) * (thumb_h + label_h)), (18, 19, 22))
    draw = ImageDraw.Draw(sheet)
    for row_index, frame_no in enumerate(sample_frames):
        y = row_index * (thumb_h + label_h)
        for col_index, (label, folder) in enumerate(columns):
            frame_path = folder / f"frame_{frame_no:04d}.png"
            if not frame_path.exists():
                continue
            rgba = Image.open(frame_path).convert("RGBA")
            bg = checkerboard(rgba.size)
            bg.alpha_composite(rgba)
            thumb = ImageOps.contain(bg.convert("RGB"), (thumb_w, thumb_h), method=Image.Resampling.LANCZOS)
            x = col_index * thumb_w
            sheet.paste(thumb, (x, y + label_h))
            draw.text((x + 8, y + 7), f"{label} f{frame_no:04d}", fill=(235, 235, 235))
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


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def parse_hex_rgb(value: str) -> tuple[int, int, int]:
    cleaned = value.strip().lstrip("#")
    if len(cleaned) != 6:
        raise SystemExit(f"Expected 6-digit RGB hex, got {value!r}")
    try:
        return int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16)
    except ValueError as exc:
        raise SystemExit(f"Invalid RGB hex: {value!r}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
