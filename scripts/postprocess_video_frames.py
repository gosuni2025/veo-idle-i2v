#!/usr/bin/env python3
"""Extract video frames and turn a solid-color background transparent.

The default settings target the project's magenta Google/Flow idle videos:
raw frames are kept unchanged, transparent frames preserve the same frame size,
and the chroma-key parameters are written to metadata for repeatability.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except ImportError as exc:  # pragma: no cover - depends on local setup
    raise SystemExit(
        "Missing image dependencies. Install them with: python3 -m pip install -r requirements.txt"
    ) from exc


DEFAULT_SEARCH_DIR = Path("outputs/google-flow")
DEFAULT_OUTPUT_ROOT = Path("outputs/postprocess")
FRAME_PATTERN = "frame_%04d.png"
PNG_GLOB = "frame_*.png"


def main() -> int:
    args = parse_args()

    video_path = resolve_video_path(args.video)
    out_dir = resolve_path(args.out_dir) if args.out_dir else DEFAULT_OUTPUT_ROOT / slugify(video_path.stem)
    raw_dir = resolve_path(args.raw_dir) if args.raw_dir else out_dir / "frames_raw"
    transparent_dir = (
        resolve_path(args.transparent_dir) if args.transparent_dir else out_dir / "frames_transparent"
    )
    metadata_path = resolve_path(args.metadata) if args.metadata else out_dir / "postprocess-meta.json"

    ffmpeg = require_binary("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    video_info = probe_video(ffprobe, video_path) if ffprobe else {}

    raw_dir.mkdir(parents=True, exist_ok=True)
    transparent_dir.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    clear_generated_frames(raw_dir)
    clear_generated_frames(transparent_dir)

    print(f"Video: {video_path}")
    print(f"Output: {out_dir}")
    print(f"Raw frames: {raw_dir}")
    print(f"Transparent frames: {transparent_dir}")

    extract_frames(ffmpeg, video_path, raw_dir, args.max_frames)
    raw_frames = sorted(raw_dir.glob(PNG_GLOB))
    if not raw_frames:
        raise SystemExit("No frames were extracted.")

    bg_color = parse_bg_color(args.bg_color, raw_frames[0], args.auto_sample)
    print(f"Background key: #{bg_color[0]:02X}{bg_color[1]:02X}{bg_color[2]:02X}")

    frame_stats = []
    for index, raw_frame in enumerate(raw_frames):
        out_frame = transparent_dir / raw_frame.name
        stats = key_frame(
            raw_frame,
            out_frame,
            bg_color=bg_color,
            hard_threshold=args.hard_threshold,
            edge_threshold=args.edge_threshold,
            fringe_threshold=args.fringe_threshold,
            fringe_mode=args.fringe_mode,
            magenta_margin=args.magenta_margin,
            halo_radius=args.halo_radius,
            halo_margin=args.halo_margin,
            border_cleanup_depth=args.border_cleanup_depth,
            border_margin=args.border_margin,
            despill_radius=args.despill_radius,
            despill_margin=args.despill_margin,
            despill_amount=args.despill_amount,
            alpha_threshold=args.alpha_threshold,
        )
        stats["frame"] = raw_frame.name
        frame_stats.append(stats)
        if args.verbose or index == 0 or (index + 1) % 25 == 0 or index == len(raw_frames) - 1:
            print(f"Processed {index + 1}/{len(raw_frames)}")

    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_video": str(video_path),
        "video_info": video_info,
        "raw_frames_dir": str(raw_dir),
        "transparent_frames_dir": str(transparent_dir),
        "frame_count": len(raw_frames),
        "frame_pattern": FRAME_PATTERN,
        "background_key_rgb": list(bg_color),
        "hard_threshold": args.hard_threshold,
        "edge_threshold": args.edge_threshold,
        "fringe_threshold": args.fringe_threshold,
        "fringe_mode": args.fringe_mode,
        "magenta_margin": args.magenta_margin,
        "halo_radius": args.halo_radius,
        "halo_margin": args.halo_margin,
        "border_cleanup_depth": args.border_cleanup_depth,
        "border_margin": args.border_margin,
        "despill_radius": args.despill_radius,
        "despill_margin": args.despill_margin,
        "despill_amount": args.despill_amount,
        "alpha_threshold": args.alpha_threshold,
        "max_frames": args.max_frames,
        "stats": summarize_stats(frame_stats),
        "reference": {
            "name": "0x0funky/agent-sprite-forge",
            "url": "https://github.com/0x0funky/agent-sprite-forge",
            "note": "Starts from the same deterministic chroma-key idea, then adds video-specific halo cleanup and despill for lossy MP4 magenta bleed.",
        },
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Metadata: {metadata_path}")
    print(f"Frames written: {len(raw_frames)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split a video into PNG frames and make a solid background transparent."
    )
    parser.add_argument(
        "video",
        nargs="?",
        help="Input MP4. If omitted, the newest .mp4 under outputs/google-flow is used.",
    )
    parser.add_argument("--out-dir", help="Output run directory. Defaults to outputs/postprocess/<video-slug>.")
    parser.add_argument("--raw-dir", help="Directory for original extracted PNG frames.")
    parser.add_argument("--transparent-dir", help="Directory for transparent PNG frames.")
    parser.add_argument("--metadata", help="Metadata JSON path.")
    parser.add_argument(
        "--bg-color",
        default="ff00ff",
        help="Background color as hex RGB, e.g. ff00ff. Use 'auto' to sample the first frame corners.",
    )
    parser.add_argument(
        "--hard-threshold",
        type=int,
        default=55,
        help="Distance from the key color removed everywhere. Lower protects foreground colors.",
    )
    parser.add_argument(
        "--edge-threshold",
        type=int,
        default=165,
        help="Distance from the key color removed only when connected to the image edge.",
    )
    parser.add_argument(
        "--fringe-threshold",
        type=int,
        default=300,
        help="Extra cleanup threshold for compressed magenta/purple fringe pixels.",
    )
    parser.add_argument(
        "--fringe-mode",
        choices=("global", "edge"),
        default="global",
        help="Use global for magenta backgrounds; use edge to protect possible magenta foreground details.",
    )
    parser.add_argument(
        "--magenta-margin",
        type=int,
        default=24,
        help="Minimum red/blue dominance over green for magenta fringe cleanup.",
    )
    parser.add_argument(
        "--halo-radius",
        type=int,
        default=2,
        help="Pixels around transparent regions where extra magenta halo cleanup is allowed.",
    )
    parser.add_argument(
        "--halo-margin",
        type=int,
        default=8,
        help="Minimum red/blue dominance over green for halo pixels to be made transparent.",
    )
    parser.add_argument(
        "--despill-radius",
        type=int,
        default=3,
        help="Pixels around transparent regions where magenta spill is color-neutralized.",
    )
    parser.add_argument(
        "--despill-margin",
        type=int,
        default=4,
        help="Minimum red/blue dominance over green before despill is applied.",
    )
    parser.add_argument(
        "--despill-amount",
        type=float,
        default=0.85,
        help="How strongly to subtract magenta spill from kept edge pixels, from 0 to 1.",
    )
    parser.add_argument(
        "--border-cleanup-depth",
        type=int,
        default=2,
        help="Clean magenta/purple compression residue within N pixels of the frame border.",
    )
    parser.add_argument(
        "--border-margin",
        type=int,
        default=4,
        help="Minimum red/blue dominance over green for frame-border cleanup.",
    )
    parser.add_argument(
        "--alpha-threshold",
        type=int,
        default=1,
        help="Pixels with alpha below this value are treated as already transparent.",
    )
    parser.add_argument(
        "--auto-sample",
        type=int,
        default=16,
        help="Corner sample size in pixels when --bg-color auto is used.",
    )
    parser.add_argument("--max-frames", type=int, help="Extract only the first N frames, useful for tuning.")
    parser.add_argument("--verbose", action="store_true", help="Print every processed frame.")
    return parser.parse_args()


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def resolve_video_path(value: str | None) -> Path:
    if value:
        path = resolve_path(value)
        if not path.exists():
            raise SystemExit(f"Input video does not exist: {path}")
        return path

    search_dir = resolve_path(DEFAULT_SEARCH_DIR)
    candidates = sorted(search_dir.glob("*.mp4"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit(f"No .mp4 files found under {search_dir}. Pass an input video path.")
    return candidates[0]


def require_binary(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"Required command not found: {name}")
    return found


def probe_video(ffprobe: str | None, video_path: Path) -> dict[str, object]:
    if not ffprobe:
        return {}
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames,duration",
        "-of",
        "json",
        str(video_path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    return streams[0] if streams else {}


def clear_generated_frames(directory: Path) -> None:
    for frame in directory.glob(PNG_GLOB):
        frame.unlink()


def extract_frames(ffmpeg: str, video_path: Path, raw_dir: Path, max_frames: int | None) -> None:
    output_pattern = raw_dir / FRAME_PATTERN
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-map",
        "0:v:0",
    ]
    if max_frames is not None:
        command.extend(["-frames:v", str(max_frames)])
    command.extend(["-start_number", "0", str(output_pattern)])
    subprocess.run(command, check=True)


def parse_bg_color(value: str, first_frame: Path, sample_size: int) -> tuple[int, int, int]:
    if value.lower() == "auto":
        return detect_corner_color(first_frame, sample_size)

    normalized = value.strip().removeprefix("#").removeprefix("0x")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", normalized):
        raise SystemExit("--bg-color must be a 6-digit hex RGB value or 'auto'.")
    return tuple(int(normalized[index : index + 2], 16) for index in (0, 2, 4))


def detect_corner_color(image_path: Path, sample_size: int) -> tuple[int, int, int]:
    with Image.open(image_path) as image:
        rgb = np.asarray(image.convert("RGB"))
    height, width, _ = rgb.shape
    size = max(1, min(sample_size, width, height))
    samples = np.concatenate(
        [
            rgb[:size, :size].reshape(-1, 3),
            rgb[:size, width - size :].reshape(-1, 3),
            rgb[height - size :, :size].reshape(-1, 3),
            rgb[height - size :, width - size :].reshape(-1, 3),
        ],
        axis=0,
    )
    median = np.median(samples, axis=0).astype(np.uint8)
    return int(median[0]), int(median[1]), int(median[2])


def key_frame(
    input_path: Path,
    output_path: Path,
    *,
    bg_color: tuple[int, int, int],
    hard_threshold: int,
    edge_threshold: int,
    fringe_threshold: int,
    fringe_mode: str,
    magenta_margin: int,
    halo_radius: int,
    halo_margin: int,
    border_cleanup_depth: int,
    border_margin: int,
    despill_radius: int,
    despill_margin: int,
    despill_amount: float,
    alpha_threshold: int,
) -> dict[str, int | float]:
    with Image.open(input_path) as image:
        rgba = np.array(image.convert("RGBA"), dtype=np.uint8)

    rgb = rgba[:, :, :3].astype(np.int32)
    alpha = rgba[:, :, 3]
    target = np.array(bg_color, dtype=np.int32)
    distance_sq = np.sum((rgb - target) ** 2, axis=2)

    hard_mask = distance_sq <= hard_threshold**2
    edge_candidate_mask = distance_sq <= edge_threshold**2
    fringe_candidate_mask = np.zeros_like(edge_candidate_mask, dtype=bool)
    if fringe_threshold > edge_threshold and is_magenta_key(bg_color):
        red = rgb[:, :, 0]
        green = rgb[:, :, 1]
        blue = rgb[:, :, 2]
        magenta_like = (red >= green + magenta_margin) & (blue >= green + magenta_margin)
        fringe_candidate_mask = (distance_sq <= fringe_threshold**2) & magenta_like
        edge_candidate_mask = edge_candidate_mask | fringe_candidate_mask
    edge_mask = edge_connected_mask(edge_candidate_mask)
    fringe_mask = fringe_candidate_mask if fringe_mode == "global" else np.zeros_like(fringe_candidate_mask)
    already_transparent = alpha < alpha_threshold
    transparent_mask = hard_mask | edge_mask | fringe_mask | already_transparent

    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    halo_area = dilate_mask(transparent_mask, halo_radius) & ~transparent_mask
    halo_mask = np.zeros_like(transparent_mask, dtype=bool)
    if is_magenta_key(bg_color) and halo_radius > 0:
        halo_like = (red >= green + halo_margin) & (blue >= green + halo_margin)
        halo_mask = halo_area & halo_like
        transparent_mask = transparent_mask | halo_mask
    border_mask = np.zeros_like(transparent_mask, dtype=bool)
    if is_magenta_key(bg_color) and border_cleanup_depth > 0:
        border_area = make_border_mask(transparent_mask.shape, border_cleanup_depth)
        border_like = (red >= green + border_margin) & (blue >= green + border_margin)
        border_mask = border_area & border_like
        transparent_mask = transparent_mask | border_mask

    alpha[transparent_mask] = 0
    rgba[:, :, 3] = alpha

    despill_mask = np.zeros_like(transparent_mask, dtype=bool)
    if is_magenta_key(bg_color) and despill_radius > 0 and despill_amount > 0:
        despill_area = dilate_mask(transparent_mask, despill_radius) & ~transparent_mask
        despill_strength = np.minimum(red, blue) - green
        despill_mask = despill_area & (despill_strength >= despill_margin)
        if np.any(despill_mask):
            rgb_out = rgba[:, :, :3].astype(np.int32)
            subtract = np.zeros_like(despill_strength, dtype=np.int32)
            subtract[despill_mask] = np.rint(
                (despill_strength[despill_mask] - despill_margin) * clamp(despill_amount, 0.0, 1.0)
            ).astype(np.int32)
            rgb_out[:, :, 0] = np.where(despill_mask, np.maximum(green, rgb_out[:, :, 0] - subtract), rgb_out[:, :, 0])
            rgb_out[:, :, 2] = np.where(despill_mask, np.maximum(green, rgb_out[:, :, 2] - subtract), rgb_out[:, :, 2])
            rgba[:, :, :3] = np.clip(rgb_out, 0, 255).astype(np.uint8)

    Image.fromarray(rgba, mode="RGBA").save(output_path)

    height, width = transparent_mask.shape
    transparent_pixels = int(np.count_nonzero(transparent_mask))
    return {
        "width": width,
        "height": height,
        "transparent_pixels": transparent_pixels,
        "transparent_ratio": round(transparent_pixels / float(width * height), 6),
        "hard_pixels": int(np.count_nonzero(hard_mask)),
        "edge_pixels": int(np.count_nonzero(edge_mask)),
        "fringe_candidate_pixels": int(np.count_nonzero(fringe_candidate_mask)),
        "fringe_pixels": int(np.count_nonzero(fringe_mask)),
        "halo_pixels": int(np.count_nonzero(halo_mask)),
        "border_pixels": int(np.count_nonzero(border_mask)),
        "despill_pixels": int(np.count_nonzero(despill_mask)),
    }


def is_magenta_key(color: tuple[int, int, int]) -> bool:
    red, green, blue = color
    return red >= 180 and blue >= 180 and green <= 96


def dilate_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    expanded = mask.copy()
    for _ in range(max(0, radius)):
        previous = expanded
        expanded = previous.copy()
        expanded[1:, :] |= previous[:-1, :]
        expanded[:-1, :] |= previous[1:, :]
        expanded[:, 1:] |= previous[:, :-1]
        expanded[:, :-1] |= previous[:, 1:]
        expanded[1:, 1:] |= previous[:-1, :-1]
        expanded[:-1, :-1] |= previous[1:, 1:]
        expanded[1:, :-1] |= previous[:-1, 1:]
        expanded[:-1, 1:] |= previous[1:, :-1]
    return expanded


def make_border_mask(shape: tuple[int, int], depth: int) -> np.ndarray:
    height, width = shape
    border = np.zeros(shape, dtype=bool)
    depth = max(0, min(depth, height, width))
    if depth == 0:
        return border
    border[:depth, :] = True
    border[height - depth :, :] = True
    border[:, :depth] = True
    border[:, width - depth :] = True
    return border


def clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def edge_connected_mask(mask: np.ndarray) -> np.ndarray:
    height, width = mask.shape
    connected = np.zeros_like(mask, dtype=bool)
    stack: list[tuple[int, int]] = []

    for x in range(width):
        if mask[0, x]:
            stack.append((x, 0))
        if mask[height - 1, x]:
            stack.append((x, height - 1))
    for y in range(height):
        if mask[y, 0]:
            stack.append((0, y))
        if mask[y, width - 1]:
            stack.append((width - 1, y))

    while stack:
        x, y = stack.pop()
        if connected[y, x] or not mask[y, x]:
            continue

        left = x
        while left > 0 and mask[y, left - 1] and not connected[y, left - 1]:
            left -= 1

        right = x
        while right + 1 < width and mask[y, right + 1] and not connected[y, right + 1]:
            right += 1

        connected[y, left : right + 1] = True
        for next_y in (y - 1, y + 1):
            if next_y < 0 or next_y >= height:
                continue
            scan_x = left
            while scan_x <= right:
                while scan_x <= right and (connected[next_y, scan_x] or not mask[next_y, scan_x]):
                    scan_x += 1
                if scan_x <= right:
                    stack.append((scan_x, next_y))
                    while scan_x <= right and mask[next_y, scan_x] and not connected[next_y, scan_x]:
                        scan_x += 1

    return connected


def summarize_stats(frame_stats: list[dict[str, int | float]]) -> dict[str, object]:
    ratios = [float(item["transparent_ratio"]) for item in frame_stats]
    return {
        "transparent_ratio_min": min(ratios),
        "transparent_ratio_max": max(ratios),
        "transparent_ratio_avg": round(sum(ratios) / len(ratios), 6),
        "first_frame": frame_stats[0],
        "last_frame": frame_stats[-1],
    }


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug or "video"


if __name__ == "__main__":
    raise SystemExit(main())
