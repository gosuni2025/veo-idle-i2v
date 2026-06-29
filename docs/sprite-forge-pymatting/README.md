# Sprite Forge PyMatting

Sprite Forge PyMatting is the local Codex sprite workflow for producing
transparent 2D game sprites from AI-generated images or magenta-background
video frames.

Use it when the asset should end as a transparent PNG sprite and the raw source
can be generated or rendered against a pure magenta chroma-key background.

## Core Idea

1. Create the raw source on a `100% solid flat #FF00FF` background.
2. Keep the subject away from magenta, fuchsia, hot pink, and neon pink.
3. Use FFmpeg chromakey to create an alpha matte.
4. Use PyMatting foreground estimation to recover the foreground RGB.
5. Save transparent RGBA PNGs and review the contact sheet or viewer.

This is preferred over broad threshold cleanup because PyMatting FG usually
preserves hair, soft edges, and outline color better.

## Codex Skill Install Check

This repository includes a public copy of the `$sprite-forge-pymatting` Codex
skill:

```text
codex-skills/sprite-forge-pymatting/
```

Codex discovers local skills from `$CODEX_HOME/skills`, which defaults to
`~/.codex/skills`. Install the skill from the repository root:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R codex-skills/sprite-forge-pymatting "${CODEX_HOME:-$HOME/.codex}/skills/"
```

Restart Codex after installing the skill.

Check whether the skill is installed:

```bash
test -f "${CODEX_HOME:-$HOME/.codex}/skills/sprite-forge-pymatting/SKILL.md"
```

For a clean reinstall, replace only that installed skill folder:

```bash
rm -rf "${CODEX_HOME:-$HOME/.codex}/skills/sprite-forge-pymatting"
cp -R codex-skills/sprite-forge-pymatting "${CODEX_HOME:-$HOME/.codex}/skills/"
```

The skill's single-image cleanup script needs:

- `ffmpeg`
- Python packages: `pymatting`, `numpy`, `Pillow`

Install the Python dependencies into this repo's `.venv-ml` environment:

```bash
.venv-ml/bin/python -m pip install -r codex-skills/sprite-forge-pymatting/requirements.txt
```

Verify:

```bash
.venv-ml/bin/python -c "import pymatting, numpy, PIL; print('PyMatting ready')"
ffmpeg -version
```

## Repository Video Workflow

For Google Flow / Veo idle videos in this repository, download the Flow MP4,
move it under `outputs/google-flow/`, then run:

```bash
npm run spriteify-pymatting -- "outputs/google-flow/<downloaded-file>.mp4"
```

The main script is:

```text
scripts/spriteify_video_pymatting.py
```

The script writes:

- `outputs/sprites-pymatting/<video-slug>/frames_raw/`
- `outputs/sprites-pymatting/<video-slug>/frames_chromakey_alpha/`
- `outputs/sprites-pymatting/<video-slug>/frames_pymatting_fg/`
- `outputs/sprites-pymatting/<video-slug>/spriteify-meta.json`
- `outputs/sprites-pymatting/<video-slug>/contact_sheet_pymatting_fg.jpg`
- `outputs/sprites-pymatting/<video-slug>/viewer.html`

The preferred final sprite frames are in `frames_pymatting_fg/`.

## Direct Single-Image Skill Workflow

The original Codex skill also has a single-image or frame-folder script:

```bash
.venv-ml/bin/python codex-skills/sprite-forge-pymatting/scripts/pymatting_chromakey.py \
  --input path/to/raw.png \
  --output-dir path/to/output
```

If you have installed the skill into Codex and want to run the installed copy:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/sprite-forge-pymatting/scripts/pymatting_chromakey.py" \
  --input path/to/raw.png \
  --output-dir path/to/output
```

That path is useful when creating a still character candidate or expression
sprite directly from image generation instead of from a Flow MP4.

## Prompt Rules

Raw sprite prompts must require:

- `100% solid flat #FF00FF` background
- `exact RGB 255, 0, 255` background
- no gradients, texture, shadows, floor plane, scenery, or border
- one subject per file unless a sheet or pack is explicitly requested
- centered horizontal placement when preparing a Flow start frame
- stable bottom-aligned baseline when preparing a Flow start frame or expression variant
- full head, hair, hands, props, clothing, and effects inside the canvas
- visible magenta margin on all sides
- no magenta/fuchsia/hot pink/neon pink on the subject

If the generated raw image does not have a usable solid magenta key, regenerate
the raw source with a stricter prompt. Do not try to fix a bad source by simply
widening a global threshold.

## PyMatting Defaults

The workflow uses FFmpeg alpha extraction plus PyMatting foreground recovery:

```text
chromakey=0xff00ff:0.24:0.10,format=rgba
```

Default PyMatting parameters:

- `regularization=1e-5`
- `n_small_iterations=6`
- `n_big_iterations=1`
- `small_size=64`
- `gradient_weight=1.0`

## Review Discipline

For a character set, process one sample first and review:

- raw magenta PNG
- transparent PyMatting FG result
- contact sheet or viewer
- metadata warnings

Continue the rest of the batch only after the sample is acceptable.

## Related Files

- [Image prompt generation prompt](image-prompt-generation-prompt.md)
- [Start frame positioning](start-frame-positioning.md)
- [Flow idle prompt](../../prompts/idle_hair_locked_no_particles.txt)
- [Project history](../HISTORY.md)
