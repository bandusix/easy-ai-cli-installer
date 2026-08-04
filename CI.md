## Automatic build & release pipeline

The `.github/workflows/build.yml` workflow keeps the offline installer
current with the latest upstream CLIs.

### Triggers

| Trigger | Behaviour |
| --- | --- |
| **Schedule** (daily 04:00 UTC) | Runs `fetch_latest.py --check` against npm and GitHub Releases. If any of the 5 CLIs has shipped a new version, the build jobs fire and a new GitHub release is cut. |
| **`workflow_dispatch`** (manual) | Always builds, even if no upstream CLI changed. |
| **`push`** to `gui_installer.py` / `build.spec` / `scripts/fetch_latest.py` / the workflow itself | Always builds. Lets you ship a UI tweak without waiting for an upstream release. |

### Jobs

1. **`detect-updates`** (Ubuntu) — pure inspection. Calls
   `scripts/fetch_latest.py --check` to probe the latest npm and GitHub
   release of every CLI. Writes a summary to the job summary and to
   `$GITHUB_OUTPUT` so downstream jobs can gate on it.
2. **`build-windows`** (Windows runner) — runs
   `fetch_latest.py --download --platform windows` to refresh the Windows
   payloads, then `pyinstaller build.spec` to bundle the GUI together with
   the payload into `dist/AI_Tools_Installer.exe`. Renamed to
   `AI_Tools_Installer_Windows.exe` and uploaded as an artifact.
3. **`build-macos-arm`** (Apple Silicon runner) — same as above for
   `macos-arm` payloads, then wraps the resulting `.app` in a `.dmg` with
   `hdiutil`. Output: `AI_Tools_Installer_macOS_arm64.dmg`.
4. **`build-macos-x64`** (`macos-13` runner) — same for Intel macs.
   Output: `AI_Tools_Installer_macOS_x64.dmg`.
5. **`release`** (Ubuntu) — only if all three builds succeed AND something
   actually changed. Downloads the artifacts and calls
   `softprops/action-gh-release` to cut a new release with a stable,
   chronologically-sortable tag and auto-generated release notes.

### What the build picks up

| Tool | Source | Output filename in `payload/` |
| --- | --- | --- |
| Claude Code | `https://registry.npmjs.org/@anthropic-ai/claude-code/latest` | `anthropic-ai-claude-code-<ver>.tgz` |
| Gemini CLI | `https://registry.npmjs.org/@google/gemini-cli/latest` | `google-gemini-cli-<ver>.tgz` |
| Kimi Code | `https://registry.npmjs.org/@moonshot-ai/kimi-code/latest` | `moonshot-ai-kimi-code-<ver>.tgz` |
| Lark CLI (npm) | `https://registry.npmjs.org/@larksuite/cli/latest` | `larksuite-cli-<ver>.tgz` |
| Lark CLI (Go binary) | `https://api.github.com/repos/larksuite/cli/releases/latest` | `lark-cli-<platform>.{zip,tar.gz}` |
| Codex | `https://api.github.com/repos/openai/codex/releases/latest` | `codex-<platform>.{zip,tar.gz}` |
| Node.js runtime | `https://nodejs.org/dist/v20.18.0/` | `node-<platform>.{zip,tar.gz}` |

The Node.js version is pinned to the current LTS (`v20.18.0`). Bump it in
`scripts/fetch_latest.py` when a new LTS ships.

### Running it locally

```bash
# 1. Probe (no downloads)
python scripts/fetch_latest.py --check

# 2. Download every payload we know about
python scripts/fetch_latest.py --download

# 3. Build the installer for the current OS
pip install pyinstaller
pyinstaller build.spec --clean

# 4. Inspect the output
ls dist/
# macOS  → AI_Tools_Installer.app  (then wrap in a .dmg with hdiutil)
# Windows → AI_Tools_Installer.exe
```

### First-time setup on a fresh repo

The workflow is self-contained — no secrets are required. The release job
uses the `GITHUB_TOKEN` that GitHub provides automatically to every
workflow run. The first time the schedule fires (or you click
*Run workflow* in the Actions tab), it will:

1. Detect that nothing is in `payload/` and download everything fresh.
2. Build the three installers (~5–10 minutes on each platform).
3. Cut release tag `installer-1-<repo>`.

From then on, daily cron checks skip the build steps entirely when no
upstream CLI has changed.