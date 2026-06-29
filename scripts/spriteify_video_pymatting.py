#!/usr/bin/env python3
"""Convert a Google/Flow magenta-background MP4 into transparent sprite frames.

The standard project pipeline is:

1. Extract raw video frames.
2. Use FFmpeg chromakey to get a clean alpha matte.
3. Use PyMatting foreground estimation to recover foreground RGB and suppress
   purple/magenta edge spill.

The output `frames_pymatting_fg/` is the preferred sprite frame set.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from pymatting import estimate_foreground_ml


DEFAULT_SEARCH_DIR = Path("outputs/google-flow")
DEFAULT_OUTPUT_ROOT = Path("outputs/sprites-pymatting")
PNG_GLOB = "frame_*.png"
FRAME_PATTERN = "frame_%04d.png"


def main() -> int:
    args = parse_args()
    video_path = resolve_video_path(args.video)
    out_dir = resolve_path(args.out_dir) if args.out_dir else DEFAULT_OUTPUT_ROOT / slugify(video_path.stem)
    raw_dir = out_dir / "frames_raw"
    keyed_dir = out_dir / "frames_chromakey_alpha"
    sprite_dir = out_dir / "frames_pymatting_fg"
    metadata_path = out_dir / "spriteify-meta.json"
    contact_sheet_path = out_dir / "contact_sheet_pymatting_fg.jpg"

    ffmpeg = require_binary("ffmpeg")
    reset_dir(raw_dir)
    reset_dir(keyed_dir)
    reset_dir(sprite_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    extract_raw_frames(ffmpeg, video_path, raw_dir, args.max_frames)
    extract_chromakey_alpha(ffmpeg, video_path, keyed_dir, args)

    raw_frames = sorted(raw_dir.glob(PNG_GLOB))
    keyed_frames = sorted(keyed_dir.glob(PNG_GLOB))
    if len(raw_frames) != len(keyed_frames):
        raise SystemExit(f"Raw/keyed frame count mismatch: {len(raw_frames)} vs {len(keyed_frames)}")
    if not raw_frames:
        raise SystemExit("No frames were extracted.")

    stats = []
    for index, (raw_path, keyed_path) in enumerate(zip(raw_frames, keyed_frames, strict=True)):
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
        Image.fromarray((rgba * 255.0).astype(np.uint8), mode="RGBA").save(sprite_dir / raw_path.name)
        stats.append(
            {
                "semi_alpha_pixels": int(np.count_nonzero((alpha > 0.0) & (alpha < 1.0))),
                "opaque_pixels": int(np.count_nonzero(alpha >= 1.0)),
                "transparent_pixels": int(np.count_nonzero(alpha <= 0.0)),
            }
        )
        if index == 0 or (index + 1) % 25 == 0 or index == len(raw_frames) - 1:
            print(f"Processed {index + 1}/{len(raw_frames)}")

    make_contact_sheet(sprite_dir, contact_sheet_path)
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_video": str(video_path),
        "out_dir": str(out_dir),
        "raw_dir": str(raw_dir),
        "chromakey_alpha_dir": str(keyed_dir),
        "sprite_dir": str(sprite_dir),
        "frame_count": len(raw_frames),
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
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    write_viewer(out_dir, len(raw_frames), sprite_dir)

    print(f"Sprite frames: {sprite_dir}")
    print(f"Viewer: {out_dir / 'viewer.html'}")
    print(f"Metadata: {metadata_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Spriteify a magenta-background Google/Flow MP4 with PyMatting FG.")
    parser.add_argument("video", nargs="?", help="Input MP4. Defaults to newest .mp4 under outputs/google-flow.")
    parser.add_argument("--out-dir", help="Output directory. Defaults to outputs/sprites-pymatting/<video-slug>.")
    parser.add_argument("--bg-color", default="0xff00ff", help="FFmpeg chromakey color, e.g. 0xff00ff.")
    parser.add_argument("--similarity", type=float, default=0.24, help="FFmpeg chromakey similarity.")
    parser.add_argument("--blend", type=float, default=0.10, help="FFmpeg chromakey blend.")
    parser.add_argument("--max-frames", type=int, help="Process only the first N frames.")
    parser.add_argument("--pymatting-regularization", type=float, default=1e-5)
    parser.add_argument("--pymatting-small-iterations", type=int, default=6)
    parser.add_argument("--pymatting-big-iterations", type=int, default=1)
    parser.add_argument("--pymatting-small-size", type=int, default=64)
    parser.add_argument("--pymatting-gradient-weight", type=float, default=1.0)
    return parser.parse_args()


def extract_raw_frames(ffmpeg: str, video_path: Path, raw_dir: Path, max_frames: int | None) -> None:
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_path)]
    if max_frames is not None:
        command += ["-frames:v", str(max_frames)]
    command.append(str(raw_dir / FRAME_PATTERN))
    subprocess.run(command, check=True)


def extract_chromakey_alpha(ffmpeg: str, video_path: Path, keyed_dir: Path, args: argparse.Namespace) -> None:
    filter_expr = f"chromakey={args.bg_color}:{args.similarity}:{args.blend},format=rgba"
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_path), "-vf", filter_expr]
    if args.max_frames is not None:
        command += ["-frames:v", str(args.max_frames)]
    command.append(str(keyed_dir / FRAME_PATTERN))
    subprocess.run(command, check=True)


def make_contact_sheet(frame_dir: Path, out_path: Path) -> None:
    frames = sorted(frame_dir.glob(PNG_GLOB))
    if not frames:
        return
    sample_count = min(12, len(frames))
    indices = np.linspace(0, len(frames) - 1, sample_count, dtype=int)
    thumb_w, thumb_h = 320, 180
    columns = 4
    rows = int(np.ceil(sample_count / columns))
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


def write_viewer(out_dir: Path, frame_count: int, sprite_dir: Path) -> None:
    sprite_frames = sorted(sprite_dir.glob(PNG_GLOB))
    first_frame = next(iter(sprite_frames), None)
    if first_frame is None:
        return
    with Image.open(first_frame) as image:
        frame_width, frame_height = image.size

    config = json.dumps(
        {
            "frameCount": frame_count,
            "frameWidth": frame_width,
            "frameHeight": frame_height,
            "frameDir": sprite_dir.name,
            "frames": [path.name for path in sprite_frames],
            "outDir": str(out_dir.resolve()),
            "projectDir": str(Path.cwd().resolve()),
        }
    )
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PyMatting Sprite Viewer</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #17191c;
      --panel: #22262b;
      --panel-2: #2b3037;
      --text: #edf0f3;
      --muted: #aeb7c2;
      --line: #3c434d;
      --accent: #4cc9a8;
      --accent-soft: rgb(76 201 168 / 18%);
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }

    .app {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      min-height: 100vh;
    }

    header,
    footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 16px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
    }

    footer {
      border-top: 1px solid var(--line);
      border-bottom: 0;
      display: grid;
      grid-template-columns: 1fr;
      align-items: stretch;
      gap: 10px;
    }

    h1 {
      margin: 0;
      font-size: 15px;
      font-weight: 650;
    }

    main {
      min-height: 0;
      display: grid;
      place-items: center;
      padding: 14px;
    }

    .stage {
      width: min(100%, 1280px);
      max-width: 1280px;
      aspect-ratio: 16 / 9;
      position: relative;
      overflow: hidden;
      border: 1px solid var(--line);
      background-color: #33383f;
      background-image:
        linear-gradient(45deg, #252a30 25%, transparent 25%),
        linear-gradient(-45deg, #252a30 25%, transparent 25%),
        linear-gradient(45deg, transparent 75%, #252a30 75%),
        linear-gradient(-45deg, transparent 75%, #252a30 75%);
      background-position: 0 0, 0 12px, 12px -12px, -12px 0;
      background-size: 24px 24px;
      touch-action: none;
    }

    img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
      user-select: none;
      -webkit-user-drag: none;
    }

    .crop {
      position: absolute;
      display: none;
      border: 2px solid var(--accent);
      background: var(--accent-soft);
      box-shadow: 0 0 0 9999px rgb(0 0 0 / 28%);
      pointer-events: none;
    }

    .controls {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }

    button,
    input,
    select {
      height: 34px;
      border: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--text);
      border-radius: 6px;
      font: inherit;
      font-size: 13px;
    }

    button {
      min-width: 68px;
      padding: 0 10px;
      cursor: pointer;
    }

    button.primary {
      border-color: color-mix(in srgb, var(--accent), #000 24%);
      background: color-mix(in srgb, var(--accent), #1d2428 70%);
    }

    label {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }

    input[type="number"] {
      width: 76px;
      padding: 0 8px;
      font-variant-numeric: tabular-nums;
    }

    input[type="text"],
    select {
      height: 34px;
      min-width: 160px;
      padding: 0 8px;
    }

    .project-meta {
      max-width: min(100%, 960px);
      overflow: hidden;
      text-overflow: ellipsis;
    }

    #exportDir,
    #viewerSelect {
      min-width: min(62vw, 520px);
    }

    #exportDirSelect {
      min-width: min(40vw, 360px);
    }

    input[type="checkbox"] {
      width: 18px;
      height: 18px;
      accent-color: var(--accent);
    }

    input[type="range"] {
      width: min(40vw, 480px);
      min-width: 200px;
      accent-color: var(--accent);
    }

    .meta {
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }

    .timeline {
      display: flex;
      gap: 4px;
      align-items: flex-start;
      overflow-x: auto;
      padding: 2px 0 4px;
      scrollbar-color: var(--line) transparent;
    }

    .frame-slot {
      flex: 0 0 42px;
      min-width: 42px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 4px;
    }

    .frame-header {
      width: 42px;
      min-width: 42px;
      height: 24px;
      padding: 0;
      border-radius: 4px;
      font-variant-numeric: tabular-nums;
      color: var(--muted);
    }

    .frame-header[draggable="true"] {
      cursor: grab;
    }

    .frame-header[draggable="true"]:active {
      cursor: grabbing;
    }

    .frame-toggle {
      width: 22px;
      min-width: 22px;
      height: 22px;
      padding: 0;
      border-radius: 3px;
    }

    .frame-toggle.enabled {
      background: var(--accent);
      border-color: var(--accent);
    }

    .frame-toggle.disabled {
      background: transparent;
      opacity: 0.55;
    }

    .frame-slot.in-loop .frame-header {
      color: var(--text);
      border-color: color-mix(in srgb, var(--accent), var(--line) 58%);
    }

    .frame-slot.active .frame-header {
      background: var(--accent);
      border-color: var(--accent);
      color: #102019;
      font-weight: 700;
    }

    .frame-slot.selected .frame-header {
      outline: 2px solid #d8b45f;
      outline-offset: 2px;
    }

    .frame-slot.disabled .frame-header {
      opacity: 0.42;
      border-style: dashed;
    }

    .frame-slot.hidden-deselected {
      display: none;
    }

    .frame-slot.drop-target .frame-header {
      outline: 2px solid #d8b45f;
      outline-offset: 2px;
    }

    .frame-slot.duplicate:not(.active) .frame-header {
      box-shadow: inset 0 -3px 0 #d8b45f;
    }

    @media (max-width: 840px) {
      header,
      footer {
        align-items: flex-start;
        flex-direction: column;
      }

      main {
        padding: 8px;
      }

      .stage,
      input[type="range"],
      .controls {
        width: 100%;
      }

      .frame-slot {
        flex-basis: 38px;
        min-width: 38px;
      }

      .frame-header {
        width: 38px;
        min-width: 38px;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <header>
      <h1>PyMatting Sprite Viewer</h1>
      <div class="meta"><span id="label">Loading</span> / <span id="frameTotal"></span> frames</div>
    </header>
    <main>
      <div class="stage" id="stage">
        <img id="frame" src="" alt="PyMatting sprite frame">
        <div class="crop" id="cropOverlay"></div>
      </div>
    </main>
    <footer>
      <div class="controls">
        <button class="primary" id="play" type="button">Pause</button>
        <button id="prev" type="button">Prev</button>
        <button id="next" type="button">Next</button>
        <input id="scrub" type="range" min="0" value="0" step="1">
        <label>FPS <input id="fps" type="number" min="1" max="60" value="24" step="1"></label>
        <label>Every <input id="frameStep" type="number" min="1" max="96" value="1" step="1"></label>
        <label><input id="pingPong" type="checkbox"> Ping Pong</label>
        <label><input id="loopSelected" type="checkbox"> Loop Selected</label>
        <label><input id="hideDeselected" type="checkbox"> Hide Off</label>
        <span class="meta" id="status">Ready</span>
      </div>
      <div class="controls">
        <label>Start <input id="loopStart" type="number" min="1" value="1" step="1"></label>
        <button id="setLoopStart" type="button">Set Start</button>
        <label>End <input id="loopEnd" type="number" min="1" value="1" step="1"></label>
        <button id="setLoopEnd" type="button">Set End</button>
        <button id="duplicateFrame" type="button">Duplicate</button>
        <button id="moveLeft" type="button">Move Left</button>
        <button id="moveRight" type="button">Move Right</button>
        <button id="sendEnd" type="button">To End</button>
        <button id="reverseSelected" type="button">Reverse</button>
        <button id="toggleDelete" type="button">Delete Slot</button>
        <button id="restoreAll" type="button">Restore All</button>
      </div>
      <div class="timeline" id="timeline" aria-label="Frame timeline"></div>
      <div class="controls">
        <label>Pad <input id="pad" type="number" min="0" max="256" value="16" step="1"></label>
        <button id="fitCurrent" type="button">Fit Frame</button>
        <button id="fitAll" type="button">Fit All</button>
        <button id="fullCrop" type="button">Full</button>
        <button id="copyCommand" type="button">Copy Cmd</button>
        <label>Export <input id="exportDir" type="text" value="exports/viewer-project"></label>
        <select id="exportDirSelect" aria-label="Export folders"></select>
        <label><input id="overwriteExport" type="checkbox"> Overwrite</label>
        <button id="exportCropSequence" type="button">Export Crop+Sequence</button>
        <button id="openExportFolder" type="button">Open Finder</button>
      </div>
      <div class="controls">
        <label>Folder <select id="viewerSelect" aria-label="Sprite viewer folders"></select></label>
        <button id="refreshViewers" type="button">Refresh Folders</button>
        <button id="openViewerProject" type="button">New Project</button>
      </div>
      <div class="controls">
        <label>Project <input id="projectName" type="text" value="viewer-project"></label>
        <select id="projectSelect" aria-label="Saved projects"></select>
        <button id="refreshProjects" type="button">Refresh</button>
        <button id="saveProject" type="button">Save Project</button>
        <button id="saveProjectAs" type="button">Save As</button>
        <button id="loadProject" type="button">Load Project</button>
      </div>
      <div class="controls">
        <span class="meta project-meta">Work <span id="workFolder">Loading</span></span>
        <span class="meta project-meta">Projects <span id="projectFolder">Loading</span></span>
      </div>
      <div class="controls">
        <label>X <input id="cropX" type="number" min="0" step="1"></label>
        <label>Y <input id="cropY" type="number" min="0" step="1"></label>
        <label>W <input id="cropW" type="number" min="1" step="1"></label>
        <label>H <input id="cropH" type="number" min="1" step="1"></label>
      </div>
    </footer>
  </div>
  <script>
    const config = __CONFIG__;
    const frameCount = config.frameCount;
    const frameNames = config.frames;
    const frame = document.getElementById("frame");
    const stage = document.getElementById("stage");
    const cropOverlay = document.getElementById("cropOverlay");
    const play = document.getElementById("play");
    const prev = document.getElementById("prev");
    const next = document.getElementById("next");
    const scrub = document.getElementById("scrub");
    const label = document.getElementById("label");
    const status = document.getElementById("status");
    const timeline = document.getElementById("timeline");
    const fpsInput = document.getElementById("fps");
    const frameStepInput = document.getElementById("frameStep");
    const pingPongInput = document.getElementById("pingPong");
    const loopSelectedInput = document.getElementById("loopSelected");
    const hideDeselectedInput = document.getElementById("hideDeselected");
    const loopStartInput = document.getElementById("loopStart");
    const loopEndInput = document.getElementById("loopEnd");
    const duplicateFrameButton = document.getElementById("duplicateFrame");
    const moveLeftButton = document.getElementById("moveLeft");
    const moveRightButton = document.getElementById("moveRight");
    const sendEndButton = document.getElementById("sendEnd");
    const reverseSelectedButton = document.getElementById("reverseSelected");
    const toggleDeleteButton = document.getElementById("toggleDelete");
    const projectNameInput = document.getElementById("projectName");
    const projectSelect = document.getElementById("projectSelect");
    const viewerSelect = document.getElementById("viewerSelect");
    const workFolder = document.getElementById("workFolder");
    const projectFolder = document.getElementById("projectFolder");
    const exportDirInput = document.getElementById("exportDir");
    const exportDirSelect = document.getElementById("exportDirSelect");
    const overwriteExportInput = document.getElementById("overwriteExport");
    const padInput = document.getElementById("pad");
    const cropInputs = {
      x: document.getElementById("cropX"),
      y: document.getElementById("cropY"),
      width: document.getElementById("cropW"),
      height: document.getElementById("cropH"),
    };
    let index = 0;
    let playing = true;
    let lastTick = performance.now();
    let loopStart = 1;
    let loopEnd = frameCount;
    let playbackDirection = 1;
    let sequenceFrames = Array.from({ length: frameCount }, (_, i) => i + 1);
    let slotEnabled = Array.from({ length: frameCount }, () => true);
    let sequenceCursor = 0;
    let selectedSlotIndices = new Set([0]);
    let lastClickedSlotIndex = 0;
    let draggedSlotIndex = null;
    const frameSlots = [];

    stage.style.aspectRatio = `${config.frameWidth} / ${config.frameHeight}`;

    function resizeStage() {
      const availableWidth = Math.max(240, window.innerWidth - 28);
      const availableHeight = Math.max(240, window.innerHeight - 184);
      const width = Math.min(1280, availableWidth, availableHeight * config.frameWidth / config.frameHeight);
      stage.style.width = `${Math.max(240, Math.floor(width))}px`;
    }
    window.addEventListener("resize", resizeStage);

    let crop = {
      x: 0,
      y: 0,
      width: config.frameWidth,
      height: config.frameHeight,
    };

    function path(i) {
      return `${config.frameDir}/${frameNames[i]}`;
    }

    function displaySourceFrame(i) {
      index = (i + frameCount) % frameCount;
      frame.src = path(index);
      scrub.value = String(sequenceCursor);
      const sourceFrame = index + 1;
      const state = slotEnabled[sequenceCursor] ? "on" : "off";
      label.textContent = `${frameNames[index]} (slot ${sequenceCursor + 1}/${sequenceFrames.length}, source ${sourceFrame}/${frameCount}, ${state})`;
      updateTimeline();
    }

    function setSequenceCursor(cursor) {
      if (!sequenceFrames.length) {
        return;
      }
      sequenceCursor = (cursor + sequenceFrames.length) % sequenceFrames.length;
      displaySourceFrame(sequenceFrames[sequenceCursor] - 1);
    }

    function setFrame(i) {
      const sourceFrame = ((i + frameCount) % frameCount) + 1;
      const found = sequenceFrames.indexOf(sourceFrame);
      sequenceCursor = found >= 0 ? found : clamp(sequenceCursor, 0, sequenceFrames.length - 1);
      displaySourceFrame(sourceFrame - 1);
    }

    function selectedIndices() {
      const valid = Array.from(selectedSlotIndices)
        .filter((slotIndex) => Number.isInteger(slotIndex) && slotIndex >= 0 && slotIndex < sequenceFrames.length)
        .sort((a, b) => a - b);
      if (valid.length) {
        return valid;
      }
      return [clamp(sequenceCursor, 0, sequenceFrames.length - 1)];
    }

    function isSlotVisible(slotIndex) {
      return !(hideDeselectedInput.checked && !slotEnabled[slotIndex]);
    }

    function actionSelectedIndices() {
      const selected = selectedIndices();
      if (!hideDeselectedInput.checked) {
        return selected;
      }
      const visible = selected.filter((slotIndex) => isSlotVisible(slotIndex));
      return visible.length ? visible : [clamp(sequenceCursor, 0, sequenceFrames.length - 1)];
    }

    function setSelectedIndices(indices, cursor = sequenceCursor) {
      const next = indices
        .map((slotIndex) => Math.round(Number(slotIndex)))
        .filter((slotIndex, position, list) => (
          Number.isInteger(slotIndex) &&
          slotIndex >= 0 &&
          slotIndex < sequenceFrames.length &&
          list.indexOf(slotIndex) === position
        ));
      selectedSlotIndices = new Set(next.length ? next : [clamp(cursor, 0, sequenceFrames.length - 1)]);
      lastClickedSlotIndex = clamp(cursor, 0, sequenceFrames.length - 1);
    }

    function selectSlotFromClick(slotIndex, event) {
      const cursor = clamp(slotIndex, 0, sequenceFrames.length - 1);
      if (event.metaKey || event.ctrlKey) {
        event.preventDefault();
        const next = selectedIndices();
        const existing = next.indexOf(cursor);
        if (existing >= 0) {
          slotEnabled[cursor] = !slotEnabled[cursor];
          if (!slotEnabled[cursor] && next.length > 1) {
            next.splice(existing, 1);
          }
        } else if (existing < 0) {
          next.push(cursor);
          slotEnabled[cursor] = true;
        }
        setSelectedIndices(next, cursor);
      } else if (event.shiftKey) {
        const anchor = clamp(lastClickedSlotIndex ?? sequenceCursor, 0, sequenceFrames.length - 1);
        const start = Math.min(anchor, cursor);
        const end = Math.max(anchor, cursor);
        let range = Array.from({ length: end - start + 1 }, (_, i) => start + i);
        if (hideDeselectedInput.checked) {
          range = range.filter((slotIndex) => isSlotVisible(slotIndex));
        }
        setSelectedIndices(range, cursor);
      } else {
        setSelectedIndices([cursor], cursor);
      }
      playing = false;
      play.textContent = "Play";
      setSequenceCursor(cursor);
    }

    function clamp(value, min, max) {
      return Math.min(Math.max(value, min), max);
    }

    function setCrop(nextCrop) {
      const x = clamp(Math.round(nextCrop.x), 0, config.frameWidth - 1);
      const y = clamp(Math.round(nextCrop.y), 0, config.frameHeight - 1);
      const width = clamp(Math.round(nextCrop.width), 1, config.frameWidth - x);
      const height = clamp(Math.round(nextCrop.height), 1, config.frameHeight - y);
      crop = { x, y, width, height };
      cropInputs.x.value = String(x);
      cropInputs.y.value = String(y);
      cropInputs.width.value = String(width);
      cropInputs.height.value = String(height);
      renderCrop();
    }

    function setLoopRange(nextStart, nextEnd) {
      loopStart = clamp(Math.round(nextStart), 1, sequenceFrames.length);
      loopEnd = clamp(Math.round(nextEnd), 1, sequenceFrames.length);
      if (loopStart > loopEnd) {
        if (document.activeElement === loopStartInput) {
          loopEnd = loopStart;
        } else {
          loopStart = loopEnd;
        }
      }
      loopStartInput.value = String(loopStart);
      loopEndInput.value = String(loopEnd);
      syncPlaybackDirectionAtEndpoint();
      updateTimeline();
    }

    function syncSequenceControls() {
      const sequenceLength = sequenceFrames.length;
      while (slotEnabled.length < sequenceLength) {
        slotEnabled.push(true);
      }
      if (slotEnabled.length > sequenceLength) {
        slotEnabled.length = sequenceLength;
      }
      loopStart = clamp(loopStart, 1, sequenceLength);
      loopEnd = clamp(loopEnd, 1, sequenceLength);
      if (loopStart > loopEnd) {
        loopEnd = loopStart;
      }
      sequenceCursor = clamp(sequenceCursor, 0, sequenceLength - 1);
      setSelectedIndices(selectedIndices(), sequenceCursor);
      scrub.max = String(sequenceLength - 1);
      loopStartInput.max = String(sequenceLength);
      loopEndInput.max = String(sequenceLength);
      frameStepInput.max = String(sequenceLength);
      loopStartInput.value = String(loopStart);
      loopEndInput.value = String(loopEnd);
      document.getElementById("frameTotal").textContent = `${frameCount} source / ${sequenceLength} slots`;
    }

    function frameStep() {
      return clamp(Math.round(Number(frameStepInput.value) || 1), 1, sequenceFrames.length);
    }

    function usesSlot(slotPosition) {
      return slotPosition >= loopStart &&
        slotPosition <= loopEnd &&
        slotEnabled[slotPosition - 1];
    }

    function exportSlots() {
      const slots = [];
      for (let slotPosition = loopStart; slotPosition <= loopEnd; slotPosition += 1) {
        if (usesSlot(slotPosition)) {
          slots.push(slotPosition - 1);
        }
      }
      return slots;
    }

    function previewSlots() {
      if (!loopSelectedInput.checked) {
        return exportSlots();
      }
      return selectedIndices().filter((slotIndex) => slotEnabled[slotIndex]);
    }

    function previewFrames() {
      return previewSlots().map((slotIndex) => sequenceFrames[slotIndex] - 1);
    }

    function exportSourceFrameNumbers() {
      return exportSlots().map((slotIndex) => sequenceFrames[slotIndex]);
    }

    function applyEverySelection() {
      const step = frameStep();
      for (let i = 0; i < slotEnabled.length; i += 1) {
        const slotPosition = i + 1;
        slotEnabled[i] =
          slotPosition >= loopStart &&
          slotPosition <= loopEnd &&
          (slotPosition - loopStart) % step === 0;
      }
      playing = false;
      play.textContent = "Play";
      syncPlaybackDirectionAtEndpoint();
      updateTimeline();
      status.textContent = `Selected every ${step}`;
    }

    function syncPlaybackDirectionAtEndpoint() {
      const active = previewSlots();
      if (!active.length) {
        return;
      }
      const cursor = active.indexOf(sequenceCursor);
      if (cursor === 0) {
        playbackDirection = 1;
      } else if (cursor === active.length - 1) {
        playbackDirection = -1;
      }
    }

    function stepFrame(delta) {
      const active = previewSlots();
      if (!active.length) {
        status.textContent = "No active frames";
        return;
      }
      playbackDirection = delta >= 0 ? 1 : -1;
      let cursor = active.indexOf(sequenceCursor);
      if (cursor < 0) {
        cursor = delta > 0 ? -1 : 0;
      }
      const nextCursor = (cursor + delta + active.length) % active.length;
      setSequenceCursor(active[nextCursor]);
    }

    function advancePlayback() {
      const active = previewSlots();
      if (!active.length) {
        status.textContent = "No active frames";
        return;
      }
      let cursor = active.indexOf(sequenceCursor);
      if (cursor < 0) {
        playbackDirection = 1;
        setSequenceCursor(active[0]);
        return;
      }
      if (!pingPongInput.checked || active.length <= 1) {
        setSequenceCursor(active[(cursor + 1) % active.length]);
        return;
      }
      let nextCursor = cursor + playbackDirection;
      if (nextCursor >= active.length) {
        playbackDirection = -1;
        nextCursor = active.length - 2;
      } else if (nextCursor < 0) {
        playbackDirection = 1;
        nextCursor = 1;
      }
      setSequenceCursor(active[nextCursor]);
    }

    function renderTimeline() {
      timeline.replaceChildren();
      frameSlots.length = 0;
      for (let slotIndex = 0; slotIndex < sequenceFrames.length; slotIndex += 1) {
        const sourceFrame = sequenceFrames[slotIndex];
        const slot = document.createElement("div");
        slot.className = "frame-slot";
        slot.addEventListener("dragover", (event) => {
          event.preventDefault();
          slot.classList.add("drop-target");
        });
        slot.addEventListener("dragleave", () => {
          slot.classList.remove("drop-target");
        });
        slot.addEventListener("drop", (event) => {
          event.preventDefault();
          slot.classList.remove("drop-target");
          const draggedData = event.dataTransfer.getData("text/plain");
          const from = draggedSlotIndex ?? (draggedData ? Number(draggedData) : NaN);
          if (Number.isInteger(from)) {
            moveSlotTo(from, slotIndex);
          }
        });

        const header = document.createElement("button");
        header.type = "button";
        header.className = "frame-header";
        header.draggable = true;
        header.textContent = String(sourceFrame);
        header.title = `slot ${slotIndex + 1}, source ${sourceFrame}: ${frameNames[sourceFrame - 1]}`;
        header.addEventListener("click", (event) => {
          selectSlotFromClick(slotIndex, event);
        });
        header.addEventListener("dragstart", (event) => {
          draggedSlotIndex = slotIndex;
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", String(slotIndex));
        });
        header.addEventListener("dragend", () => {
          draggedSlotIndex = null;
          for (const item of frameSlots) {
            item.slot.classList.remove("drop-target");
          }
        });

        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "frame-toggle";
        toggle.setAttribute("aria-pressed", slotEnabled[slotIndex] ? "true" : "false");
        toggle.title = `slot ${slotIndex + 1}: ${slotEnabled[slotIndex] ? "included" : "excluded"} from preview/export`;
        toggle.addEventListener("click", () => {
          playing = false;
          play.textContent = "Play";
          slotEnabled[slotIndex] = !slotEnabled[slotIndex];
          setSelectedIndices([slotIndex], slotIndex);
          setSequenceCursor(slotIndex);
          syncPlaybackDirectionAtEndpoint();
          updateTimeline();
        });

        slot.append(header, toggle);
        timeline.append(slot);
        frameSlots.push({ slot, header, toggle });
      }
      syncSequenceControls();
      updateTimeline();
    }

    function resetSequence() {
      sequenceFrames = Array.from({ length: frameCount }, (_, i) => i + 1);
      slotEnabled = Array.from({ length: frameCount }, () => true);
      sequenceCursor = clamp(index, 0, frameCount - 1);
      setSelectedIndices([sequenceCursor], sequenceCursor);
      loopStart = 1;
      loopEnd = sequenceFrames.length;
      renderTimeline();
      setSequenceCursor(sequenceCursor);
    }

    function duplicateSelectedSlots() {
      const selected = actionSelectedIndices();
      const items = selected.map((slotIndex) => ({
        sourceFrame: sequenceFrames[slotIndex],
        enabled: slotEnabled[slotIndex],
      }));
      const insertAt = selected[selected.length - 1] + 1;
      sequenceFrames.splice(insertAt, 0, ...items.map((item) => item.sourceFrame));
      slotEnabled.splice(insertAt, 0, ...items.map((item) => item.enabled));
      if (loopEnd >= insertAt) {
        loopEnd += items.length;
      }
      const nextSelection = items.map((_, index) => insertAt + index);
      sequenceCursor = nextSelection[0];
      setSelectedIndices(nextSelection, sequenceCursor);
      renderTimeline();
      setSequenceCursor(sequenceCursor);
      status.textContent = `Duplicated ${items.length} selected cells`;
    }

    function moveSelectedSlots(delta) {
      const selected = actionSelectedIndices();
      if (!selected.length) {
        return;
      }
      const cells = sequenceFrames.map((sourceFrame, slotIndex) => ({
        sourceFrame,
        enabled: slotEnabled[slotIndex],
        selected: selected.includes(slotIndex),
        cursor: slotIndex === sequenceCursor,
      }));
      let moved = false;
      if (delta < 0) {
        for (let i = 1; i < cells.length; i += 1) {
          if (cells[i].selected && !cells[i - 1].selected) {
            const previous = cells[i - 1];
            cells[i - 1] = cells[i];
            cells[i] = previous;
            moved = true;
          }
        }
      } else {
        for (let i = cells.length - 2; i >= 0; i -= 1) {
          if (cells[i].selected && !cells[i + 1].selected) {
            const next = cells[i + 1];
            cells[i + 1] = cells[i];
            cells[i] = next;
            moved = true;
          }
        }
      }
      if (!moved) {
        status.textContent = delta < 0 ? "Already at start" : "Already at end";
        return;
      }
      sequenceFrames = cells.map((cell) => cell.sourceFrame);
      slotEnabled = cells.map((cell) => cell.enabled);
      const nextSelection = cells
        .map((cell, slotIndex) => cell.selected ? slotIndex : -1)
        .filter((slotIndex) => slotIndex >= 0);
      const cursorIndex = cells.findIndex((cell) => cell.cursor);
      sequenceCursor = cursorIndex >= 0 ? cursorIndex : nextSelection[0];
      setSelectedIndices(nextSelection, sequenceCursor);
      renderTimeline();
      setSequenceCursor(sequenceCursor);
      status.textContent = `Moved ${nextSelection.length} selected cells`;
    }

    function sendSelectedSlotsToEnd() {
      const selected = actionSelectedIndices();
      const selectedSet = new Set(selected);
      const moving = selected.map((slotIndex) => ({
        sourceFrame: sequenceFrames[slotIndex],
        enabled: slotEnabled[slotIndex],
      }));
      const remainingFrames = [];
      const remainingEnabled = [];
      for (let i = 0; i < sequenceFrames.length; i += 1) {
        if (!selectedSet.has(i)) {
          remainingFrames.push(sequenceFrames[i]);
          remainingEnabled.push(slotEnabled[i]);
        }
      }
      const insertAt = remainingFrames.length;
      sequenceFrames = remainingFrames.concat(moving.map((item) => item.sourceFrame));
      slotEnabled = remainingEnabled.concat(moving.map((item) => item.enabled));
      loopEnd = sequenceFrames.length;
      const nextSelection = moving.map((_, index) => insertAt + index);
      sequenceCursor = nextSelection[0];
      setSelectedIndices(nextSelection, sequenceCursor);
      renderTimeline();
      setSequenceCursor(sequenceCursor);
      status.textContent = `Moved ${moving.length} selected cells to end; End ${loopEnd}`;
    }

    function reverseSelectedSlots() {
      const selected = actionSelectedIndices();
      if (selected.length <= 1) {
        status.textContent = "Select at least two cells";
        return;
      }
      const reversed = selected.map((slotIndex) => ({
        sourceFrame: sequenceFrames[slotIndex],
        enabled: slotEnabled[slotIndex],
      })).reverse();
      for (let i = 0; i < selected.length; i += 1) {
        const slotIndex = selected[i];
        sequenceFrames[slotIndex] = reversed[i].sourceFrame;
        slotEnabled[slotIndex] = reversed[i].enabled;
      }
      sequenceCursor = selected.includes(sequenceCursor) ? sequenceCursor : selected[0];
      setSelectedIndices(selected, sequenceCursor);
      renderTimeline();
      setSequenceCursor(sequenceCursor);
      status.textContent = `Reversed ${selected.length} selected cells`;
    }

    function moveSlotTo(from, to) {
      if (from === to || from < 0 || from >= sequenceFrames.length || to < 0 || to >= sequenceFrames.length) {
        return;
      }
      const [sourceFrame] = sequenceFrames.splice(from, 1);
      const [enabled] = slotEnabled.splice(from, 1);
      sequenceFrames.splice(to, 0, sourceFrame);
      slotEnabled.splice(to, 0, enabled);
      sequenceCursor = to;
      setSelectedIndices([sequenceCursor], sequenceCursor);
      renderTimeline();
      setSequenceCursor(sequenceCursor);
    }

    function deleteSelectedSlots() {
      const selected = actionSelectedIndices();
      if (sequenceFrames.length <= 1 || selected.length >= sequenceFrames.length) {
        status.textContent = "Keep at least one slot";
        return;
      }
      const selectedSet = new Set(selected);
      const nextFrames = [];
      const nextEnabled = [];
      for (let i = 0; i < sequenceFrames.length; i += 1) {
        if (!selectedSet.has(i)) {
          nextFrames.push(sequenceFrames[i]);
          nextEnabled.push(slotEnabled[i]);
        }
      }
      sequenceFrames = nextFrames;
      slotEnabled = nextEnabled;
      sequenceCursor = clamp(selected[0], 0, sequenceFrames.length - 1);
      setSelectedIndices([sequenceCursor], sequenceCursor);
      renderTimeline();
      setSequenceCursor(sequenceCursor);
      status.textContent = `Deleted ${selected.length} selected cells`;
    }

    function updateTimeline() {
      for (let i = 0; i < frameSlots.length; i += 1) {
        const slotPosition = i + 1;
        const sourceFrame = sequenceFrames[i];
        const item = frameSlots[i];
        const isInLoopRange = slotPosition >= loopStart && slotPosition <= loopEnd;
        const isEnabled = slotEnabled[i];
        const isDuplicate = sequenceFrames.indexOf(sourceFrame) !== sequenceFrames.lastIndexOf(sourceFrame);
        item.slot.classList.toggle("active", i === sequenceCursor);
        item.slot.classList.toggle("selected", selectedSlotIndices.has(i));
        item.slot.classList.toggle("in-loop", isInLoopRange);
        item.slot.classList.toggle("disabled", !isEnabled);
        item.slot.classList.toggle("hidden-deselected", hideDeselectedInput.checked && !isEnabled);
        item.slot.classList.toggle("duplicate", isDuplicate);
        item.toggle.classList.toggle("enabled", isEnabled);
        item.toggle.classList.toggle("disabled", !isEnabled);
        item.toggle.setAttribute("aria-pressed", isEnabled ? "true" : "false");
        item.toggle.title = `slot ${slotPosition}: ${isEnabled ? "included" : "excluded"} from preview/export`;
      }
      const exportSlotCount = exportSlots().length;
      const uniqueExportCount = new Set(exportSlots().map((slotIndex) => sequenceFrames[slotIndex])).size;
      const selectedCount = selectedIndices().length;
      if (loopSelectedInput.checked) {
        const previewSlotCount = previewSlots().length;
        status.textContent = `${previewSlotCount} preview slots / ${exportSlotCount} export slots; ${selectedCount} cells selected`;
      } else {
        status.textContent = `${exportSlotCount} enabled slots / ${uniqueExportCount} unique images; ${selectedCount} cells selected`;
      }
    }

    function renderCrop() {
      cropOverlay.style.display = "block";
      cropOverlay.style.left = `${(crop.x / config.frameWidth) * 100}%`;
      cropOverlay.style.top = `${(crop.y / config.frameHeight) * 100}%`;
      cropOverlay.style.width = `${(crop.width / config.frameWidth) * 100}%`;
      cropOverlay.style.height = `${(crop.height / config.frameHeight) * 100}%`;
    }

    function pointFromEvent(event) {
      const rect = stage.getBoundingClientRect();
      return {
        x: clamp(((event.clientX - rect.left) / rect.width) * config.frameWidth, 0, config.frameWidth - 1),
        y: clamp(((event.clientY - rect.top) / rect.height) * config.frameHeight, 0, config.frameHeight - 1),
      };
    }

    let drawing = false;
    let drawStart = null;
    stage.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) {
        return;
      }
      playing = false;
      play.textContent = "Play";
      drawing = true;
      drawStart = pointFromEvent(event);
      stage.setPointerCapture(event.pointerId);
      setCrop({ x: drawStart.x, y: drawStart.y, width: 1, height: 1 });
    });

    stage.addEventListener("pointermove", (event) => {
      if (!drawing || !drawStart) {
        return;
      }
      const point = pointFromEvent(event);
      const x = Math.min(drawStart.x, point.x);
      const y = Math.min(drawStart.y, point.y);
      setCrop({
        x,
        y,
        width: Math.abs(point.x - drawStart.x) + 1,
        height: Math.abs(point.y - drawStart.y) + 1,
      });
    });

    stage.addEventListener("pointerup", () => {
      drawing = false;
      drawStart = null;
    });

    for (const [key, input] of Object.entries(cropInputs)) {
      input.addEventListener("change", () => {
        setCrop({
          x: Number(cropInputs.x.value),
          y: Number(cropInputs.y.value),
          width: Number(cropInputs.width.value),
          height: Number(cropInputs.height.value),
        });
      });
    }

    function timelinePayload() {
      const selectedSlots = exportSlots();
      const sequenceFramesForExport = selectedSlots.map((slotIndex) => sequenceFrames[slotIndex]);
      return {
        fps: Number(fpsInput.value) || 24,
        loop_start: loopStart,
        loop_end: loopEnd,
        frame_step: frameStep(),
        selected_slots: selectedSlots.map((slotIndex) => slotIndex + 1),
        sequence_frames: sequenceFramesForExport,
        editor_sequence_frames: sequenceFrames.slice(),
        editor_slot_enabled: slotEnabled.slice(),
        ping_pong: pingPongInput.checked,
        loop_selected: loopSelectedInput.checked,
        deleted_frames: [],
      };
    }

    function cropPayload() {
      return {
        frame_size: { width: config.frameWidth, height: config.frameHeight },
        frame_count: frameCount,
        source_frame_dir: config.frameDir,
        crop,
        timeline: timelinePayload(),
      };
    }

    function projectPayload() {
      return {
        schema: "veo-idle-sprite-viewer-project",
        version: 1,
        project_name: projectName(),
        saved_at: new Date().toISOString(),
        frame_size: { width: config.frameWidth, height: config.frameHeight },
        frame_count: frameCount,
        source_frame_dir: config.frameDir,
        out_dir: config.outDir,
        frame_names: frameNames.slice(),
        crop: { ...crop },
        timeline: timelinePayload(),
        editor: {
          current_slot: sequenceCursor + 1,
          sequence_frames: sequenceFrames.slice(),
          slot_enabled: slotEnabled.slice(),
          selected_slot_indices: selectedIndices().map((slotIndex) => slotIndex + 1),
          selection_anchor: lastClickedSlotIndex + 1,
          hide_deselected: hideDeselectedInput.checked,
          loop_selected: loopSelectedInput.checked,
          export_dir: exportDirInput.value,
          overwrite_export: overwriteExportInput.checked,
        },
      };
    }

    function projectName() {
      return (projectNameInput.value || "viewer-project").trim() || "viewer-project";
    }

    function setProjectName(value) {
      projectNameInput.value = (value || "viewer-project").replace(/\\.json$/i, "");
    }

    function shellQuote(value) {
      return JSON.stringify(value).replaceAll("$", "\\$").replaceAll("`", "\\`");
    }

    function commandText() {
      const parts = [
        "cd",
        shellQuote(config.projectDir),
        "&&",
        "npm",
        "run",
        "crop-sprites",
        "--",
        shellQuote(config.outDir),
        "--x",
        String(crop.x),
        "--y",
        String(crop.y),
        "--width",
        String(crop.width),
        "--height",
        String(crop.height),
        "--start-frame",
        String(loopStart),
        "--end-frame",
        String(loopEnd),
        "--frame-step",
        String(frameStep()),
        "--sequence-frames",
        shellQuote(exportSourceFrameNumbers().join(",")),
        "--fps",
        String(Number(fpsInput.value) || 24),
      ];
      if (pingPongInput.checked) {
        parts.push("--ping-pong");
      }
      return parts.join(" ");
    }

    async function copyText(text) {
      if (navigator.clipboard) {
        await navigator.clipboard.writeText(text);
        return;
      }
      const textarea = document.createElement("textarea");
      textarea.value = text;
      document.body.append(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }

    async function requestJson(url, options = {}) {
      const response = await fetch(url, {
        ...options,
        headers: {
          "Content-Type": "application/json",
          ...(options.headers || {}),
        },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      return data;
    }

    async function refreshViewerFolders() {
      const data = await requestJson("/api/viewers");
      viewerSelect.replaceChildren();
      if (!data.viewers || !data.viewers.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "No viewer folders";
        viewerSelect.append(option);
        return [];
      }
      for (const viewer of data.viewers) {
        const option = document.createElement("option");
        option.value = viewer.root;
        const countText = viewer.frame_count ? `${viewer.frame_count} frames` : "no frames";
        const frameDirText = viewer.frame_dir ? ` / ${viewer.frame_dir}` : "";
        option.textContent = `${viewer.current ? "* " : ""}${viewer.name}${frameDirText} (${countText})`;
        viewerSelect.append(option);
      }
      const current = data.viewers.find((viewer) => viewer.current);
      if (current) {
        viewerSelect.value = current.root;
      }
      return data.viewers;
    }

    async function openViewerProject() {
      const root = viewerSelect.value;
      if (!root) {
        throw new Error("Viewer folder is required");
      }
      status.textContent = "Opening project";
      const result = await requestJson("/api/viewer", {
        method: "POST",
        body: JSON.stringify({ root }),
      });
      window.location.href = `${result.url || "/viewer.html"}?t=${Date.now()}`;
    }

    async function refreshExportDirs() {
      const data = await requestJson("/api/export-dirs");
      exportDirSelect.replaceChildren();
      if (!data.dirs || !data.dirs.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "No export folders";
        exportDirSelect.append(option);
        return [];
      }
      for (const dir of data.dirs) {
        const option = document.createElement("option");
        option.value = dir.value;
        option.textContent = `${dir.name}${dir.exists ? "" : " (new)"}`;
        exportDirSelect.append(option);
      }
      if (exportDirInput.value) {
        exportDirSelect.value = exportDirInput.value;
      }
      if (!exportDirSelect.value && data.dirs[0]) {
        exportDirSelect.value = data.dirs[0].value;
        exportDirInput.value = data.dirs[0].value;
      }
      return data.dirs;
    }

    async function exportCropSequence() {
      const exportDir = (exportDirInput.value || "").trim();
      if (!exportDir) {
        throw new Error("Export folder is required");
      }
      status.textContent = "Exporting";
      const result = await requestJson("/api/export", {
        method: "POST",
        body: JSON.stringify({
          export_dir: exportDir,
          overwrite: overwriteExportInput.checked,
          export: cropPayload(),
        }),
      });
      status.textContent = `Exported ${result.frame_count} frames / ${result.unique_frame_count} images`;
      refreshExportDirs().catch(() => {});
    }

    async function openExportFolder() {
      const exportDir = (exportDirInput.value || "").trim();
      if (!exportDir) {
        throw new Error("Export folder is required");
      }
      const result = await requestJson("/api/reveal", {
        method: "POST",
        body: JSON.stringify({ export_dir: exportDir }),
      });
      status.textContent = `Opened Finder: ${result.out_dir}`;
    }

    async function loadServerInfo() {
      const info = await requestJson("/api/info");
      workFolder.textContent = info.viewer_root || "Unknown";
      projectFolder.textContent = info.project_dir || "Unknown";
    }

    async function refreshProjects() {
      const data = await requestJson("/api/projects");
      if (data.viewer_root) {
        workFolder.textContent = data.viewer_root;
      }
      if (data.project_dir) {
        projectFolder.textContent = data.project_dir;
      }
      projectSelect.replaceChildren();
      if (!data.projects || !data.projects.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "No projects";
        projectSelect.append(option);
        return [];
      }
      for (const project of data.projects) {
        const option = document.createElement("option");
        option.value = project.name;
        option.textContent = `${project.name} (${project.updated_at ? project.updated_at.slice(0, 19).replace("T", " ") : "saved"})`;
        projectSelect.append(option);
      }
      if (projectName()) {
        projectSelect.value = projectName();
      }
      return data.projects;
    }

    async function saveProject(name = projectName()) {
      setProjectName(name);
      const saved = await requestJson(`/api/project?name=${encodeURIComponent(projectName())}`, {
        method: "PUT",
        body: JSON.stringify(projectPayload()),
      });
      setProjectName(saved.name || projectName());
      await refreshProjects();
      projectSelect.value = projectName();
      status.textContent = `Project saved: ${projectName()}`;
    }

    async function saveProjectAs() {
      const nextName = window.prompt("Project name", projectName());
      if (nextName === null) {
        return;
      }
      await saveProject(nextName);
    }

    async function loadProject(name = projectSelect.value || projectName()) {
      if (!name) {
        throw new Error("No project selected");
      }
      const data = await requestJson(`/api/project?name=${encodeURIComponent(name)}`);
      applyProject(data);
      setProjectName(data.project_name || name);
      projectSelect.value = projectName();
    }

    function normalizeSequence(value, labelText) {
      if (value === undefined || value === null || value === "") {
        return null;
      }
      const values = Array.isArray(value) ? value : String(value).replaceAll(" ", "").split(",");
      const sequence = [];
      for (const item of values) {
        if (item === "") {
          continue;
        }
        const frameNumber = Math.round(Number(item));
        if (!Number.isFinite(frameNumber) || frameNumber < 1 || frameNumber > frameCount) {
          throw new Error(`${labelText} has an out-of-range frame`);
        }
        sequence.push(frameNumber);
      }
      if (!sequence.length) {
        throw new Error(`${labelText} is empty`);
      }
      return sequence;
    }

    function normalizeEnabled(value, length) {
      if (!Array.isArray(value)) {
        return null;
      }
      const enabled = value.slice(0, length).map((item) => item === true || item === 1 || item === "1" || item === "true");
      while (enabled.length < length) {
        enabled.push(true);
      }
      return enabled;
    }

    function enabledFromSelectedSlots(value, length) {
      if (!Array.isArray(value)) {
        return null;
      }
      const selected = new Set();
      for (const item of value) {
        const slotNumber = Math.round(Number(item));
        if (Number.isFinite(slotNumber) && slotNumber >= 1 && slotNumber <= length) {
          selected.add(slotNumber);
        }
      }
      return Array.from({ length }, (_, index) => selected.has(index + 1));
    }

    function normalizeSlotSelection(value, length, fallback) {
      if (!Array.isArray(value)) {
        return [fallback];
      }
      const selected = [];
      for (const item of value) {
        const slotNumber = Math.round(Number(item));
        if (Number.isFinite(slotNumber) && slotNumber >= 1 && slotNumber <= length && !selected.includes(slotNumber - 1)) {
          selected.push(slotNumber - 1);
        }
      }
      return selected.length ? selected : [fallback];
    }

    function assertProjectCompatible(data) {
      const size = data.frame_size;
      if (size && (Number(size.width) !== config.frameWidth || Number(size.height) !== config.frameHeight)) {
        throw new Error("Project frame size does not match this viewer");
      }
      if (data.frame_count && Number(data.frame_count) !== frameCount) {
        throw new Error("Project frame count does not match this viewer");
      }
    }

    function applyProject(data) {
      assertProjectCompatible(data);
      const timeline = data.timeline || {};
      const editor = data.editor || {};
      const editorSequence = normalizeSequence(
        editor.sequence_frames ?? timeline.editor_sequence_frames,
        "Project editor sequence"
      );
      const exportSequence = normalizeSequence(timeline.sequence_frames, "Project export sequence");

      let nextSequence = editorSequence || exportSequence;
      let nextEnabled = null;
      if (!nextSequence) {
        nextSequence = Array.from({ length: frameCount }, (_, i) => i + 1);
      }

      nextEnabled = normalizeEnabled(editor.slot_enabled ?? timeline.editor_slot_enabled, nextSequence.length);
      if (!nextEnabled) {
        nextEnabled = enabledFromSelectedSlots(timeline.selected_slots, nextSequence.length);
      }
      if (!nextEnabled) {
        nextEnabled = Array.from({ length: nextSequence.length }, () => true);
      }

      sequenceFrames = nextSequence.slice();
      slotEnabled = nextEnabled.slice();
      loopStart = clamp(Number(timeline.loop_start) || 1, 1, sequenceFrames.length);
      loopEnd = clamp(Number(timeline.loop_end) || sequenceFrames.length, 1, sequenceFrames.length);
      if (loopStart > loopEnd) {
        loopStart = 1;
        loopEnd = sequenceFrames.length;
      }
      frameStepInput.value = String(clamp(Number(timeline.frame_step) || 1, 1, sequenceFrames.length));
      fpsInput.value = String(clamp(Number(timeline.fps) || 24, 1, 60));
      pingPongInput.checked = Boolean(timeline.ping_pong);
      loopSelectedInput.checked = Boolean(editor.loop_selected ?? timeline.loop_selected ?? data.loop_selected ?? false);
      hideDeselectedInput.checked = Boolean(editor.hide_deselected ?? data.hide_deselected ?? false);
      exportDirInput.value = editor.export_dir || data.export_dir || exportDirInput.value;
      overwriteExportInput.checked = Boolean(editor.overwrite_export ?? data.overwrite_export ?? false);
      const currentSlot = clamp(Number(editor.current_slot || data.current_slot || 1) - 1, 0, sequenceFrames.length - 1);
      sequenceCursor = currentSlot;
      setSelectedIndices(normalizeSlotSelection(editor.selected_slot_indices, sequenceFrames.length, currentSlot), currentSlot);

      renderTimeline();
      if (data.crop) {
        setCrop(data.crop);
      }
      playing = false;
      play.textContent = "Play";
      setSequenceCursor(currentSlot);
      status.textContent = "Project loaded";
    }

    function loadFrame(frameIndex) {
      return new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = reject;
        image.src = path(frameIndex);
      });
    }

    async function alphaBounds(frameIndex) {
      const image = await loadFrame(frameIndex);
      const canvas = document.createElement("canvas");
      canvas.width = config.frameWidth;
      canvas.height = config.frameHeight;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      context.drawImage(image, 0, 0);
      const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let minX = canvas.width;
      let minY = canvas.height;
      let maxX = -1;
      let maxY = -1;
      for (let y = 0; y < canvas.height; y += 1) {
        for (let x = 0; x < canvas.width; x += 1) {
          const alpha = data[(y * canvas.width + x) * 4 + 3];
          if (alpha > 4) {
            if (x < minX) minX = x;
            if (y < minY) minY = y;
            if (x > maxX) maxX = x;
            if (y > maxY) maxY = y;
          }
        }
      }
      if (maxX < 0) {
        return null;
      }
      return { x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1 };
    }

    async function fitFrames(frameIndices) {
      const pad = clamp(Number(padInput.value) || 0, 0, 256);
      status.textContent = "Scanning";
      let union = null;
      for (const frameIndex of frameIndices) {
        const bounds = await alphaBounds(frameIndex);
        if (!bounds) {
          continue;
        }
        if (!union) {
          union = bounds;
        } else {
          const minX = Math.min(union.x, bounds.x);
          const minY = Math.min(union.y, bounds.y);
          const maxX = Math.max(union.x + union.width - 1, bounds.x + bounds.width - 1);
          const maxY = Math.max(union.y + union.height - 1, bounds.y + bounds.height - 1);
          union = { x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1 };
        }
      }
      if (!union) {
        status.textContent = "No alpha";
        return;
      }
      const x = clamp(union.x - pad, 0, config.frameWidth - 1);
      const y = clamp(union.y - pad, 0, config.frameHeight - 1);
      const right = clamp(union.x + union.width + pad, 1, config.frameWidth);
      const bottom = clamp(union.y + union.height + pad, 1, config.frameHeight);
      setCrop({ x, y, width: right - x, height: bottom - y });
      status.textContent = "Crop set";
    }

    play.addEventListener("click", () => {
      playing = !playing;
      play.textContent = playing ? "Pause" : "Play";
    });
    prev.addEventListener("click", () => stepFrame(-1));
    next.addEventListener("click", () => stepFrame(1));
    document.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        saveProject().catch((error) => {
          status.textContent = error instanceof Error ? error.message : "Project save failed";
        });
        return;
      }
      const target = event.target;
      const isTyping = target && (
        target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.tagName === "SELECT" ||
        target.isContentEditable
      );
      if (isTyping) {
        return;
      }
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        playing = false;
        play.textContent = "Play";
        stepFrame(event.key === "ArrowLeft" ? -1 : 1);
      }
    });
    scrub.addEventListener("input", () => {
      playing = false;
      play.textContent = "Play";
      setSequenceCursor(Number(scrub.value));
    });
    fpsInput.addEventListener("change", () => {
      fpsInput.value = String(clamp(Number(fpsInput.value) || 24, 1, 60));
    });
    frameStepInput.addEventListener("change", () => {
      frameStepInput.value = String(frameStep());
      applyEverySelection();
    });
    pingPongInput.addEventListener("change", () => {
      syncPlaybackDirectionAtEndpoint();
      updateTimeline();
    });
    loopSelectedInput.addEventListener("change", () => {
      syncPlaybackDirectionAtEndpoint();
      updateTimeline();
    });
    hideDeselectedInput.addEventListener("change", updateTimeline);
    loopStartInput.addEventListener("change", () => setLoopRange(Number(loopStartInput.value), loopEnd));
    loopEndInput.addEventListener("change", () => setLoopRange(loopStart, Number(loopEndInput.value)));
    document.getElementById("setLoopStart").addEventListener("click", () => setLoopRange(sequenceCursor + 1, loopEnd));
    document.getElementById("setLoopEnd").addEventListener("click", () => setLoopRange(loopStart, sequenceCursor + 1));
    duplicateFrameButton.addEventListener("click", duplicateSelectedSlots);
    moveLeftButton.addEventListener("click", () => moveSelectedSlots(-1));
    moveRightButton.addEventListener("click", () => moveSelectedSlots(1));
    sendEndButton.addEventListener("click", sendSelectedSlotsToEnd);
    reverseSelectedButton.addEventListener("click", reverseSelectedSlots);
    toggleDeleteButton.addEventListener("click", deleteSelectedSlots);
    document.getElementById("restoreAll").addEventListener("click", resetSequence);
    document.getElementById("fitCurrent").addEventListener("click", () => {
      fitFrames([index]).catch(() => {
        status.textContent = "Scan failed";
      });
    });
    document.getElementById("fitAll").addEventListener("click", () => {
      const frames = previewFrames();
      if (loopSelectedInput.checked && !frames.length) {
        status.textContent = "No selected preview frames";
        return;
      }
      fitFrames(frames.length ? frames : Array.from({ length: frameCount }, (_, i) => i)).catch(() => {
        status.textContent = "Scan failed";
      });
    });
    document.getElementById("fullCrop").addEventListener("click", () => {
      setCrop({ x: 0, y: 0, width: config.frameWidth, height: config.frameHeight });
      status.textContent = "Full frame";
    });
    document.getElementById("copyCommand").addEventListener("click", () => {
      copyText(commandText()).then(() => {
        status.textContent = "Command copied";
      }).catch(() => {
        status.textContent = "Copy failed";
      });
    });
    document.getElementById("exportCropSequence").addEventListener("click", () => {
      exportCropSequence().catch((error) => {
        status.textContent = error instanceof Error ? error.message : "Export failed";
      });
    });
    document.getElementById("openExportFolder").addEventListener("click", () => {
      openExportFolder().catch((error) => {
        status.textContent = error instanceof Error ? error.message : "Open Finder failed";
      });
    });
    exportDirSelect.addEventListener("change", () => {
      if (exportDirSelect.value) {
        exportDirInput.value = exportDirSelect.value;
      }
    });
    document.getElementById("refreshViewers").addEventListener("click", () => {
      refreshViewerFolders().then(() => {
        status.textContent = "Folders refreshed";
      }).catch((error) => {
        status.textContent = error instanceof Error ? error.message : "Folder refresh failed";
      });
    });
    document.getElementById("openViewerProject").addEventListener("click", () => {
      openViewerProject().catch((error) => {
        status.textContent = error instanceof Error ? error.message : "Project open failed";
      });
    });
    projectSelect.addEventListener("change", () => {
      if (projectSelect.value) {
        setProjectName(projectSelect.value);
      }
    });
    document.getElementById("refreshProjects").addEventListener("click", () => {
      refreshProjects().then(() => {
        status.textContent = "Projects refreshed";
      }).catch((error) => {
        status.textContent = error instanceof Error ? error.message : "Refresh failed";
      });
    });
    document.getElementById("saveProject").addEventListener("click", () => {
      saveProject().catch((error) => {
        status.textContent = error instanceof Error ? error.message : "Project save failed";
      });
    });
    document.getElementById("saveProjectAs").addEventListener("click", () => {
      saveProjectAs().catch((error) => {
        status.textContent = error instanceof Error ? error.message : "Project save failed";
      });
    });
    document.getElementById("loadProject").addEventListener("click", () => {
      loadProject().catch((error) => {
        status.textContent = error instanceof Error ? error.message : "Project load failed";
      });
    });

    for (let i = 0; i < frameCount; i += 1) {
      const image = new Image();
      image.src = path(i);
    }

    function loop(now) {
      const fps = clamp(Number(fpsInput.value) || 24, 1, 60);
      if (playing && now - lastTick > 1000 / fps) {
        advancePlayback();
        lastTick = now;
      }
      requestAnimationFrame(loop);
    }

    resizeStage();
    renderTimeline();
    setSequenceCursor(0);
    setCrop(crop);
    loadServerInfo().then(() => Promise.all([
      refreshProjects(),
      refreshViewerFolders(),
      refreshExportDirs(),
    ])).catch(() => {
      workFolder.textContent = "Static server";
      projectFolder.textContent = "Unavailable";
    });
    requestAnimationFrame(loop);
  </script>
</body>
</html>
""".replace("__CONFIG__", config)
    (out_dir / "viewer.html").write_text(html, encoding="utf-8")


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


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def require_binary(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"Missing required binary: {name}")
    return found


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


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def slugify(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in value)
    slug = "-".join(part for part in slug.split("-") if part)
    return slug or "video"


if __name__ == "__main__":
    raise SystemExit(main())
