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

    print("Extracting raw video frames", flush=True)
    extract_raw_frames(ffmpeg, video_path, raw_dir, args.max_frames)
    print("Extracting chromakey alpha frames", flush=True)
    extract_chromakey_alpha(ffmpeg, video_path, keyed_dir, args)

    raw_frames = sorted(raw_dir.glob(PNG_GLOB))
    keyed_frames = sorted(keyed_dir.glob(PNG_GLOB))
    if len(raw_frames) != len(keyed_frames):
        raise SystemExit(f"Raw/keyed frame count mismatch: {len(raw_frames)} vs {len(keyed_frames)}")
    if not raw_frames:
        raise SystemExit("No frames were extracted.")

    print(f"Recovering transparent foreground for {len(raw_frames)} frames", flush=True)
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
            print(f"Processed {index + 1}/{len(raw_frames)}", flush=True)

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
<html lang="ko">
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
      grid-template-rows: auto minmax(0, 1fr);
      place-items: center;
      gap: 8px;
      padding: 14px;
      overflow: auto;
    }

    .preview-tools {
      width: min(100%, 1280px);
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      flex-wrap: wrap;
    }

    .stage {
      width: min(100%, 1280px);
      max-width: none;
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

    .stage.video-drop-target {
      border-color: var(--accent);
      outline: 2px dashed var(--accent);
      outline-offset: 4px;
    }

    .stage.video-drop-target img,
    .app.importing .stage img {
      opacity: 0.12;
    }

    .stage.video-drop-target .crop,
    .app.importing .stage .crop {
      display: none !important;
    }

    .stage.video-drop-target::after,
    .app.importing .stage::after {
      position: absolute;
      inset: 0;
      display: grid;
      place-items: center;
      background: rgb(0 0 0 / 48%);
      color: var(--text);
      font-size: 15px;
      font-weight: 700;
      pointer-events: none;
    }

    .stage.video-drop-target::after {
      content: "Drop video to import";
    }

    .app.importing .stage::after {
      content: "Importing video";
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

    dialog.import-progress {
      width: min(420px, calc(100vw - 32px));
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      background: var(--panel);
      color: var(--text);
      box-shadow: 0 20px 60px rgb(0 0 0 / 44%);
    }

    dialog.import-progress::backdrop {
      background: rgb(0 0 0 / 46%);
    }

    .import-progress h2 {
      margin: 0 0 10px;
      font-size: 15px;
    }

    .import-progress p {
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 13px;
    }

    .import-progress progress {
      width: 100%;
      height: 14px;
      accent-color: var(--accent);
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
      <h1>PyMatting 스프라이트 뷰어</h1>
      <div class="meta"><span id="label">불러오는 중</span> / <span id="frameTotal"></span> 프레임</div>
    </header>
    <main>
      <div class="preview-tools" aria-label="Preview zoom controls">
        <button id="zoomOut" type="button">축소</button>
        <span class="meta" id="previewZoomLabel">줌 100%</span>
        <button id="zoomIn" type="button">확대</button>
        <button id="zoomReset" type="button">원상태</button>
        <button id="zoomActual" type="button">1:1</button>
      </div>
      <div class="stage" id="stage">
        <img id="frame" src="" alt="PyMatting sprite frame">
        <div class="crop" id="cropOverlay"></div>
      </div>
    </main>
    <footer>
      <div class="controls">
        <button class="primary" id="play" type="button">일시정지</button>
        <button id="prev" type="button">이전</button>
        <button id="next" type="button">다음</button>
        <input id="scrub" type="range" min="0" value="0" step="1">
        <label>FPS <input id="fps" type="number" min="1" max="60" value="24" step="1"></label>
        <label>전체 N번째 <input id="frameStep" type="number" min="1" max="96" value="1" step="1"></label>
        <label><input id="pingPong" type="checkbox"> 핑퐁</label>
        <label><input id="loopSelected" type="checkbox"> 선택 반복</label>
        <label><input id="hideDeselected" type="checkbox"> 꺼짐 숨김</label>
        <span class="meta" id="status">준비됨</span>
      </div>
      <div class="controls">
        <label>시작 <input id="loopStart" type="number" min="1" value="1" step="1"></label>
        <button id="setLoopStart" type="button">시작 지정</button>
        <label>끝 <input id="loopEnd" type="number" min="1" value="1" step="1"></label>
        <button id="setLoopEnd" type="button">끝 지정</button>
        <button id="duplicateFrame" type="button">복제</button>
        <button id="moveLeft" type="button">왼쪽 이동</button>
        <button id="moveRight" type="button">오른쪽 이동</button>
        <button id="sendEnd" type="button">끝으로</button>
        <button id="reverseSelected" type="button">반전</button>
        <button id="selectAllCells" type="button">전체 선택</button>
        <button id="deselectAllCells" type="button">선택 해제</button>
        <button id="enableSelected" type="button">선택 켜기</button>
        <button id="disableSelected" type="button">선택 끄기</button>
        <label>선택 N번째 <input id="selectedFrameStep" type="number" min="1" max="96" value="2" step="1"></label>
        <button id="enableSelectedEvery" type="button">N번째 켜기</button>
        <button id="disableSelectedEvery" type="button">N번째 끄기</button>
        <button id="keepSelectedEvery" type="button">N번째만 남기기</button>
        <button id="keepSelectedEnabled" type="button">선택 켜짐만 남기기</button>
        <button id="toggleDelete" type="button">슬롯 삭제</button>
        <button id="restoreAll" type="button">전체 복원</button>
      </div>
      <div class="timeline" id="timeline" aria-label="Frame timeline"></div>
      <div class="controls">
        <label>여백 <input id="pad" type="number" min="0" max="256" value="16" step="1"></label>
        <button id="fitCurrent" type="button">현재 맞춤</button>
        <button id="fitAll" type="button">전체 맞춤</button>
        <button id="fullCrop" type="button">전체 영역</button>
        <button id="copyCommand" type="button">명령 복사</button>
        <label>출력 W <input id="outputWidth" type="number" min="1" max="32768" value="1" step="1">px</label>
        <label>출력 H <input id="outputHeight" type="number" min="1" max="32768" value="1" step="1">px</label>
        <label><input id="keepOutputAspect" type="checkbox" checked> 비율 유지</label>
        <button id="matchCropSize" type="button">크롭=출력</button>
        <label>내보내기 <input id="exportDir" type="text" value="exports/viewer-project"></label>
        <select id="exportDirSelect" aria-label="Export folders"></select>
        <label><input id="overwriteExport" type="checkbox"> 덮어쓰기</label>
        <button id="exportCropSequence" type="button">크롭 내보내기</button>
        <button id="openExportFolder" type="button">Finder 열기</button>
      </div>
      <div class="controls">
        <button id="importVideo" type="button">비디오 가져오기</button>
        <input id="videoFile" type="file" accept=".mp4,.mov,.m4v,.webm,video/mp4,video/quicktime,video/webm" hidden>
        <label>폴더 <select id="viewerSelect" aria-label="Sprite viewer folders"></select></label>
        <button id="refreshViewers" type="button">폴더 새로고침</button>
        <button id="openViewerProject" type="button">새 프로젝트</button>
      </div>
      <div class="controls">
        <label>프로젝트 <input id="projectName" type="text" value="viewer-project"></label>
        <select id="projectSelect" aria-label="Saved projects"></select>
        <button id="refreshProjects" type="button">새로고침</button>
        <button id="saveProject" type="button">프로젝트 저장</button>
        <button id="saveProjectAs" type="button">다른 이름 저장</button>
        <button id="loadProject" type="button">불러오기</button>
      </div>
      <div class="controls">
        <span class="meta project-meta">작업 폴더 <span id="workFolder">불러오는 중</span></span>
        <span class="meta project-meta">프로젝트 폴더 <span id="projectFolder">불러오는 중</span></span>
      </div>
      <div class="controls">
        <label>X <input id="cropX" type="number" min="0" step="1"></label>
        <label>Y <input id="cropY" type="number" min="0" step="1"></label>
        <label>W <input id="cropW" type="number" min="1" step="1"></label>
        <label>H <input id="cropH" type="number" min="1" step="1"></label>
      </div>
    </footer>
  </div>
  <dialog class="import-progress" id="importProgressDialog" aria-labelledby="importProgressTitle">
    <h2 id="importProgressTitle">비디오 가져오는 중</h2>
    <p id="importProgressText">준비 중</p>
    <progress id="importProgressBar" max="100" value="0"></progress>
    <div class="meta" id="importProgressDetail"></div>
    <button id="importProgressClose" type="button" hidden>닫기</button>
  </dialog>
  <script>
    const config = __CONFIG__;
    const frameCount = config.frameCount;
    const frameNames = config.frames;
    const app = document.querySelector(".app");
    const frame = document.getElementById("frame");
    const stage = document.getElementById("stage");
    const cropOverlay = document.getElementById("cropOverlay");
    const play = document.getElementById("play");
    const prev = document.getElementById("prev");
    const next = document.getElementById("next");
    const scrub = document.getElementById("scrub");
    const label = document.getElementById("label");
    const status = document.getElementById("status");
    const importProgressDialog = document.getElementById("importProgressDialog");
    const importProgressText = document.getElementById("importProgressText");
    const importProgressBar = document.getElementById("importProgressBar");
    const importProgressDetail = document.getElementById("importProgressDetail");
    const importProgressClose = document.getElementById("importProgressClose");
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
    const selectAllCellsButton = document.getElementById("selectAllCells");
    const deselectAllCellsButton = document.getElementById("deselectAllCells");
    const enableSelectedButton = document.getElementById("enableSelected");
    const disableSelectedButton = document.getElementById("disableSelected");
    const selectedFrameStepInput = document.getElementById("selectedFrameStep");
    const enableSelectedEveryButton = document.getElementById("enableSelectedEvery");
    const disableSelectedEveryButton = document.getElementById("disableSelectedEvery");
    const keepSelectedEveryButton = document.getElementById("keepSelectedEvery");
    const keepSelectedEnabledButton = document.getElementById("keepSelectedEnabled");
    const toggleDeleteButton = document.getElementById("toggleDelete");
    const projectNameInput = document.getElementById("projectName");
    const projectSelect = document.getElementById("projectSelect");
    const viewerSelect = document.getElementById("viewerSelect");
    const importVideoButton = document.getElementById("importVideo");
    const videoFileInput = document.getElementById("videoFile");
    const workFolder = document.getElementById("workFolder");
    const projectFolder = document.getElementById("projectFolder");
    const zoomOutButton = document.getElementById("zoomOut");
    const zoomInButton = document.getElementById("zoomIn");
    const zoomResetButton = document.getElementById("zoomReset");
    const zoomActualButton = document.getElementById("zoomActual");
    const previewZoomLabel = document.getElementById("previewZoomLabel");
    const outputWidthInput = document.getElementById("outputWidth");
    const outputHeightInput = document.getElementById("outputHeight");
    const keepOutputAspectInput = document.getElementById("keepOutputAspect");
    const matchCropSizeButton = document.getElementById("matchCropSize");
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
    const buttonTitles = {
      play: "재생/일시정지: 현재 on 상태이고 루프 범위 안에 있는 셀을 FPS 설정에 맞춰 미리보기로 재생하거나 멈춥니다.",
      prev: "이전 프레임: 현재 미리보기 순서에서 이전 on 셀로 이동합니다.",
      next: "다음 프레임: 현재 미리보기 순서에서 다음 on 셀로 이동합니다.",
      zoomOut: "축소: 프리뷰 영역을 한 단계 작게 봅니다. 내보내기 픽셀 크기는 바뀌지 않습니다.",
      zoomIn: "확대: 프리뷰 영역을 한 단계 크게 봅니다. 픽셀 확인용 화면 확대입니다.",
      zoomReset: "원상태: 프리뷰 줌을 처음 열었을 때의 100% 상태로 되돌립니다.",
      zoomActual: "1:1: 원본 프레임 픽셀을 화면 CSS 픽셀과 1:1 크기로 표시합니다.",
      setLoopStart: "시작 지정: 현재 활성 슬롯을 루프와 내보내기 범위의 시작점으로 설정합니다.",
      setLoopEnd: "끝 지정: 현재 활성 슬롯을 루프와 내보내기 범위의 끝점으로 설정합니다.",
      duplicateFrame: "복제: 선택된 셀들을 바로 뒤에 복사합니다. 같은 원본 프레임을 재사용하므로 이미지 파일은 중복 저장하지 않습니다.",
      moveLeft: "왼쪽 이동: 선택된 셀 묶음을 타임라인에서 한 칸 왼쪽으로 이동합니다.",
      moveRight: "오른쪽 이동: 선택된 셀 묶음을 타임라인에서 한 칸 오른쪽으로 이동합니다.",
      sendEnd: "끝으로: 선택된 셀 묶음을 타임라인 맨 끝으로 보내고 루프 끝도 새 마지막 슬롯으로 맞춥니다.",
      reverseSelected: "반전: 선택된 셀들의 순서를 그 자리에서 뒤집습니다.",
      selectAllCells: "전체 선택: 모든 타임라인 셀을 선택합니다. 단축키 Cmd+A.",
      deselectAllCells: "선택 해제: 현재 선택된 셀을 모두 해제합니다. 단축키 Cmd+D.",
      enableSelected: "선택 켜기: 선택된 셀을 모두 on으로 바꿔 미리보기와 내보내기에 포함합니다.",
      disableSelected: "선택 끄기: 선택된 셀을 모두 off로 바꿔 미리보기와 내보내기에서 제외합니다.",
      enableSelectedEvery: "N번째 켜기: 선택된 셀 목록 안에서 선택 N번째 값마다 해당하는 셀만 on으로 바꿉니다.",
      disableSelectedEvery: "N번째 끄기: 선택된 셀 목록 안에서 선택 N번째 값마다 해당하는 셀만 off로 바꿉니다.",
      keepSelectedEvery: "N번째만 남기기: 선택된 셀 목록 안에서 선택 N번째 값마다 해당하는 셀만 on으로 두고, 나머지 선택 셀은 off로 바꿉니다.",
      keepSelectedEnabled: "선택 켜짐만 남기기: 선택되어 있으면서 on인 셀만 남기고 나머지 슬롯을 삭제합니다.",
      toggleDelete: "슬롯 삭제: 선택된 셀을 타임라인에서 삭제합니다. 원본 PNG 파일은 삭제하지 않습니다.",
      restoreAll: "전체 복원: 슬롯 순서, 복제, on/off 선택, 루프 범위를 처음 불러온 상태로 되돌립니다.",
      fitCurrent: "현재 맞춤: 현재 보이는 프레임의 알파 영역을 기준으로 크롭 박스를 맞춥니다.",
      fitAll: "전체 맞춤: 미리보기 대상 프레임 전체의 알파 영역을 스캔해 공통 크롭 박스를 맞춥니다.",
      fullCrop: "전체 영역: 크롭 박스를 프레임 전체 크기로 되돌립니다.",
      copyCommand: "명령 복사: 현재 크롭과 타임라인 설정으로 crop-sprites 명령을 클립보드에 복사합니다.",
      matchCropSize: "크롭=출력: 내보내기 PNG 크기를 현재 크롭 픽셀 크기와 같게 맞춥니다.",
      exportCropSequence: "크롭 내보내기: 백엔드 서버로 현재 크롭과 on 슬롯 시퀀스를 내보내 게임용 PNG와 JSON을 생성합니다.",
      openExportFolder: "Finder 열기: 현재 내보내기 폴더를 Finder에서 엽니다.",
      importVideo: "비디오 가져오기: MP4/MOV/WebM 파일을 업로드해 PyMatting 스프라이트 뷰어 프로젝트로 변환합니다.",
      refreshViewers: "폴더 새로고침: 생성된 스프라이트 뷰어 폴더 목록을 다시 불러옵니다.",
      openViewerProject: "새 프로젝트: 선택한 스프라이트 뷰어 폴더를 새 작업 대상으로 엽니다.",
      refreshProjects: "새로고침: 저장된 프로젝트 목록을 다시 불러옵니다.",
      saveProject: "프로젝트 저장: 현재 크롭, 슬롯 순서, on/off, 선택 상태, 내보내기 설정을 저장합니다. 단축키 Cmd+S.",
      saveProjectAs: "다른 이름 저장: 현재 편집 상태를 새 프로젝트 이름으로 저장합니다.",
      loadProject: "불러오기: 선택한 저장 프로젝트의 크롭과 타임라인 편집 상태를 불러옵니다.",
      importProgressClose: "닫기: 비디오 가져오기 진행 창을 닫습니다.",
    };
    for (const [id, title] of Object.entries(buttonTitles)) {
      const button = document.getElementById(id);
      if (button) {
        button.title = title;
      }
    }
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
    let previewZoom = 1;

    function baseStageWidth() {
      const availableWidth = Math.max(240, window.innerWidth - 28);
      const availableHeight = Math.max(240, window.innerHeight - 184);
      return Math.max(240, Math.min(1280, availableWidth, availableHeight * config.frameWidth / config.frameHeight));
    }

    function applyPreviewZoom() {
      stage.style.width = `${Math.max(1, Math.floor(baseStageWidth() * previewZoom))}px`;
      previewZoomLabel.textContent = `줌 ${Math.round(previewZoom * 100)}%`;
    }

    function setPreviewZoom(nextZoom) {
      previewZoom = clamp(Number(nextZoom) || 1, 0.25, 16);
      applyPreviewZoom();
    }

    function resizeStage() {
      applyPreviewZoom();
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

    function rawSelectedIndices() {
      return Array.from(selectedSlotIndices)
        .filter((slotIndex) => Number.isInteger(slotIndex) && slotIndex >= 0 && slotIndex < sequenceFrames.length)
        .sort((a, b) => a - b);
    }

    function selectedIndices() {
      const valid = rawSelectedIndices();
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

    function selectAllCells() {
      setSelectedIndices(Array.from({ length: sequenceFrames.length }, (_, i) => i), sequenceCursor);
      updateTimeline();
      status.textContent = `전체 ${sequenceFrames.length}개 셀 선택`;
    }

    function deselectAllCells() {
      selectedSlotIndices = new Set();
      updateTimeline();
      status.textContent = "셀 선택 해제";
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
      play.textContent = "재생";
      setSequenceCursor(cursor);
    }

    function clamp(value, min, max) {
      return Math.min(Math.max(value, min), max);
    }

    function setCrop(nextCrop) {
      const previousCrop = crop;
      const x = clamp(Math.round(nextCrop.x), 0, config.frameWidth - 1);
      const y = clamp(Math.round(nextCrop.y), 0, config.frameHeight - 1);
      const width = clamp(Math.round(nextCrop.width), 1, config.frameWidth - x);
      const height = clamp(Math.round(nextCrop.height), 1, config.frameHeight - y);
      crop = { x, y, width, height };
      cropInputs.x.value = String(x);
      cropInputs.y.value = String(y);
      cropInputs.width.value = String(width);
      cropInputs.height.value = String(height);
      syncOutputSizeAfterCrop(previousCrop);
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
      selectedFrameStepInput.max = String(sequenceLength);
      loopStartInput.value = String(loopStart);
      loopEndInput.value = String(loopEnd);
      document.getElementById("frameTotal").textContent = `${frameCount} source / ${sequenceLength} slots`;
    }

    function frameStep() {
      return clamp(Math.round(Number(frameStepInput.value) || 1), 1, sequenceFrames.length);
    }

    function selectedFrameStep() {
      return clamp(Math.round(Number(selectedFrameStepInput.value) || 1), 1, sequenceFrames.length);
    }

    function outputDimension(input, fallback) {
      return clamp(Math.round(Number(input.value) || fallback), 1, 32768);
    }

    function outputSize() {
      return {
        width: outputDimension(outputWidthInput, crop.width),
        height: outputDimension(outputHeightInput, crop.height),
      };
    }

    function setOutputSize(size) {
      outputWidthInput.value = String(outputDimension({ value: size.width }, crop.width));
      outputHeightInput.value = String(outputDimension({ value: size.height }, crop.height));
    }

    function outputMatchesCrop(targetCrop) {
      const size = outputSize();
      return size.width === targetCrop.width && size.height === targetCrop.height;
    }

    function syncOutputSizeAfterCrop(previousCrop) {
      if (outputMatchesCrop(previousCrop)) {
        setOutputSize({ width: crop.width, height: crop.height });
      }
    }

    function setOutputWidth(value) {
      const next = Number(value);
      if (!Number.isFinite(next) || value === "") {
        return;
      }
      const width = clamp(Math.round(next), 1, 32768);
      if (keepOutputAspectInput.checked) {
        setOutputSize({ width, height: Math.max(1, Math.round(width * crop.height / crop.width)) });
        return;
      }
      setOutputSize({ width, height: outputSize().height });
    }

    function setOutputHeight(value) {
      const next = Number(value);
      if (!Number.isFinite(next) || value === "") {
        return;
      }
      const height = clamp(Math.round(next), 1, 32768);
      if (keepOutputAspectInput.checked) {
        setOutputSize({ width: Math.max(1, Math.round(height * crop.width / crop.height)), height });
        return;
      }
      setOutputSize({ width: outputSize().width, height });
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
      play.textContent = "재생";
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
        header.title = `셀 선택/이동: 슬롯 ${slotIndex + 1}, 원본 프레임 ${sourceFrame}. 클릭하면 이 셀만 선택하고, Shift+클릭은 범위 선택, Cmd+클릭은 선택 추가/해제, 드래그는 순서 이동입니다. 파일: ${frameNames[sourceFrame - 1]}`;
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
        toggle.title = `셀 on/off: 슬롯 ${slotIndex + 1}을 미리보기와 내보내기에 ${slotEnabled[slotIndex] ? "포함" : "제외"}합니다. 클릭하면 현재 셀의 on/off 상태가 바뀝니다.`;
        toggle.addEventListener("click", () => {
          playing = false;
          play.textContent = "재생";
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

    function setSelectedCellsEnabled(enabled) {
      const selected = selectedIndices();
      for (const slotIndex of selected) {
        slotEnabled[slotIndex] = enabled;
      }
      playing = false;
      play.textContent = "재생";
      syncPlaybackDirectionAtEndpoint();
      updateTimeline();
      status.textContent = `${enabled ? "Enabled" : "Disabled"} ${selected.length} selected cells`;
    }

    function setSelectedEveryCellsEnabled(enabled) {
      const selected = selectedIndices();
      const step = selectedFrameStep();
      const every = selected.filter((_, position) => position % step === 0);
      for (const slotIndex of every) {
        slotEnabled[slotIndex] = enabled;
      }
      playing = false;
      play.textContent = "재생";
      syncPlaybackDirectionAtEndpoint();
      updateTimeline();
      status.textContent = `${enabled ? "Enabled" : "Disabled"} ${every.length}/${selected.length} selected cells every ${step}`;
    }

    function keepOnlySelectedEveryCellsEnabled() {
      const selected = selectedIndices();
      const step = selectedFrameStep();
      let kept = 0;
      selected.forEach((slotIndex, position) => {
        const enabled = position % step === 0;
        slotEnabled[slotIndex] = enabled;
        if (enabled) {
          kept += 1;
        }
      });
      playing = false;
      play.textContent = "재생";
      syncPlaybackDirectionAtEndpoint();
      updateTimeline();
      status.textContent = `선택 셀 ${selected.length}개 중 ${kept}개만 on`;
    }

    function keepSelectedEnabledSlots() {
      const keep = selectedIndices().filter((slotIndex) => slotEnabled[slotIndex]);
      if (!keep.length) {
        status.textContent = "No selected on cells";
        return;
      }
      sequenceFrames = keep.map((slotIndex) => sequenceFrames[slotIndex]);
      slotEnabled = Array.from({ length: sequenceFrames.length }, () => true);
      loopStart = 1;
      loopEnd = sequenceFrames.length;
      sequenceCursor = 0;
      setSelectedIndices(Array.from({ length: sequenceFrames.length }, (_, i) => i), sequenceCursor);
      renderTimeline();
      setSequenceCursor(sequenceCursor);
      status.textContent = `Kept ${sequenceFrames.length} selected on cells`;
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
        item.toggle.title = `셀 on/off: 슬롯 ${slotPosition}을 미리보기와 내보내기에 ${isEnabled ? "포함" : "제외"}합니다. 클릭하면 현재 셀의 on/off 상태가 바뀝니다.`;
      }
      const exportSlotCount = exportSlots().length;
      const uniqueExportCount = new Set(exportSlots().map((slotIndex) => sequenceFrames[slotIndex])).size;
      const selectedCount = rawSelectedIndices().length;
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
      play.textContent = "재생";
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
      const size = outputSize();
      return {
        frame_size: { width: config.frameWidth, height: config.frameHeight },
        frame_count: frameCount,
        source_frame_dir: config.frameDir,
        output_size: size,
        output_frame_size: size,
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
        output_size: outputSize(),
        frame_names: frameNames.slice(),
        crop: { ...crop },
        timeline: timelinePayload(),
        editor: {
          current_slot: sequenceCursor + 1,
          sequence_frames: sequenceFrames.slice(),
          slot_enabled: slotEnabled.slice(),
          selected_slot_indices: rawSelectedIndices().map((slotIndex) => slotIndex + 1),
          selection_anchor: lastClickedSlotIndex + 1,
          hide_deselected: hideDeselectedInput.checked,
          loop_selected: loopSelectedInput.checked,
          output_size: outputSize(),
          keep_output_aspect: keepOutputAspectInput.checked,
          preview_zoom: previewZoom,
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

    function startupProjectName() {
      const params = new URLSearchParams(window.location.search);
      if (params.get("new") !== "1") {
        return "";
      }
      return (params.get("project") || "viewer-project")
        .replace(/\\.json$/i, "")
        .replace(/[\\\\/?%*:|"<>]/g, "_")
        .trim();
    }

    function applyStartupProjectDefaults() {
      const name = startupProjectName();
      if (!name) {
        return;
      }
      setProjectName(name);
      exportDirInput.value = `exports/${name}`;
      overwriteExportInput.checked = false;
      status.textContent = `New project: ${name}`;
    }

    function shellQuote(value) {
      return JSON.stringify(value).replaceAll("$", "\\$").replaceAll("`", "\\`");
    }

    function commandText() {
      const size = outputSize();
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
        "--out-width",
        String(size.width),
        "--out-height",
        String(size.height),
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

    function isVideoFile(file) {
      return Boolean(file && (
        /^video\\//.test(file.type || "") ||
        /\\.(mp4|mov|m4v|webm)$/i.test(file.name || "")
      ));
    }

    function eventHasFiles(event) {
      const transfer = event.dataTransfer;
      if (!transfer) {
        return false;
      }
      return Array.from(transfer.items || []).some((item) => item.kind === "file") ||
        (transfer.files && transfer.files.length > 0);
    }

    function droppedVideoFile(event) {
      return Array.from(event.dataTransfer?.files || []).find(isVideoFile) || null;
    }

    function showImportProgress(text) {
      app.classList.add("importing");
      importProgressText.textContent = text;
      importProgressDetail.textContent = "";
      importProgressClose.hidden = true;
      importProgressBar.max = 100;
      importProgressBar.value = 0;
      if (!importProgressDialog.open) {
        if (importProgressDialog.showModal) {
          importProgressDialog.showModal();
        } else {
          importProgressDialog.setAttribute("open", "");
        }
      }
    }

    function showImportError(error) {
      app.classList.remove("importing");
      importProgressText.textContent = error instanceof Error ? error.message : "Video import failed";
      importProgressBar.value = 0;
      importProgressDetail.textContent = "Failed";
      importProgressClose.hidden = false;
    }

    function setImportProgress(text, percent = null) {
      importProgressText.textContent = text;
      if (Number.isFinite(percent)) {
        const value = clamp(Math.round(percent), 0, 100);
        importProgressBar.value = value;
        importProgressDetail.textContent = `${value}%`;
      } else {
        importProgressBar.removeAttribute("value");
        importProgressDetail.textContent = "";
      }
    }

    function uploadVideoFile(file) {
      return new Promise((resolve, reject) => {
        const request = new XMLHttpRequest();
        request.open("POST", `/api/import-video?stream=1&name=${encodeURIComponent(file.name || "video.mp4")}`);
        request.setRequestHeader("Content-Type", file.type || "application/octet-stream");
        let seenLength = 0;
        let responseBuffer = "";
        let doneEvent = null;

        function handleImportEvent(event) {
          if (event.type === "uploaded") {
            setImportProgress("Upload complete; extracting frames");
          } else if (event.type === "progress") {
            setImportProgress(event.message || "Recovering transparent frames", event.percent);
            importProgressDetail.textContent = `${event.processed}/${event.total} frames`;
          } else if (event.type === "log") {
            importProgressText.textContent = event.message || "Processing frames";
          } else if (event.type === "done") {
            doneEvent = event;
            setImportProgress(event.message || "Opening new project", 100);
          } else if (event.type === "error") {
            reject(new Error(event.message || "Video import failed"));
          }
        }

        function readImportEvents() {
          responseBuffer += request.responseText.slice(seenLength);
          seenLength = request.responseText.length;
          const lines = responseBuffer.split("\\n");
          responseBuffer = lines.pop() || "";
          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed) {
              continue;
            }
            try {
              handleImportEvent(JSON.parse(trimmed));
            } catch (error) {
              reject(error);
            }
          }
        }

        request.upload.addEventListener("progress", (event) => {
          if (event.lengthComputable && event.total > 0) {
            setImportProgress(`Uploading ${file.name || "video"}`, (event.loaded / event.total) * 100);
          }
        });
        request.upload.addEventListener("load", () => {
          setImportProgress("Processing frames");
        });
        request.addEventListener("progress", readImportEvents);
        request.addEventListener("load", () => {
          readImportEvents();
          if (responseBuffer.trim()) {
            try {
              handleImportEvent(JSON.parse(responseBuffer));
            } catch (error) {
              reject(error);
              return;
            }
            responseBuffer = "";
          }
          if (request.status >= 200 && request.status < 300) {
            resolve(doneEvent || {});
          } else {
            reject(new Error(`HTTP ${request.status}`));
          }
        });
        request.addEventListener("error", () => reject(new Error("Video upload failed")));
        request.addEventListener("abort", () => reject(new Error("Video upload cancelled")));
        request.send(file);
      });
    }

    async function importVideoFile(file) {
      if (!isVideoFile(file)) {
        throw new Error("Drop an MP4, MOV, M4V, or WEBM video");
      }
      playing = false;
      play.textContent = "재생";
      showImportProgress(`Uploading ${file.name || "video"}`);
      status.textContent = `Uploading ${file.name || "video"}`;
      const data = await uploadVideoFile(file);
      setImportProgress("Processing frames");
      status.textContent = "Opening imported video";
      window.location.href = `${data.url || "/viewer.html"}?t=${Date.now()}`;
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
      const size = result.output_frame_size ? ` @ ${result.output_frame_size.width}x${result.output_frame_size.height}` : "";
      status.textContent = `Exported ${result.frame_count} frames / ${result.unique_frame_count} images${size}`;
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
      keepOutputAspectInput.checked = Boolean(editor.keep_output_aspect ?? data.keep_output_aspect ?? true);
      exportDirInput.value = editor.export_dir || data.export_dir || exportDirInput.value;
      overwriteExportInput.checked = Boolean(editor.overwrite_export ?? data.overwrite_export ?? false);
      const currentSlot = clamp(Number(editor.current_slot || data.current_slot || 1) - 1, 0, sequenceFrames.length - 1);
      sequenceCursor = currentSlot;
      setSelectedIndices(normalizeSlotSelection(editor.selected_slot_indices, sequenceFrames.length, currentSlot), currentSlot);

      renderTimeline();
      if (data.crop) {
        setCrop(data.crop);
      }
      const savedOutputSize = editor.output_size || data.output_size || data.output_frame_size;
      if (savedOutputSize) {
        setOutputSize(savedOutputSize);
      } else {
        const legacyScale = clamp(Number(editor.output_scale_percent ?? data.output_scale_percent ?? 100) || 100, 1, 100) / 100;
        setOutputSize({ width: Math.round(crop.width * legacyScale), height: Math.round(crop.height * legacyScale) });
      }
      if (Number.isFinite(Number(editor.preview_zoom))) {
        setPreviewZoom(Number(editor.preview_zoom));
      }
      playing = false;
      play.textContent = "재생";
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
      play.textContent = playing ? "일시정지" : "재생";
    });
    prev.addEventListener("click", () => stepFrame(-1));
    next.addEventListener("click", () => stepFrame(1));
    timeline.addEventListener("wheel", (event) => {
      if (!event.deltaY || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
        return;
      }
      event.preventDefault();
      timeline.scrollLeft += event.deltaY;
    }, { passive: false });
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
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "a") {
        event.preventDefault();
        selectAllCells();
        return;
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "d") {
        event.preventDefault();
        deselectAllCells();
        return;
      }
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        playing = false;
        play.textContent = "재생";
        stepFrame(event.key === "ArrowLeft" ? -1 : 1);
      }
    });
    scrub.addEventListener("input", () => {
      playing = false;
      play.textContent = "재생";
      setSequenceCursor(Number(scrub.value));
    });
    fpsInput.addEventListener("change", () => {
      fpsInput.value = String(clamp(Number(fpsInput.value) || 24, 1, 60));
    });
    frameStepInput.addEventListener("change", () => {
      frameStepInput.value = String(frameStep());
      applyEverySelection();
    });
    selectedFrameStepInput.addEventListener("change", () => {
      selectedFrameStepInput.value = String(selectedFrameStep());
    });
    zoomOutButton.addEventListener("click", () => setPreviewZoom(previewZoom - 0.25));
    zoomInButton.addEventListener("click", () => setPreviewZoom(previewZoom + 0.25));
    zoomResetButton.addEventListener("click", () => setPreviewZoom(1));
    zoomActualButton.addEventListener("click", () => setPreviewZoom(config.frameWidth / baseStageWidth()));
    outputWidthInput.addEventListener("input", () => setOutputWidth(outputWidthInput.value));
    outputHeightInput.addEventListener("input", () => setOutputHeight(outputHeightInput.value));
    outputWidthInput.addEventListener("change", () => setOutputWidth(outputWidthInput.value));
    outputHeightInput.addEventListener("change", () => setOutputHeight(outputHeightInput.value));
    matchCropSizeButton.addEventListener("click", () => {
      setOutputSize({ width: crop.width, height: crop.height });
      status.textContent = "Output size matches crop";
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
    selectAllCellsButton.addEventListener("click", selectAllCells);
    deselectAllCellsButton.addEventListener("click", deselectAllCells);
    enableSelectedButton.addEventListener("click", () => setSelectedCellsEnabled(true));
    disableSelectedButton.addEventListener("click", () => setSelectedCellsEnabled(false));
    enableSelectedEveryButton.addEventListener("click", () => setSelectedEveryCellsEnabled(true));
    disableSelectedEveryButton.addEventListener("click", () => setSelectedEveryCellsEnabled(false));
    keepSelectedEveryButton.addEventListener("click", keepOnlySelectedEveryCellsEnabled);
    keepSelectedEnabledButton.addEventListener("click", keepSelectedEnabledSlots);
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
    importVideoButton.addEventListener("click", () => {
      videoFileInput.click();
    });
    videoFileInput.addEventListener("change", () => {
      const file = Array.from(videoFileInput.files || []).find(isVideoFile);
      videoFileInput.value = "";
      if (!file) {
        status.textContent = "No video selected";
        return;
      }
      importVideoFile(file).catch((error) => {
        showImportError(error);
        status.textContent = error instanceof Error ? error.message : "Video import failed";
      });
    });
    document.addEventListener("dragover", (event) => {
      if (!eventHasFiles(event)) {
        return;
      }
      event.preventDefault();
      stage.classList.add("video-drop-target");
      event.dataTransfer.dropEffect = "copy";
    });
    document.addEventListener("dragleave", (event) => {
      if (!event.relatedTarget) {
        stage.classList.remove("video-drop-target");
      }
    });
    document.addEventListener("drop", (event) => {
      const file = droppedVideoFile(event);
      if (!file) {
        stage.classList.remove("video-drop-target");
        return;
      }
      event.preventDefault();
      stage.classList.remove("video-drop-target");
      importVideoFile(file).catch((error) => {
        showImportError(error);
        status.textContent = error instanceof Error ? error.message : "Video import failed";
      });
    });
    importProgressClose.addEventListener("click", () => {
      importProgressDialog.close();
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
    setOutputSize({ width: crop.width, height: crop.height });
    applyStartupProjectDefaults();
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
