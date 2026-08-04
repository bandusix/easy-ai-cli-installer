# Easy AI CLI Installer

A one-click GUI installer for **5 popular AI coding CLIs** on macOS and
Windows, based on
[`bandusix/easy-codex-and-claude-cli-setup`](https://github.com/bandusix/easy-codex-and-claude-cli-setup)
with two additions:

1. **Claude API relay support** — let users point Claude Code at a custom
   `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` pair (e.g. a private
   gateway) without polluting the user's shell rc, registry, or any other
   tool's environment.
2. **Online source mode** — a segmented control at the top of the window
   lets users pick between fetching the **latest** release (from npm and
   GitHub Releases, so the install is always current) and the **bundled**
   payload shipped inside the .exe / .dmg (the original 100 % offline
   behaviour).

Everything else — UX, language switcher, three-language support, hand-drawn
Fluent / macOS-native UI, the 5 installer functions — is inherited from
upstream.

## What ships in this fork

| File | Purpose |
| --- | --- |
| `gui_installer.py` | The full Tkinter application. Drop this into the upstream repo to replace the original. |
| `build.spec` | PyInstaller spec. Bundles `payload/` into the executable so the offline mode keeps working after packaging. |
| `scripts/fetch_latest.py` | Stdlib-only probe + download script. Used by the CI to detect and pull new CLI versions. |
| `.github/workflows/build.yml` | The "auto-update" workflow. Runs daily, builds new installers on any CLI update, and cuts a GitHub release. |
| `LICENSE` | MIT, identical to upstream. |
| `.gitignore` | Excludes `payload/`, build artifacts, IDE noise. |

> The repo intentionally **does not** contain a `payload/` directory or a
> `.github/workflows/` build pipeline. The Python source is meant to be
> merged back into the upstream project, where the existing CI continues to
> build the offline payload and produce the signed `.exe` / `.dmg`.

## Changes vs upstream

### 1. Claude API Configuration card

A new "Claude API Configuration" card sits between the tools list and the
install button. It contains two text fields — **Base URL** and **Auth Token**
— and is only enabled when the Claude toggle is on. The card has:

- A **left accent stripe** in Claude's brand colour (`#CC785C`).
- A header `Claude API Configuration` with an `OPTIONAL` pill on the right.
- A short hint describing the two modes.
- Labels rendered in uppercase + tracked-out letterspacing for English
  (CJK labels render as-is).
- Placeholders shown in grey when the field is empty and unfocused.
- The Auth Token field masks input with `•` until focused.

The values flow into a new `install_claude_code(source_mode, base_url,
auth_token)` function. The two are validated up-front: if only one is filled
in, the installer refuses to proceed (the error message is also localised).

The shim installed at `bin_dir/claude` is rewritten so the two env vars
are set only when that command runs. Nothing else on the system sees them:

- **Windows** — `claude.cmd` is a `.bat` wrapper that runs
  `set ANTHROPIC_BASE_URL=…` then `set ANTHROPIC_AUTH_TOKEN=…` before
  delegating to the upstream Node-based shim. CRLF line endings, written
  in binary mode so Windows text-mode doesn't double-`\r`.
- **macOS** — `bin_dir/claude` becomes a tiny `#!/bin/sh` wrapper that
  `export`s the two variables and `exec`s the upstream binary.

If both fields are blank, the shim is the original one-liner, so users
who only want the official Anthropic endpoint see no change in behaviour.

### 2. Source: Latest / Bundled

A new segmented control sits in the chrome row between the header and the
tools card. Two pills:

- **Latest (online)** *(default)* — for every selected tool:
  - Claude / Gemini / Kimi / Lark → `npm install -g <pkg>@latest`
  - Codex → fetches the latest GitHub release of
    `openai/codex` and downloads the platform-matching asset.
- **Bundled (offline)** — the original behaviour: extract the tgz / binary
  from `payload/` that was built by the upstream CI.

Helpers added to `gui_installer.py`:

- `_http_get_json(url)` — stdlib-only JSON fetch with a User-Agent that
  GitHub's API accepts.
- `_download_to(url, dest, progress_cb=None)` — stream a URL to disk
  with a 180-second timeout.
- `_github_latest_release(repo, asset_suffix)` — pick the right
  `browser_download_url` for the current OS.

### 3. Cosmetic

The original code already had a hand-drawn Tkinter UI; I tweaked two
things while I was in the file:

- The API card uses more vertical breathing room, an 8/18 px label-to-field
  / field-to-field rhythm, and a 32 px tall input rather than 26 px.
- The source toggle is a true segmented control: clicking a pill flips
  the active state and repaints both pills.

## Why a fork, not a PR

The upstream README still advertises a **100 % offline install** as a
headline feature. Online mode is a strict superset of bundled mode, but
forking keeps the upstream's "guaranteed offline" promise intact while this
repo can evolve the relay / latest-fetch story independently.

If upstream wants either feature, both are designed to merge as a single
diff against `gui_installer.py`.

## Running the source directly

The Python file is self-contained. From the repo root:

```bash
python gui_installer.py
```

You will see the GUI but the install will fail because the bundled
`payload/` is empty. Use the **Latest (online)** mode against a real
network for a working install. To build the offline payload, run the
upstream CI workflow and drop the produced artifacts into `payload/`.

## Packaging

The original CI in
[`bandusix/easy-codex-and-claude-cli-setup/.github/workflows`](https://github.com/bandusix/easy-codex-and-claude-cli-setup/tree/main/.github/workflows)
already runs PyInstaller against `gui_installer.py`. Once this file is
dropped in as a replacement, the existing release pipeline produces
`AI_Tools_Installer_Windows.exe` and `AI_Tools_Installer_macOS.dmg` with
the new features.

This fork ships its own `.github/workflows/build.yml` that does the same
plus an automatic-version-check step. See [CI.md](CI.md) for the full
pipeline description. In short: a daily cron probes npm and GitHub
Releases; if any of the 5 CLIs has a newer version, the workflow
re-downloads its payload, rebuilds the three platform installers, and
publishes them as a GitHub release.

## License

MIT — same as upstream. Copyright © 2026 bandusix.
