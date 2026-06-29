---
name: sprite-forge-pymatting
description: Generate or process 2D character sprites and animation frames with solid #FF00FF chroma-key backgrounds, then recover transparent PNGs using PyMatting foreground estimation. Use when Codex needs Sprite Forge style image generation, magenta-background sprites, chroma key removal, transparent character PNGs, hair/edge preservation, or PyMatting FG foreground recovery for PNG frames or frame folders.
---

# Sprite Forge PyMatting

Use this skill for 2D sprite assets that should be generated on a solid magenta
chroma-key background and delivered as transparent PNGs with preserved hair,
soft edges, and foreground color.

## Core Workflow

1. Generate raw sprite art with built-in `image_gen`.
2. Require a 100% solid flat `#FF00FF` background in the prompt.
3. Keep the subject away from magenta, fuchsia, hot pink, and neon pink unless
   the user explicitly accepts the risk.
4. Do not use code-rendered art as the raw source.
5. Postprocess raw PNGs with `scripts/pymatting_chromakey.py`.
6. Show the user a comparison or contact sheet before batch-processing the
   rest of a character set.

For prompt wording, guardrails, and Flow start-frame placement, read
`references/prompt-rules.md` and `references/start-frame-positioning.md` before
writing generation prompts.

## Installation

From the `veo-idle-i2v` repository root:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R codex-skills/sprite-forge-pymatting "${CODEX_HOME:-$HOME/.codex}/skills/"
```

Restart Codex after installing the skill.

The cleanup script requires `ffmpeg` on `PATH` and these Python packages:

```bash
python3 -m pip install -r codex-skills/sprite-forge-pymatting/requirements.txt
```

## PyMatting Processing

Use PyMatting FG as the default chroma-key removal path. Do not solve bad raw
generation by widening a global color threshold. If the background is not a
usable magenta key, regenerate the raw sprite with a stricter prompt.

Run:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/sprite-forge-pymatting/scripts/pymatting_chromakey.py" \
  --input path/to/raw.png \
  --output-dir path/to/output
```

The script writes:

- `frames_pymatting_fg/` transparent PNGs
- `frames_chromakey_alpha/` FFmpeg chromakey alpha previews
- `spriteify-meta.json`
- `contact_sheet_pymatting_fg.png`

The processing pipeline is:

1. FFmpeg `chromakey=0xff00ff:0.24:0.10,format=rgba` creates the alpha matte.
2. `pymatting.estimate_foreground_ml` recovers straight foreground RGB.
3. The recovered RGB is combined with the alpha and saved as RGBA PNG.

Default PyMatting parameters:

- `regularization=1e-5`
- `n_small_iterations=6`
- `n_big_iterations=1`
- `small_size=64`
- `gradient_weight=1.0`

## Batch Discipline

For character candidate sets, process one sample first and show:

- raw magenta PNG
- PyMatting FG transparent result
- any metadata warnings

Only continue the remaining images after the user approves the sample. For a
5-candidate character group, keep the five files in the same folder and preserve
the existing filename shape unless the user requests a new structure.
