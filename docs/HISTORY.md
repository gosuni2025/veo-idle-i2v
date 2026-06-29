# Image-to-Video History

## 2026-06-12

Chroma-key refinement:

- Added viewer-selectable comparison frame sets for `Formula Unmix`, `PyMatting FG`, `PyMatting + Outline`, and `CorridorKey Proxy`.
- After visual review, `PyMatting FG` is the preferred default. It preserves hair shape better than `PyMatting + Outline`, which hides spill slightly more but softens/tapers outlines in a less convincing way.
- Ran CorridorKey v1.0 through a synthetic green-screen proxy because the public checkpoint supports green/blue screen, not magenta screen directly. The proxy is built from the current alpha and formula-unmixed foreground, composited over green, then processed by CorridorKey with a green screen setting.
- CorridorKey MPS inference completed for all 192 frames at `image-size 1024`. The result is selectable in the local viewer as `CorridorKey Proxy`, but its hair tips are thicker/softer than `PyMatting FG`.
- Added `npm run spriteify-pymatting` as the standard post-download command: it extracts MP4 frames, creates an FFmpeg chromakey alpha matte, then uses PyMatting foreground estimation to write transparent sprite frames under `outputs/sprites-pymatting/<video-slug>/frames_pymatting_fg/`.

Workflow decision:

- Practical generation should start in Google Flow web because it has been cheaper/better for this asset.
- On a new thread, bootstrap by opening Google Flow, using `prompts/idle_hair_locked_no_particles.txt`, and uploading the Google-sized start frame `inputs/flow_idle_input_magenta_16x9_1920x1080.png`.
- After the user downloads a Flow MP4 and requests post-processing, move it to `outputs/google-flow/` and run the PyMatting FG sprite pipeline.
- Removed the old Gemini/Veo paid-generation scripts, `.env.example`, and related npm dependencies from the current workflow. This repository now keeps Flow web as the generation path.

Game crop workflow:

- Added `npm run crop-sprites` / `scripts/crop_sprite_frames.py` to crop final transparent sprite frames with one shared rectangle.
- Updated generated PyMatting viewer pages with a crop overlay, alpha-fit buttons, crop command copy, and `sprite-export-settings.json` export.
- Crop coordinates are based on the final extracted frame resolution, not the original Google-sized 1920x1080/1080x1920 start frame. This matters because Flow downloads may be 1280x720 or another final video size.
- Extended the crop/export workflow with an Aseprite-like frame timeline, loop start/end, FPS preview/export metadata, ping-pong preview/export metadata, and per-frame deletion. Exported game frames are renumbered from `frame_0000.png` while `crop-meta.json` records the source-to-output frame map.
- Moved the downloaded Google Flow result `Character_idle_animation_illustr…_202606121110.mp4` into `outputs/google-flow/` and spriteified it with PyMatting FG. The output viewer is under `outputs/sprites-pymatting/character-idle-animation-illustr-202606121110/`.
- Fixed ping-pong preview playback to keep an explicit playback direction and flip only at loop endpoints, instead of using duplicate-frame sequence lookup.
- Added an `Every` / `--frame-step` control so preview playback and export can use every Nth source frame to reduce game sprite data size.
- Added timeline slot duplication and move-left/move-right controls to the generated viewer. Export now carries `sequence_frames`; the cropper writes duplicated source images only once and records `animation_indices` / `animation_sequence` in `crop-meta.json` so a game engine can cache unique PNGs and assemble duplicated animation frames numerically.
- Changed the viewer timeline model to per-slot preview/export toggles. Frame numbers now act as draggable headers for slot reordering; the square below each header toggles whether that slot participates in playback/export. Viewer `Every` now bulk-selects every Nth slot and unselects the rest instead of acting as a playback skip.
- Added a `Hide Off` viewer option beside `Ping Pong` to hide deselected timeline slots while keeping the underlying slot order/export sequence intact.
- Added a `To End` timeline action that moves the selected slot to the end and updates the loop `End` value to the new final slot.
- Added multi-cell timeline selection. Header clicks select one cell, `Shift+Click` selects the continuous range from the last clicked cell, and timeline actions (`Duplicate`, `Move Left`, `Move Right`, `To End`, `Delete Slot`, `Reverse`) now operate on either a single selected cell or multiple selected cells as one group. With `Hide Off` enabled, range selection and group actions apply only to visible enabled cells.
- Added a backend viewer server (`npm run serve-viewer`) with project APIs. The viewer now shows the backend work folder and saves named project JSON files under `viewer-projects/`; `Save Project` / `Cmd+S`, `Save As`, the project list, and `Load Project` preserve crop, slot order, toggles, loop range, FPS, ping-pong, and current slot.
- Replaced the ambiguous viewer `JSON` download with backend `Export Crop+Sequence`. The user enters an export folder, and the backend runs the cropper to write cropped PNG images plus `animation-sequence.json`, `crop-meta.json`, and a contact sheet into that folder.
- Added a `Loop Selected` preview option beside `Ping Pong`. When enabled, playback, arrow-key stepping, and `Fit All` use only the selected enabled cells, while backend export still uses the normal enabled slot sequence.
- Added an `Open Finder` button beside `Export Crop+Sequence`; it asks the backend to create/open the configured export folder in Finder.
- Added `npm run restart-viewer`, which stops any existing viewer server on the target port, starts the backend for a viewer folder, and verifies that `viewer.html` responds.
- Added backend file UX for the viewer: `New Project` lists generated sprite viewer folders from `outputs/sprites-pymatting/` and opens the selected folder without retyping paths, and export folders are populated from the backend.
- Added `Cmd+Click` frame-header selection toggles so individual timeline cells can be added to or removed from the current multi-selection while also toggling that cell's on/off square; `Shift+Click` remains range selection.
- Updated the main Flow prompt and AGENTS guidance to explicitly request a seamless loop where the final frame returns close to the first frame in pose, camera, crop, scale, hair position, and silhouette.
- Prepared a new silver-haired character Flow start frame from `ig_047d8c5f1ebd3203016a2b65a2287481918ad957f01bfa6115.png` as `inputs/flow_idle_input_magenta_019eb957_silver_16x9_1920x1080.png`, with pure magenta background and 1920x1080 canvas.
- Revised the loop prompt after a Flow result turned the character's body. The new wording treats the character as a frozen 2D sprite/cutout, bans body/head/torso rotation and perspective/parallax shifts, and says the loop should be achieved only by hair strands easing back to neutral.
- Tightened the prompt to explicitly require the first and final frames to match exactly. If exact first/final matching conflicts with motion, the prompt now says to reduce hair motion rather than move the body, camera, or pose.
- Simplified the loop wording again: the prompt now asks plainly for the first and final frames to match exactly for seamless looping, without describing the loop requirement as "hair-only."
- Added `**CRITICAL LOOP REQUIREMENT:**` Markdown-style emphasis to the first/final-frame matching sentence so the loop constraint stands out in Flow prompts.
- Moved `Character_idle_animation_loop_202606121152.mp4` from a local download folder into `outputs/google-flow/` and spriteified it with PyMatting FG. The output viewer is under `outputs/sprites-pymatting/character-idle-animation-loop-202606121152/`.
- Quick first/final-frame check on that loop attempt: PyMatting frame 1 vs frame 96 RMSE was about `0.047`, so it is not mathematically identical and should be visually reviewed in the viewer.

## 2026-06-11

Goal: turn the uploaded anime portrait into a subtle idle animation. Desired motion is tiny hair movement only. Pose, hands, fingers, expression, camera, crop, and background should remain locked.

Source assets copied into this project:

- `inputs/sample_reference_original_512.png`
- `inputs/flow_idle_input_magenta_16x9_1920x1080.png`
- `inputs/pixverse_idle_input_magenta_1024.png`

Local Mac tests:

- ComfyUI models were moved to an external local model directory, with the local model folder symlinked there.
- An old local ComfyUI workspace was removed.
- Wan2.2 and LTX local image-to-video tests were too slow or unstable for this precise idle use case.
- Conclusion: local Mac image-to-video is not practical enough for this asset right now.

PixVerse:

- Paid generation could not proceed because credits were zero, even though web credits existed.
- Web generation worked, but it allowed hand or pose drift unless the prompt strongly locked them.
- Extracted frames from a downloaded PixVerse video for Aseprite review in the previous Codex workspace.

Google Flow / Veo:

- Flow must use the image as a `Frame` / start frame, not only as an `Asset` reference, otherwise it may redraw the character.
- Flow prefers 16:9 or 9:16. A 16:9 magenta input was prepared to avoid square-image issues.
- Current practical generation strategy is Google Flow web with Veo 3.1 Lite/Fast, using the free daily Flow credits first. Use 3.1 Lite for cheap iteration and 3.1 Fast when a candidate needs better motion/prompt adherence.
- A paid Gemini/Veo path was considered early, but the repository now keeps only the Flow web workflow.
- Downloaded Google/Flow result moved from a local download folder to `outputs/google-flow/Character_animation_with_subtle_…_202606112011.mp4` for local post-processing.

Post-processing:

- Added a local MP4-to-PNG post-processor that extracts raw frames with `ffmpeg`, then writes same-size transparent PNG frames by chroma-keying the magenta background.
- The chroma-key pass follows the deterministic sprite workflow used by `agent-sprite-forge`: exact/near key-color pixels are removed, then a looser key-color threshold is flood-filled only from frame edges to protect foreground colors.
- Added video-specific magenta halo cleanup and despill because lossy MP4 output leaves purple compression bleed around hair strands and outlines, unlike the solid-magenta PNG sheets usually handled by sprite workflows.
- Output metadata records the source video, frame count, key color, thresholds, and transparency summary stats.

Prompt update:

- Added explicit ban on floating particles, dust, sparkles, magical motes, embers, snow, rain, bokeh dots, decorative effects, background animation, light streaks, and aura.
- Added silent/no-audio wording.

Frame-size rule:

- For Google/Veo start-frame tests, use a frame that already matches the requested video aspect ratio and working frame size.
- Project standard is `1920x1080` for `16:9` and `1080x1920` for `9:16`.
- The 512px original is kept only as a reference sample. Do not use it as the direct start frame unless intentionally testing model behavior.
- If requesting `1080p` output from newer Veo models, keep in mind the API documentation ties 1080p output to 8-second generations.

Audio note:

- Flow/Veo outputs may include native audio. The sprite extraction workflow ignores audio and uses image frames only.
