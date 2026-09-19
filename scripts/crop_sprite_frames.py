#!/usr/bin/env python3
"""Crop transparent sprite frame sequences with one shared rectangle."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps


DEFAULT_OUTPUT_ROOT = Path("outputs/sprites-pymatting")
DEFAULT_FRAME_DIR_NAME = "frames_pymatting_fg"
PNG_GLOB = "frame_*.png"
GENERATED_OUTPUT_FILES = ("crop-meta.json", "animation-sequence.json", "contact_sheet_crop.jpg")


def main() -> int:
    args = parse_args()
    frame_dir, root_dir = resolve_frame_dir(args.source)
    frames = sorted(frame_dir.glob(PNG_GLOB))
    if not frames:
        raise SystemExit(f"No {PNG_GLOB} files found under {frame_dir}")

    with Image.open(frames[0]) as first:
        frame_size = first.size

    crop, timeline, output_size_spec, output_scale_percent = read_export_config(args)
    crop = maybe_scale_crop(crop, frame_size, args)
    crop = validate_crop(crop, frame_size)
    output_size = resolve_output_size(crop, output_size_spec, output_scale_percent)
    timeline = resolve_timeline(args, timeline, len(frames))
    animation_frames = select_frames(frames, timeline)
    if not animation_frames:
        raise SystemExit("Frame selection settings removed every frame.")

    out_dir = resolve_path(args.out_dir) if args.out_dir else default_out_dir(root_dir, frame_dir, crop, timeline, output_size)
    prepare_output_dir(out_dir, args.overwrite)

    box = (crop["x"], crop["y"], crop["x"] + crop["width"], crop["y"] + crop["height"])
    output_files = []
    animation_sequence = []
    source_to_output_index: dict[int, int] = {}
    for sequence_index, (source_number, frame_path) in enumerate(animation_frames):
        if source_number in source_to_output_index:
            output_index = source_to_output_index[source_number]
            animation_sequence.append(
                {
                    "sequence_index": sequence_index,
                    "source_frame": source_number,
                    "image_index": output_index,
                    "file": output_files[output_index]["file"],
                }
            )
            continue

        output_index = len(output_files)
        source_to_output_index[source_number] = output_index
        out_name = frame_path.name if args.preserve_names else f"frame_{output_index:04d}.png"
        with Image.open(frame_path) as image:
            export_image = resize_rgba_for_export(image.convert("RGBA").crop(box), output_size)
            export_image.save(out_dir / out_name)
        output_files.append({"image_index": output_index, "source_frame": source_number, "file": out_name})
        animation_sequence.append(
            {
                "sequence_index": sequence_index,
                "source_frame": source_number,
                "image_index": output_index,
                "file": out_name,
            }
        )
        if output_index == 0 or (output_index + 1) % 50 == 0 or sequence_index == len(animation_frames) - 1:
            print(f"Exported unique image {output_index + 1}/{len(set(item[0] for item in animation_frames))}")

    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_frame_dir": str(frame_dir),
        "out_dir": str(out_dir),
        "source_frame_count": len(frames),
        "frame_count": len(animation_frames),
        "unique_frame_count": len(output_files),
        "source_frame_size": {"width": frame_size[0], "height": frame_size[1]},
        "output_size": output_size,
        "output_scale_percent": output_scale_percent,
        "output_frame_size": output_size,
        "crop": crop,
        "timeline": timeline,
        "output_files": output_files,
        "animation_indices": [item["image_index"] for item in animation_sequence],
        "animation_source_frames": [item["source_frame"] for item in animation_sequence],
        "animation_sequence": animation_sequence,
    }
    metadata_path = out_dir / "crop-meta.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    sequence_path = out_dir / "animation-sequence.json"
    sequence = {
        "created_at": metadata["created_at"],
        "fps": timeline["fps"],
        "ping_pong": timeline["ping_pong"],
        "frame_count": metadata["frame_count"],
        "unique_frame_count": metadata["unique_frame_count"],
        "output_size": output_size,
        "output_scale_percent": output_scale_percent,
        "output_frame_size": metadata["output_frame_size"],
        "crop": crop,
        "images": output_files,
        "animation_indices": metadata["animation_indices"],
        "animation_source_frames": metadata["animation_source_frames"],
        "animation_sequence": animation_sequence,
    }
    sequence_path.write_text(json.dumps(sequence, indent=2), encoding="utf-8")

    contact_sheet_path = out_dir / "contact_sheet_crop.jpg"
    make_contact_sheet(out_dir, contact_sheet_path)

    print(f"Exported frames: {out_dir}")
    print(f"Metadata: {metadata_path}")
    print(f"Animation sequence: {sequence_path}")
    print(f"Contact sheet: {contact_sheet_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crop a sprite frame directory with a shared rectangle.")
    parser.add_argument(
        "source",
        nargs="?",
        help=(
            "Spriteify output directory or frame directory. Defaults to the newest "
            "outputs/sprites-pymatting/* directory with frames_pymatting_fg."
        ),
    )
    parser.add_argument("--crop-json", help="Crop JSON exported/copied from viewer.html.")
    parser.add_argument("--x", type=int, help="Crop rectangle left coordinate.")
    parser.add_argument("--y", type=int, help="Crop rectangle top coordinate.")
    parser.add_argument("--width", type=int, help="Crop rectangle width.")
    parser.add_argument("--height", type=int, help="Crop rectangle height.")
    parser.add_argument("--start-frame", type=int, help="First source frame to export, 1-based.")
    parser.add_argument("--end-frame", type=int, help="Last source frame to export, 1-based and inclusive.")
    parser.add_argument("--frame-step", type=int, help="Export every Nth source frame inside the loop range.")
    parser.add_argument(
        "--sequence-frames",
        help=(
            "Comma-separated 1-based source frame sequence to export. "
            "Duplicates are stored once as images and referenced multiple times in JSON."
        ),
    )
    parser.add_argument(
        "--delete-frames",
        default="",
        help="Comma-separated 1-based frames/ranges to skip, e.g. '1,8,12-16'.",
    )
    parser.add_argument("--fps", type=float, help="Playback FPS to store in crop metadata.")
    parser.add_argument("--ping-pong", action="store_true", help="Store ping-pong playback intent in metadata.")
    parser.add_argument("--preserve-names", action="store_true", help="Keep original source frame filenames.")
    parser.add_argument("--scale", type=float, default=100.0, help="Legacy output image scale percent, used only when no exact output size is set.")
    parser.add_argument("--out-width", type=int, help="Exact output PNG width in pixels.")
    parser.add_argument("--out-height", type=int, help="Exact output PNG height in pixels.")
    parser.add_argument("--out-dir", help="Output frame directory. Defaults to a crop-specific sibling directory.")
    parser.add_argument("--overwrite", action="store_true", help="Allow writing into an existing non-empty output dir.")
    parser.add_argument(
        "--scale-json",
        action="store_true",
        help="Scale crop-json coordinates when its frame_size differs from the actual frames.",
    )
    return parser.parse_args()


def read_export_config(args: argparse.Namespace) -> tuple[dict[str, int], dict, dict[str, int] | None, float]:
    cli_output_size = output_size_from_args(args)
    if args.crop_json:
        data = json.loads(resolve_path(args.crop_json).read_text(encoding="utf-8"))
        crop_source = data.get("crop", data)
        crop = normalize_crop(crop_source)
        if "frame_size" in data:
            crop["json_frame_size"] = normalize_frame_size(data["frame_size"])
        output_size = cli_output_size or normalize_output_size(data.get("output_size") or data.get("output_frame_size"))
        return crop, data.get("timeline", {}), output_size, float(data.get("output_scale_percent", args.scale))

    values = {"x": args.x, "y": args.y, "width": args.width, "height": args.height}
    missing = [key for key, value in values.items() if value is None]
    if missing:
        raise SystemExit("Pass --crop-json or all of --x --y --width --height.")
    return {key: int(value) for key, value in values.items() if value is not None}, {}, cli_output_size, float(args.scale)


def normalize_crop(value: dict) -> dict[str, int]:
    try:
        width = value["width"] if "width" in value else value["w"]
        height = value["height"] if "height" in value else value["h"]
        return {
            "x": int(round(float(value["x"]))),
            "y": int(round(float(value["y"]))),
            "width": int(round(float(width))),
            "height": int(round(float(height))),
        }
    except KeyError as error:
        raise SystemExit(f"Crop JSON is missing key: {error.args[0]}") from error


def normalize_frame_size(value: dict) -> dict[str, int]:
    try:
        return {
            "width": int(round(float(value["width"]))),
            "height": int(round(float(value["height"]))),
        }
    except KeyError as error:
        raise SystemExit(f"frame_size JSON is missing key: {error.args[0]}") from error


def output_size_from_args(args: argparse.Namespace) -> dict[str, int] | None:
    if args.out_width is None and args.out_height is None:
        return None
    if args.out_width is None or args.out_height is None:
        raise SystemExit("Pass both --out-width and --out-height.")
    return {"width": args.out_width, "height": args.out_height}


def normalize_output_size(value) -> dict[str, int] | None:
    if not value:
        return None
    try:
        return {
            "width": int(round(float(value["width"]))),
            "height": int(round(float(value["height"]))),
        }
    except KeyError as error:
        raise SystemExit(f"output_size JSON is missing key: {error.args[0]}") from error


def validate_output_size(value: dict[str, int]) -> dict[str, int]:
    width = int(value["width"])
    height = int(value["height"])
    if width <= 0 or height <= 0:
        raise SystemExit("Output width and height must be positive.")
    return {"width": width, "height": height}


def resolve_output_size(crop: dict[str, int], output_size: dict[str, int] | None, scale_percent: float) -> dict[str, int]:
    if output_size:
        return validate_output_size(output_size)
    return scaled_size((crop["width"], crop["height"]), validate_output_scale_percent(scale_percent))


def maybe_scale_crop(crop: dict[str, int], frame_size: tuple[int, int], args: argparse.Namespace) -> dict[str, int]:
    json_frame_size = crop.pop("json_frame_size", None)
    if not json_frame_size:
        return crop

    json_size = (json_frame_size["width"], json_frame_size["height"])
    if json_size == frame_size:
        return crop

    if not args.scale_json:
        raise SystemExit(
            "Crop JSON frame_size does not match the actual frames "
            f"({json_size[0]}x{json_size[1]} vs {frame_size[0]}x{frame_size[1]}). "
            "Re-export from the final viewer or pass --scale-json."
        )

    scale_x = frame_size[0] / json_size[0]
    scale_y = frame_size[1] / json_size[1]
    return {
        "x": int(round(crop["x"] * scale_x)),
        "y": int(round(crop["y"] * scale_y)),
        "width": int(round(crop["width"] * scale_x)),
        "height": int(round(crop["height"] * scale_y)),
    }


def validate_crop(crop: dict[str, int], frame_size: tuple[int, int]) -> dict[str, int]:
    frame_width, frame_height = frame_size
    x, y, width, height = crop["x"], crop["y"], crop["width"], crop["height"]
    if width <= 0 or height <= 0:
        raise SystemExit("Crop width and height must be positive.")
    if x < 0 or y < 0:
        raise SystemExit("Crop x and y must be zero or greater.")
    if x + width > frame_width or y + height > frame_height:
        raise SystemExit(
            f"Crop {x},{y},{width},{height} exceeds frame size {frame_width}x{frame_height}."
        )
    return {"x": x, "y": y, "width": width, "height": height}


def validate_output_scale_percent(value: float) -> float:
    scale = float(value)
    if scale <= 0 or scale > 100:
        raise SystemExit("--scale must be greater than 0 and no more than 100.")
    return scale


def scaled_size(size: tuple[int, int], scale_percent: float) -> dict[str, int]:
    width, height = size
    if scale_percent == 100:
        return {"width": width, "height": height}
    scale = scale_percent / 100.0
    return {"width": max(1, round(width * scale)), "height": max(1, round(height * scale))}


def resize_rgba_for_export(image: Image.Image, output_size: dict[str, int]) -> Image.Image:
    next_size = (output_size["width"], output_size["height"])
    if next_size == image.size:
        return image
    scale = min(next_size[0] / image.width, next_size[1] / image.height)
    resized_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    # ponytail: Pillow RGBa is premultiplied alpha; use it instead of owning a fragile pixel loop.
    resized = image.convert("RGBa").resize(resized_size, Image.Resampling.LANCZOS).convert("RGBA")
    canvas = Image.new("RGBA", next_size, (0, 0, 0, 0))
    canvas.alpha_composite(resized, ((next_size[0] - resized_size[0]) // 2, (next_size[1] - resized_size[1]) // 2))
    return canvas


def resolve_frame_dir(value: str | None) -> tuple[Path, Path]:
    if value:
        source = resolve_path(value)
        if not source.exists():
            raise SystemExit(f"Source does not exist: {source}")
    else:
        source = newest_sprite_root()

    if source.is_dir() and any(source.glob(PNG_GLOB)):
        return source, source.parent

    frame_dir = source / DEFAULT_FRAME_DIR_NAME
    if frame_dir.is_dir() and any(frame_dir.glob(PNG_GLOB)):
        return frame_dir, source

    raise SystemExit(
        f"Source must be a sprite root containing {DEFAULT_FRAME_DIR_NAME}/ or a frame directory: {source}"
    )


def newest_sprite_root() -> Path:
    root = resolve_path(DEFAULT_OUTPUT_ROOT)
    candidates = [
        item
        for item in root.glob("*")
        if item.is_dir() and (item / DEFAULT_FRAME_DIR_NAME).is_dir() and any((item / DEFAULT_FRAME_DIR_NAME).glob(PNG_GLOB))
    ]
    if not candidates:
        raise SystemExit(f"No sprite outputs found under {root}. Pass a source path.")
    return max(candidates, key=lambda item: item.stat().st_mtime)


def resolve_timeline(args: argparse.Namespace, timeline: dict, frame_count: int) -> dict:
    start = args.start_frame if args.start_frame is not None else timeline.get("loop_start", 1)
    end = args.end_frame if args.end_frame is not None else timeline.get("loop_end", frame_count)
    frame_step = args.frame_step if args.frame_step is not None else timeline.get("frame_step", 1)
    sequence_frames = args.sequence_frames if args.sequence_frames is not None else timeline.get("sequence_frames")
    fps = args.fps if args.fps is not None else timeline.get("fps", 24)
    deleted = set(parse_frame_ranges(timeline.get("deleted_frames", []), frame_count))
    deleted.update(parse_frame_ranges(args.delete_frames, frame_count))
    ping_pong = bool(args.ping_pong or timeline.get("ping_pong", False))

    start = int(start)
    end = int(end)
    frame_step = int(frame_step)
    if start < 1 or end < 1:
        raise SystemExit("Frame range is 1-based; start/end must be at least 1.")
    if start > end:
        raise SystemExit("--start-frame must be less than or equal to --end-frame.")
    if end > frame_count:
        raise SystemExit(f"--end-frame {end} exceeds available frame count {frame_count}.")
    if frame_step < 1:
        raise SystemExit("--frame-step must be at least 1.")
    if float(fps) <= 0:
        raise SystemExit("--fps must be positive.")

    return {
        "loop_start": start,
        "loop_end": end,
        "frame_step": frame_step,
        "sequence_frames": parse_sequence_frames(sequence_frames, frame_count) if sequence_frames is not None else None,
        "fps": float(fps),
        "ping_pong": ping_pong,
        "deleted_frames": sorted(deleted),
    }


def parse_frame_ranges(value, frame_count: int) -> set[int]:
    if value in (None, ""):
        return set()
    if isinstance(value, int):
        values = [str(value)]
    elif isinstance(value, list):
        values = [str(item) for item in value]
    else:
        values = str(value).replace(" ", "").split(",")

    frames: set[int] = set()
    for item in values:
        if not item:
            continue
        if "-" in item:
            start_text, end_text = item.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                start, end = end, start
            frames.update(range(start, end + 1))
        else:
            frames.add(int(item))

    invalid = [item for item in frames if item < 1 or item > frame_count]
    if invalid:
        raise SystemExit(f"Deleted frame number out of range 1..{frame_count}: {invalid[0]}")
    return frames


def parse_sequence_frames(value, frame_count: int) -> list[int]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        values = value
    else:
        values = str(value).replace(" ", "").split(",")

    sequence = []
    for item in values:
        if item == "":
            continue
        frame_number = int(item)
        if frame_number < 1 or frame_number > frame_count:
            raise SystemExit(f"Sequence frame out of range 1..{frame_count}: {frame_number}")
        sequence.append(frame_number)
    return sequence


def select_frames(frames: list[Path], timeline: dict) -> list[tuple[int, Path]]:
    if timeline["sequence_frames"] is not None:
        return [(source_number, frames[source_number - 1]) for source_number in timeline["sequence_frames"]]

    deleted = set(timeline["deleted_frames"])
    selected = []
    for source_number in range(timeline["loop_start"], timeline["loop_end"] + 1):
        if (source_number - timeline["loop_start"]) % timeline["frame_step"] != 0:
            continue
        if source_number in deleted:
            continue
        selected.append((source_number, frames[source_number - 1]))
    return selected


def default_out_dir(root_dir: Path, frame_dir: Path, crop: dict[str, int], timeline: dict, output_size: dict[str, int]) -> Path:
    if timeline.get("sequence_frames") is not None:
        sequence = timeline["sequence_frames"]
        sequence_suffix = f"_seq{len(sequence)}_unique{len(set(sequence))}"
    else:
        sequence_suffix = ""
    trim_suffix = (
        f"_f{timeline['loop_start']}-{timeline['loop_end']}"
        if timeline["loop_start"] != 1 or timeline["loop_end"] != len(list(frame_dir.glob(PNG_GLOB)))
        else ""
    )
    step_suffix = f"_step{timeline['frame_step']}" if timeline["frame_step"] > 1 else ""
    delete_suffix = f"_drop{len(timeline['deleted_frames'])}" if timeline["deleted_frames"] else ""
    output_suffix = (
        f"_out{output_size['width']}x{output_size['height']}"
        if output_size != {"width": crop["width"], "height": crop["height"]}
        else ""
    )
    name = (
        f"{frame_dir.name}_crop_"
        f"x{crop['x']}_y{crop['y']}_w{crop['width']}_h{crop['height']}"
        f"{trim_suffix}{step_suffix}{delete_suffix}{sequence_suffix}{output_suffix}"
    )
    return root_dir / name


def prepare_output_dir(path: Path, overwrite: bool) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if not any(path.iterdir()):
        return
    if not overwrite:
        raise SystemExit(f"Output dir is not empty: {path}. Pass --overwrite to replace files.")
    for old_frame in path.glob(PNG_GLOB):
        if old_frame.is_file():
            old_frame.unlink()
    for name in GENERATED_OUTPUT_FILES:
        (path / name).unlink(missing_ok=True)


def make_contact_sheet(frame_dir: Path, out_path: Path) -> None:
    frames = sorted(frame_dir.glob(PNG_GLOB))
    if not frames:
        return

    sample_count = min(12, len(frames))
    indices = evenly_spaced_indices(len(frames), sample_count)
    thumb_w, thumb_h = 240, 240
    columns = 4
    rows = (sample_count + columns - 1) // columns
    sheet = Image.new("RGB", (columns * thumb_w, rows * thumb_h), (28, 30, 34))
    for sheet_index, frame_index in enumerate(indices):
        rgba = Image.open(frames[frame_index]).convert("RGBA")
        bg = checkerboard(rgba.size)
        bg.alpha_composite(rgba)
        thumb = ImageOps.contain(bg.convert("RGB"), (thumb_w, thumb_h), method=Image.Resampling.LANCZOS)
        x = (sheet_index % columns) * thumb_w
        y = (sheet_index // columns) * thumb_h
        sheet.paste(thumb, (x, y))
    sheet.save(out_path, quality=92)


def evenly_spaced_indices(total: int, count: int) -> list[int]:
    if count <= 1:
        return [0]
    return [round(index * (total - 1) / (count - 1)) for index in range(count)]


def checkerboard(size: tuple[int, int]) -> Image.Image:
    width, height = size
    tile = 24
    image = Image.new("RGBA", size, (38, 43, 49, 255))
    light = Image.new("RGBA", (tile, tile), (72, 78, 86, 255))
    for y in range(0, height, tile):
        for x in range(0, width, tile):
            if (x // tile + y // tile) % 2:
                image.paste(light, (x, y))
    return image


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
