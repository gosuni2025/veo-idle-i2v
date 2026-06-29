# Prompt Rules

Use these rules when creating raw images for PyMatting chroma-key removal.

## Required Background

- Background must be `100% solid flat #FF00FF`.
- Say `exact RGB 255, 0, 255` when the model tends to produce darker magenta.
- No gradients, no texture, no shadows, no floor plane, no scenery, no border.
- Keep a hard, clean boundary between subject and background.

## Subject Rules

- One subject per file unless the user asks for a pack or sheet.
- If the image will become a Flow start frame, center the subject horizontally
  on the target canvas and bottom-align it to a stable baseline.
- Keep full head, hair, hands, props, clothing, tails, capes, and effects inside
  the canvas.
- Leave visible magenta margin on all sides.
- Avoid magenta/fuchsia/hot pink/neon pink on the subject. If those colors are
  required, warn the user and test one sample first.

## Style

For visual novel character candidates:

```text
visual novel character sprite, thigh-up solo character, clean lineart,
flat cel-shaded anime rendering, polished modern mobile RPG and visual novel
anime face proportions, refined hand-drawn illustration feeling, organic line
weight, subtle pencil-and-ink warmth, delicate hand-painted cel shade edges,
not photorealistic, not retro VN, not chibi
```

## Reference Images

When preserving an existing design:

1. Call `view_image` first so the reference is visible.
2. Prompt with `Use the image just shown as the visual reference`.
3. Preserve silhouette, palette, face/eyes, clothing, accessories, and material
   language.
4. Let only the requested background, pose, frame, or action change.

## Rejection Rules

Regenerate the raw image instead of forcing postprocessing when:

- The background is not clearly magenta.
- The background has gradients, texture, scenery, or shadows.
- Subject colors include large magenta/fuchsia regions that collide with the key.
- Body parts or props are cropped.
