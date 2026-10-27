# Codex Notes

<!-- agent-guidance:2026-10-23 -->

## 에이전트 운영과 문맥 사용

- 일반 조사·구현·검증은 메인 에이전트가 직접 수행한다. 독립적인 작업 범위가 있고 전체 완료 시간을 줄이는 경우에만 필요한 수의 서브에이전트를 사용한다. 사용자가 명시한 멀티 에이전트 요청을 우선한다.
- 골 모드·랄프모드는 목표를 지속 수행하는 방식이다. 모드 이름이나 작업 규모만으로 자동 위임하거나 조사·개발·테스트 에이전트를 상시 분리하지 않는다. 메인도 직접 구현·검증한다.
- **이미지·시안·캐릭터 모션 제작은 예외로, 멀티 에이전트 병렬 생성을 유지한다.** 공통 스타일·규격·참조·출력 경로를 먼저 공유하고 자산별 소유권을 나눈다. 기존 이미지 생성 스킬·백엔드·시안 우선 규칙을 따른다.
- 위임할 때는 목표·담당 경로·완료 조건·필요한 자료만 전달한다. 서로 독립적인 작업에 전체 대화·모든 지침·대용량 로그를 반복 전달하지 않는다. 변경 경로·검증 결과·막힌 점 위주로 회신한다.
- 같은 상태의 진행 확인·재지시·중복 조사를 반복하지 않는다. 결과를 기다리는 동안 독립 작업을 진행하고, 새 변경·실패·미해결 문제가 있을 때만 검증 범위를 넓힌다.
- 상세 지침은 아래 색인에서 현재 작업에 해당하는 문서를 **작업 전에 반드시 읽는다**. 관련 주제가 여러 개면 모두 적용하되 전체 문서를 일괄 로딩하지 않는다. 제목만으로 판단하기 어려우면 안내 문서에서 작업 키워드를 검색해 관련 문서를 선택한다.

## 지침 유지·추가

- 새 요청을 기록하기 전에 기존 항목을 검색하고 같은 주제의 현재 규칙을 수정한다. 날짜별 요청을 계속 덧붙이거나 같은 규칙의 사본을 만들지 않는다.
- 이 파일에는 모든 작업에 적용되는 공통 규칙과 상세 문서의 읽기 조건·링크만 둔다. 기능·UI·플랫폼·에셋·배포 명세는 해당 주제 문서에 기록한다.
- 완료 이력·일회성 조사·임시 검증 결과는 기존 작업 기록·인계 문서에 기록한다. 지속 적용되는 사용자 결정은 이력으로만 옮기거나 삭제하지 말고 해당 주제의 유효한 규칙으로 유지한다.
- 새 세부 문서를 만들면 아래 색인에 언제 읽어야 하는지 함께 추가한다. 단순 문서 분할을 위해 중첩 AGENTS.md를 만들지 않는다. CLAUDE.md 등 다른 안내 파일에는 중복 지침 대신 이 파일을 참조한다.
- 이 파일은 UTF-8 기준 권장 8KiB(8,192바이트) 이하, 상한 10KiB(10,240바이트)로 유지한다.
- 작업 시작과 이 파일 수정 후 `wc -c` 등으로 크기를 확인한다. 8KiB를 넘으면 사용자에게 파일 경로·현재 크기·기준·분리 방안을 알린다. 같은 대화에서 크기와 상태가 같으면 경고를 반복하지 않는다.
- 10KiB를 넘으면 사용자에게 알리고 추가 지침을 쌓기 전에 상세 항목을 주제 문서로 분리한다. 공통 규칙과 필수 읽기 조건을 남기고, 정확성·사용자 제약을 희생해 삭제하지 않는다.
- 정리 후 중복·상충·누락·끊어진 링크를 확인한다. 최신 명시적 사용자 결정이 이전 규칙을 대체하면 현재 규칙은 한 곳에서 관리하고 변경 이유는 작업 기록에 남긴다.

## 기존 공통·프로젝트 규칙

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
