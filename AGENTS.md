# Codex Notes

This project is for Google Flow / Gemini / Veo image-to-video tests using a locked anime portrait idle frame.

Important:

- No paid generation script path is tracked in this repo. Do not add or commit credentials, `.env` files, generated media, extracted frames, model weights, or virtual environments.
- When asked to persist repository work, make one intentional git commit for each completed work batch before pushing. Keep generated media, extracted frames, model weights, virtual environments, and `.env` out of git.
- Read `docs/HISTORY.md` before continuing experiments.
- Practical generation is Google Flow web. The old paid Gemini/Veo scripts and `.env.example` have been removed because this workflow uses Flow web.
- The main input is `inputs/flow_idle_input_magenta_16x9_1920x1080.png`.
- Google/Veo start frames should be pre-sized to the requested frame: `1920x1080` for `16:9`, `1080x1920` for `9:16`. Do not feed the 512px sample directly unless intentionally testing drift/cropping.
- Original sample reference is `inputs/sample_reference_original_512.png`.
- The main prompt is `prompts/idle_hair_locked_no_particles.txt`.
- Current preferred postprocess/sprite method is `PyMatting FG`, not outline-alpha or CorridorKey proxy.

Useful commands:

```bash
npm run spriteify-pymatting -- "outputs/google-flow/<downloaded-google-flow-video>.mp4"
npm run restart-viewer -- "outputs/sprites-pymatting/<video-slug>" --port <port>
```

Google Flow web bootstrap:

- When the user wants to start a new Google/Flow job, open Google Flow in the browser.
- Show/copy the saved prompt from `prompts/idle_hair_locked_no_particles.txt`.
- Use the prepared Google-sized frame `inputs/flow_idle_input_magenta_16x9_1920x1080.png` as the start frame / first frame. If a new source image is provided, first create a Google-sized magenta-background frame at `1920x1080` for `16:9` or `1080x1920` for `9:16`; do not upload the 512px reference directly.
- The Flow job should preserve exact first frame, locked camera, fixed hands/fingers/body, front-facing orientation, solid magenta background, and only tiny 2D hair motion.
- The Flow job should require exact first/final frame matching for looping. Mark this in the prompt as `**CRITICAL LOOP REQUIREMENT:**`. The final frame must match the first frame exactly so the video can be used as a seamless looping game idle animation. Keep this loop instruction simple; avoid overexplaining it as "hair-only looping."

After Google Flow download:

- Move the downloaded `.mp4` into `outputs/google-flow/`.
- Spriteify with PyMatting foreground recovery:

```bash
npm run spriteify-pymatting -- "outputs/google-flow/<downloaded-file>.mp4"
```

- Preferred output is `outputs/sprites-pymatting/<video-slug>/frames_pymatting_fg/`.
- The generated `outputs/sprites-pymatting/<video-slug>/viewer.html` should be served with `npm run restart-viewer -- "outputs/sprites-pymatting/<video-slug>" --port <port>` when editing projects, because the backend provides named project save/load under `viewer-projects/`. This script kills any existing listener on the port, starts the backend again, and verifies `viewer.html` responds. Use static `python3 -m http.server` only for read-only review.
- Treat the backend viewer server as a loopback-only local editing tool. It defaults to `127.0.0.1`, only accepts loopback hosts, and has unauthenticated project save/export APIs, so do not expose it on an external network.
- The backend viewer can list and switch generated sprite viewer folders through the `New Project` UI, so prefer that over asking the user to type local paths.
- When giving the user a viewer URL, make sure the local server is actually running first. Check that the port is listening and verify the viewer responds, for example with `lsof -nP -iTCP:<port> -sTCP:LISTEN` and `curl -I http://127.0.0.1:<port>/viewer.html`, before telling the user to open it.
- Keep `Outline Alpha`, `Formula Unmix`, and `CorridorKey Proxy` as comparison/experimental methods only; current judgment is that `PyMatting FG` preserves hair shape best.

Primary quality constraints:

- Exact first frame.
- No zoom, pan, reframing, crop change, or pose change.
- No body turn, head turn, torso rotation, breathing, leaning, parallax, perspective shift, or 3D rotation.
- Hands and fingers must stay fixed.
- Only tiny hair motion is allowed.
- Critical loop requirement: final frame must match the first frame exactly for seamless looping; if that conflicts with motion, keep motion smaller and more subtle.
- No particles, dust, sparkles, motes, weather, aura, or background effects.
