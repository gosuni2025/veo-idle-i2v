# Start Frame Positioning

Use this guide when preparing a still character sprite or expression variant as
a Google Flow / Veo start frame.

## Target Canvas

Use one of the project standard canvas sizes:

- `1920x1080` for `16:9`
- `1080x1920` for `9:16`

The background must be `100% solid flat #FF00FF`, exact RGB `255, 0, 255`.

## Placement Rules

- Center the character horizontally on the target canvas.
- Keep the character bottom-aligned to a stable baseline.
- Keep the full head, hair, hands, props, clothing edges, and silhouette inside
  the canvas.
- Leave visible magenta margin on every side.
- Preserve the approved sprite's apparent scale, crop, camera angle, pose, and
  hand positions.
- For expression variants, keep the same canvas size, x center, bottom baseline,
  apparent scale, and crop as the approved reference expression.
- If a generated expression visibly changes pose, size, camera, crop, or body
  placement, regenerate from the approved reference instead of scaling or
  warping that failed output.

For standing or thigh-up VN sprites, use a bottom-contact baseline: the lowest
visible non-transparent part of the sprite should land on the same y coordinate
across the base sprite and every expression variant. If the sprite is a portrait
without feet, preserve the existing lower crop edge instead of inventing more
body.

## Transparent Sprite To Magenta Start Frame

When compositing a transparent PNG into a Flow start frame:

1. Create a new RGB canvas at the target size filled with `#FF00FF`.
2. Measure the sprite's non-transparent alpha bounding box.
3. Choose a baseline y coordinate for the sprite bottom.
4. Paste the sprite so the alpha bounding box center is on the canvas center x.
5. Paste the sprite so the alpha bounding box bottom sits on the baseline y.
6. Verify the final PNG is RGB or RGBA with a pure magenta background outside
   the sprite.

For a new character, choose a baseline that leaves useful top margin while
keeping the character large enough for Flow to preserve details. For expression
variants, reuse the approved reference baseline exactly.

## Prompt Clauses

```text
Composition and placement: one centered character on the canvas, horizontally
centered, bottom-aligned to a stable baseline, full head, hair, hands, props,
clothing edges, and silhouette fully inside the canvas, generous visible
magenta margin on all sides, same apparent scale and camera angle, no crop
change, no reframing.
```

```text
Placement lock: preserve the approved reference image's exact canvas size,
horizontal center, bottom baseline, apparent scale, crop, camera angle, pose,
hand positions, prop positions, and bottom contact. Change only the requested
facial expression.
```

## Validation Checklist

- Canvas size is exactly `1920x1080` or `1080x1920`.
- Background outside the subject is exact `#FF00FF`.
- Character is horizontally centered on the canvas.
- Character bottom contact matches the chosen baseline.
- No part of the head, hair, hands, props, or clothing is cropped.
- Expression variants match the approved reference's canvas size, x center,
  bottom baseline, apparent scale, and crop.
- The Flow prompt still says to preserve first-frame framing, crop, scale,
  composition, and camera.
