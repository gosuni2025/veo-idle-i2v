# Veo Idle I2V

고정 포즈 애니메이션풍 idle 애니메이션을 위한 Google Flow 웹 기반 이미지-투-비디오 워크플로 프로젝트입니다.

## 설정

```bash
git clone https://github.com/gosuni2025/veo-idle-i2v.git
cd veo-idle-i2v
npm install
python3 -m venv .venv-ml
.venv-ml/bin/python -m pip install -r codex-skills/sprite-forge-pymatting/requirements.txt
```

## 문서

- [Sprite Forge PyMatting](docs/sprite-forge-pymatting/README.md): 마젠타 배경 스프라이트를 PyMatting FG로 투명 PNG화하는 워크플로 요약입니다.
- [Image Prompt Generation Prompt](docs/sprite-forge-pymatting/image-prompt-generation-prompt.md): 신규 캐릭터/표정 스프라이트용 이미지 생성 프롬프트 템플릿입니다.
- [Start Frame Positioning](docs/sprite-forge-pymatting/start-frame-positioning.md): 캐릭터를 Flow 시작 프레임 중앙/하단 기준선에 배치하는 지침입니다.

## Codex 스킬 설치

이 repo에는 공개 배포용 `$sprite-forge-pymatting` 스킬 폴더가 포함되어 있습니다.

```text
codex-skills/sprite-forge-pymatting/
```

repo를 받은 뒤 Codex에서 이 스킬을 쓰려면 로컬 Codex 스킬 폴더로 복사하고 Codex를 재시작하세요.

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R codex-skills/sprite-forge-pymatting "${CODEX_HOME:-$HOME/.codex}/skills/"
```

설치 확인:

```bash
test -f "${CODEX_HOME:-$HOME/.codex}/skills/sprite-forge-pymatting/SKILL.md"
```

자세한 설치/사용법은 [Sprite Forge PyMatting 문서](docs/sprite-forge-pymatting/README.md#codex-skill-install-check)를 참고합니다.

PyMatting 실행 환경은 이 저장소의 `.venv-ml`을 사용합니다. `ffmpeg`도 `PATH`에서 실행 가능해야 합니다.

```bash
.venv-ml/bin/python -c "import pymatting, numpy, PIL; print('PyMatting ready')"
ffmpeg -version
```

## Google Flow 웹 워크플로

현재 생성 경로는 Google Flow 웹입니다. 이 고정 idle 애니메이션에서는 Flow 웹 작업이 유료 생성 반복 작업보다 더 적합했기 때문에, 저장소의 이전 Gemini/Veo 실행 스크립트와 `.env.example`은 제거했습니다.

Flow 작업을 시작할 때:

- `inputs/flow_idle_input_magenta_16x9_1920x1080.png`를 시작 프레임 / 첫 프레임으로 사용합니다.
- `prompts/idle_hair_locked_no_particles.txt`를 프롬프트로 사용합니다.
- 첫 프레임은 정확히 유지하고, 카메라는 고정하며, 손/손가락/몸은 움직이지 않고, 배경은 단색 마젠타로 유지하며, 머리카락만 아주 작게 움직이게 합니다.
- 새 소스 이미지를 도입하는 경우, 먼저 Google 크기에 맞춘 마젠타 시작 프레임을 준비합니다. `16:9`는 `1920x1080`, `9:16`은 `1080x1920`입니다. 캐릭터는 [Start Frame Positioning](docs/sprite-forge-pymatting/start-frame-positioning.md) 기준대로 캔버스 가로 중앙과 안정된 하단 기준선에 맞춥니다.
- `inputs/sample_reference_original_512.png`는 원본 참조 샘플로만 유지하고, Flow 시작 프레임으로 직접 업로드하지 않습니다.

Google Flow에서 다운로드한 뒤 MP4를 `outputs/google-flow/`로 옮기고 다음을 실행합니다.

```bash
npm run spriteify-pymatting -- "outputs/google-flow/<downloaded-file>.mp4"
```

권장 출력:

- `outputs/sprites-pymatting/<video-slug>/frames_pymatting_fg/`: 투명 PNG 스프라이트 프레임
- `outputs/sprites-pymatting/<video-slug>/viewer.html`: 로컬 애니메이션 뷰어
- `outputs/sprites-pymatting/<video-slug>/contact_sheet_pymatting_fg.jpg`: 빠른 검토용 시트

프로젝트 저장/불러오기가 필요한 검토 작업에서는 백엔드 서버로 뷰어를 서빙하세요. `restart-viewer` 사용을 권장합니다. 이 명령은 해당 포트의 기존 리스너를 종료하고, 백엔드를 다시 시작한 뒤, `viewer.html`이 응답하는지 확인합니다.

뷰어 서버는 루프백 전용 로컬 편집 도구입니다. 기본 호스트는 `127.0.0.1`이며, 인증 없는 프로젝트 저장/내보내기 API가 있으므로 외부 네트워크에 노출하지 마세요. 서버 스크립트는 루프백 호스트만 허용합니다.

```bash
npm run restart-viewer -- "outputs/sprites-pymatting/<video-slug>" --port 8767
```

프로젝트 JSON 파일을 다른 위치에 저장하려면 `--projects-dir /path/to/projects`를 전달하세요.

## 게임 크롭 워크플로

16:9 Flow 프레임은 생성용 캔버스이며 최종 게임 스프라이트 크기가 아닙니다. `spriteify-pymatting` 이후 생성된 `viewer.html`을 열고, 크롭 영역을 설정한 다음, 하나의 공통 사각형으로 모든 투명 프레임을 크롭합니다.

뷰어에서:

- 이미지 위에서 드래그하여 크롭 박스를 설정합니다.
- 현재 프레임에는 `Fit Frame`을 사용하고, 현재 미리보기 프레임 세트 전체의 알파 경계를 합치려면 `Fit All`을 사용합니다.
- 하단 타임라인을 사용해 미리보기/내보내기용 프레임을 켜거나 끄고, 애니메이션 슬롯을 복제하거나 이동하고, 루프 시작/끝, 미리보기 FPS, 핑퐁 재생을 설정하며, 원하지 않는 슬롯을 삭제합니다.
- 프레임 헤더를 클릭해 선택합니다. `Cmd+Click`으로 헤더를 클릭하면 해당 셀 하나를 추가/제거하고 선택 상태와 on/off 사각형을 함께 토글합니다. 다른 헤더를 `Shift+Click`하면 마지막으로 클릭한 셀부터 해당 셀까지의 연속 범위를 선택합니다.
- `Hide Off`가 켜져 있으면 `Shift+Click` 범위 선택은 보이는 활성 셀만 포함합니다.
- 현재 선택 영역에 `Duplicate`, `Move Left`, `Move Right`, `To End`, `Delete Slot`, `Reverse`를 사용할 수 있습니다. 셀 하나만 선택하면 단일 셀 작업으로 동작하고, 여러 셀을 선택하면 그룹으로 함께 이동합니다.
- `To End`는 `End`를 새 최종 슬롯으로 업데이트합니다.
- `Ping Pong` 옆의 `Loop Selected`를 사용하면 선택된 활성 셀만 미리보기 루프로 재생할 수 있습니다. 이는 미리보기 보조 기능이며, 백엔드 내보내기는 여전히 활성화된 슬롯 순서를 사용합니다.
- 편집 중 선택 해제된 타임라인 슬롯을 임시로 숨기려면 `Hide Off`를 사용합니다.
- 바로 실행할 수 있는 크롭 명령을 복사하려면 `Copy Cmd`를 사용합니다.
- `Export` 폴더를 입력하고 `Export Crop+Sequence`를 사용하면 백엔드를 통해 크롭된 PNG 이미지와 애니메이션 JSON을 작성합니다.
- `Export Crop+Sequence` 옆의 `Open Finder`를 사용하면 해당 내보내기 폴더를 Finder에서 생성/열기 할 수 있습니다.
- `New Project`를 사용하면 `outputs/sprites-pymatting/`에 있는 다른 생성 스프라이트 뷰어 폴더를 선택하고, 경로를 다시 입력하지 않고 열 수 있습니다.
- 뷰어는 백엔드 작업 폴더와 프로젝트 폴더를 표시합니다. 이름이 있는 프로젝트 파일은 해당 작업 폴더 안의 `viewer-projects/` 아래에 저장됩니다.
- 현재 편집기 상태를 `Project` 필드의 이름으로 저장하려면 `Save Project` 또는 `Cmd+S`를 사용합니다.
- 새 프로젝트 이름을 입력하려면 `Save As`를 사용합니다.
- 프로젝트 목록과 `Load Project`를 사용해 이 뷰어의 저장된 백엔드 프로젝트를 복원합니다.

크롭 좌표는 원본 1920x1080 시작 프레임이 아니라 최종 추출 프레임 해상도를 기준으로 합니다. 예를 들어 Google Flow가 1280x720을 출력했다면, 뷰어와 크롭 명령은 1280x720 좌표를 사용합니다.

명령 형태:

```bash
npm run crop-sprites -- "outputs/sprites-pymatting/<video-slug>" --x 300 --y 0 --width 700 --height 720
```

내보낸/프로젝트 JSON 프리셋을 사용하는 경우:

```bash
npm run crop-sprites -- "outputs/sprites-pymatting/<video-slug>" --crop-json sprite-export-settings.json
```

프레임 트림/내보내기 옵션은 1부터 시작하며 양 끝을 포함합니다.

```bash
npm run crop-sprites -- "outputs/sprites-pymatting/<video-slug>" \
  --x 300 --y 0 --width 700 --height 720 \
  --start-frame 8 --end-frame 72 --delete-frames 15,24-26 \
  --frame-step 3 --fps 12 --ping-pong
```

명시적인 시퀀스 없이 크롭퍼를 사용할 때 `--frame-step 3`은 소스 프레임 `start`, `start + 3`, `start + 6` 순서로 계속 유지합니다. 뷰어에서 `Every`는 일괄 토글 명령입니다. 현재 루프 범위 안에서 N번째마다 슬롯을 선택하고 나머지는 선택 해제합니다. 복사된 명령과 백엔드 내보내기는 이후 `sequence_frames`를 통해 정확히 선택된 슬롯 시퀀스를 사용합니다.

뷰어의 프레임 번호는 드래그 가능한 슬롯 헤더입니다. 헤더를 드래그하면 애니메이션 순서를 바꿀 수 있습니다. 그 아래 사각형을 클릭하면 해당 슬롯을 미리보기와 내보내기에 포함하거나 제외할 수 있습니다. `Duplicate`, `Move Left`, `Move Right`도 원본 이미지 파일이 아니라 애니메이션 슬롯을 편집합니다. 내보내기 JSON에는 `sequence_frames`가 포함되며, 크롭퍼는 각 고유 소스 이미지를 한 번씩만 작성하고 `crop-meta.json`에는 숫자 애니메이션 참조를 기록합니다.

- `unique_frame_count`: 실제로 내보낸 PNG 파일 수
- `animation_indices`: 고유하게 내보낸 이미지에 대한 인덱스로 표현한 게임 애니메이션 순서
- `animation_sequence`: 슬롯별 소스 프레임, 이미지 인덱스, 파일 매핑

이를 통해 게임 엔진은 각 고유 PNG를 하나씩 가져오거나 캐시한 뒤, 중복 프레임을 포함한 최종 애니메이션을 숫자 시퀀스로 구성할 수 있습니다.

프로젝트 저장/불러오기는 백엔드 뷰어 서버가 필요합니다. 정적 파일 서빙은 읽기 전용 검토에는 여전히 괜찮지만, `viewer-projects/` 아래에 이름 있는 프로젝트 파일을 저장할 수는 없습니다.

백엔드 내보내기에도 뷰어 서버가 필요합니다. 상대 내보내기 폴더는 뷰어 작업 폴더를 기준으로 해석되며, 절대 경로도 허용됩니다. 내보내기 출력에는 크롭된 PNG 파일, `animation-sequence.json`, `crop-meta.json`, `contact_sheet_crop.jpg`가 포함됩니다.

크롭된 프레임은 소스 프레임 디렉터리 옆에 작성됩니다. 예:

- `outputs/sprites-pymatting/<video-slug>/frames_pymatting_fg_crop_x300_y0_w700_h720/`
- `animation-sequence.json`: 고유 이미지 파일과 `animation_indices`를 포함한 게임용 시퀀스 데이터
- `crop-meta.json`: 소스 크기, 크롭 사각형, 프레임 수, fps, 핑퐁 플래그, 루프 범위, 프레임 스텝, 삭제된 프레임, 소스-출력 프레임 맵
- `contact_sheet_crop.jpg`: 빠른 크롭 검토용 시트

크롭퍼는 기본적으로 내보낸 게임 프레임을 `frame_0000.png`부터 다시 번호 매깁니다. 원본 소스 프레임 파일명이 필요한 경우에만 `--preserve-names`를 전달하세요.

## 프레임 후처리

레거시 결정론적 크로마키 후처리:

```bash
python3 -m pip install -r requirements.txt
npm run postprocess
```

특정 파일을 대상으로 하려면:

```bash
npm run postprocess -- "outputs/google-flow/Character_animation_with_subtle_…_202606112011.mp4"
```

출력은 `outputs/postprocess/<video-slug>/`에 작성됩니다.

- `frames_raw/`: 원본 추출 PNG 프레임
- `frames_transparent/`: 알파가 있는 동일 크기 PNG 프레임
- `postprocess-meta.json`: 소스, 프레임 수, 임계값, 요약 통계

유용한 튜닝 플래그:

```bash
npm run postprocess -- --max-frames 5 --bg-color auto --hard-threshold 55 --edge-threshold 165 --fringe-threshold 300
```

비디오에 머리카락이나 외곽선 주변의 보라색 MP4 압축 번짐이 있다면, 비디오별 정리 옵션을 조정하세요.

```bash
npm run postprocess -- --halo-radius 2 --despill-radius 3 --despill-amount 0.85 --border-cleanup-depth 2
```

실제 캐릭터에 마젠타/보라색 전경 디테일이 있다면 더 안전한 edge-only 프린지 모드를 사용하세요.

```bash
npm run postprocess -- --fringe-mode edge
```

최종 스프라이트 프레임에는 대신 PyMatting FG 파이프라인을 권장합니다.

```bash
npm run spriteify-pymatting -- "outputs/google-flow/<downloaded-file>.mp4"
```

## 현재 프롬프트 우선순위

- 첫 프레임을 정확히 유지하고, 재디자인하지 않습니다.
- 손, 손가락, 얼굴, 몸, 옷, 액세서리, 카메라, 크롭, 마젠타 배경을 고정합니다.
- 머리카락만 미묘하게 움직입니다.
- particles, dust, sparkles, motes, weather, aura, decorative effects를 넣지 않습니다.
- Flow 결과의 오디오는 스프라이트 추출 과정에서 사용하지 않습니다.
