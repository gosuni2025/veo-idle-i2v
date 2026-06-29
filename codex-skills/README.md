# Codex Skills

This folder contains Codex skills that can be installed into a local Codex
profile.

## Install Sprite Forge PyMatting

From the repository root:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R codex-skills/sprite-forge-pymatting "${CODEX_HOME:-$HOME/.codex}/skills/"
```

Restart Codex after installing the skill.

Verify:

```bash
test -f "${CODEX_HOME:-$HOME/.codex}/skills/sprite-forge-pymatting/SKILL.md"
```

The cleanup script also needs `ffmpeg` on `PATH` and Python packages listed in
`codex-skills/sprite-forge-pymatting/requirements.txt`.
