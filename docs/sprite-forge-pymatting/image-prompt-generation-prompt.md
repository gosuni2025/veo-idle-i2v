# Image Prompt Generation Prompt

Use this document to generate raw image prompts for Sprite Forge PyMatting.
These prompts are for still VN sprites or still start-frame assets. For Google
Flow / Veo idle animation, use `prompts/idle_hair_locked_no_particles.txt`
after the still magenta start frame is ready.

## Copy-Paste Prompt Generator

Paste this into Codex or another prompt-writing assistant when you want a final
image-generation prompt for a new sprite.

```text
You are writing an image-generation prompt for a visual novel game sprite that
will be postprocessed with PyMatting foreground recovery.

Return:
1. A concise positive prompt.
2. A concise negative prompt.
3. A short validation checklist for the generated raw image.

Hard requirements:
- The raw image must contain exactly one sprite subject.
- The subject must be on a 100% solid flat #FF00FF background, exact RGB 255, 0, 255.
- The background must have no gradients, texture, shadows, floor plane, scenery, border, reflection, text, logo, or watermark.
- The subject must not use magenta, fuchsia, hot pink, neon pink, or purple clothing/accessories unless explicitly requested.
- The subject must be fully inside the canvas, including full head, hair, hands, props, clothing edges, and effects.
- Leave visible magenta margin on every side.
- If the image will become a Flow start frame, the subject must be horizontally centered on the target canvas and bottom-aligned to a stable baseline.
- The final prompt must be ready for PyMatting chroma-key cleanup.

Style base:
visual novel character sprite, thigh-up solo character, clean lineart,
flat cel-shaded anime rendering, polished modern mobile RPG and visual novel
anime face proportions, refined hand-drawn illustration feeling, organic line
weight, subtle pencil-and-ink warmth, delicate hand-painted cel shade edges,
not photorealistic, not retro VN, not chibi.

Character brief:
- Character name/id: {character_name}
- Role/archetype: {role}
- Age impression: {age_impression}
- Hair: {hair}
- Face/eyes: {face_and_eyes}
- Outfit: {outfit}
- Props: {props}
- Pose/framing: {pose_and_framing}
- Expression: {expression}
- Mood/world: {mood_world}
- Colors to avoid on subject: magenta, fuchsia, hot pink, neon pink, purple.

Keep the output practical. Do not add scenery, lighting setups, body-language
changes, new props, or narrative details that could make the generator change
the pose, crop, or design.
```

## New Character Positive Template

```text
visual novel character sprite,
thigh-up solo character,
clean lineart,
flat cel-shaded anime rendering,
polished modern mobile RPG and visual novel anime face proportions,
refined hand-drawn illustration feeling,
organic line weight,
subtle pencil-and-ink warmth,
delicate hand-painted cel shade edges,
not photorealistic,
not retro VN,
not chibi,
{character role},
{hair},
{face and eyes},
{outfit},
{props},
{expression},
{pose},
centered composition,
horizontally centered on the canvas,
bottom-aligned to a stable baseline,
full head, hair, hands, props, and clothing edges fully inside the canvas,
generous visible magenta margin on all sides,
100% solid flat #FF00FF chroma-key background,
exact RGB 255, 0, 255 background,
one uniform background color,
hard clean boundary between subject and background,
PyMatting foreground recovery ready composition
```

## New Character Negative Template

```text
low quality, worst quality,
bad anatomy, extra fingers, missing fingers, extra limbs,
changed face, inconsistent outfit,
magenta clothing, fuchsia clothing, hot pink clothing, neon pink clothing,
purple clothing or accessories,
white background, transparent background in raw generation,
complex background, scenery, floor plane, gradients, texture, shadows,
reflection, border,
text, watermark, logo,
cropped head, cropped hair, cropped hands, cropped props,
multiple characters,
rough sketch, messy lineart,
photorealism,
retro VN, chibi,
sexualized pose
```

## Expression Replacement Prompt

Use this when an approved transparent runtime sprite already exists and only the
face should change.

```text
Use the image just shown as the exact visual reference.
Generate exactly one raw visual novel sprite only.
Preserve the same character identity, face structure, hairstyle, outfit,
accessories, pose, hand positions, prop positions, crop, apparent scale, camera
angle, centered bottom-aligned framing, and bottom contact.
Preserve the approved reference image's canvas size, horizontal center, bottom
baseline, and lower crop edge.

Expression change only: {expression_clause}.

Keep everything else unchanged.
Background must be 100% solid flat #FF00FF, exact RGB 255, 0, 255.
No sheet, no collage, no gradients, no scenery, no shadows, no borders, no new
props, no pose changes, no crop changes, no scale changes, no camera changes,
no body-language changes.
```

Compact expression clauses work best:

```text
Expression change only: worried, raised inner brows, tense eyes, uncertain mouth
Expression change only: surprised, widened eyes, slightly open mouth
Expression change only: serious, focused eyes, closed neutral mouth
Expression change only: angry, lowered brows, sharp eyes, tight mouth
Expression change only: soft smile, relaxed eyes, small gentle smile
```

## Still Start Frame For Flow

For this repository, still sprites can become Flow start frames. After creating
and approving a transparent or magenta sprite, prepare the start frame at the
requested Flow aspect ratio:

- `1920x1080` for `16:9`
- `1080x1920` for `9:16`

The start frame should keep the sprite on a pure magenta background. Then use
the Flow animation prompt:

```text
prompts/idle_hair_locked_no_particles.txt
```

Do not upload a small 512px sample directly to Flow unless intentionally testing
model drift or cropping behavior.

For placement, follow `docs/sprite-forge-pymatting/start-frame-positioning.md`:
center the character horizontally on the target canvas, keep the sprite
bottom-aligned to a stable baseline, and reuse the same canvas size, x center,
bottom baseline, scale, and crop for expression variants.

## Raw Image Validation Checklist

Before PyMatting cleanup, confirm:

- The background is exactly flat magenta, not purple or textured.
- There are no shadows, floor contact shadows, gradients, borders, or scenery.
- The subject has no large magenta/pink/purple foreground regions.
- The full silhouette is inside the canvas with margin on all sides.
- Hands, hair, props, and clothing edges are not cropped.
- A single subject is present.

If any item fails, regenerate the raw image instead of trying to rescue it with
looser chroma-key thresholds.
