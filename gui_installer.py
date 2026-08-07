import os
import sys
import platform
import tarfile
import zipfile
import shutil
import subprocess
import threading
import locale
import json
import webbrowser
import tkinter as tk
from tkinter import messagebox
from tkinter import font as tkfont

# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------
IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"
ARCH = platform.machine().lower()
IS_ARM = "arm" in ARCH or "aarch64" in ARCH

# Prevents a console window from flashing/popping up when this --windowed
# GUI app spawns npm.cmd or other console subprocesses on Windows.
_NO_WINDOW_FLAGS = subprocess.CREATE_NO_WINDOW if IS_WIN else 0


def get_resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


def add_to_path_win(target_dir):
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_ALL_ACCESS)
        current_path, _ = winreg.QueryValueEx(key, "PATH")
        if target_dir not in current_path:
            new_path = current_path + ";" + target_dir
            winreg.SetValueEx(key, "PATH", 0, winreg.REG_EXPAND_SZ, new_path)
            subprocess.run(["setx", "PATH", new_path], shell=True, stdout=subprocess.DEVNULL,
                            creationflags=_NO_WINDOW_FLAGS)
    except Exception as e:
        print(f"Failed to update PATH: {e}")


def get_install_dirs():
    if IS_MAC:
        bin_dir = os.path.expanduser("~/.local/bin")
        app_dir = os.path.expanduser("~/.local/share/ai_tools_env")
    elif IS_WIN:
        bin_dir = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "ai_tools_bin")
        app_dir = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "ai_tools_env")
    else:
        raise Exception("Unsupported OS")

    os.makedirs(bin_dir, exist_ok=True)
    os.makedirs(app_dir, exist_ok=True)

    if IS_MAC:
        shell_rc = os.path.expanduser("~/.zshrc")
        if os.path.exists(shell_rc):
            with open(shell_rc, "r") as f:
                content = f.read()
            if "export PATH=\"$HOME/.local/bin:$PATH\"" not in content:
                with open(shell_rc, "a") as f:
                    f.write('\nexport PATH="$HOME/.local/bin:$PATH"\n')

    elif IS_WIN:
        add_to_path_win(bin_dir)

    return bin_dir, app_dir


def install_codex(source_mode="bundled"):
    """Codex is a standalone Rust binary. In `bundled` mode we extract the
    archive shipped in payload/; in `online` mode we hit the latest GitHub
    release of openai/codex and download the matching asset."""
    bin_dir, _ = get_install_dirs()

    if source_mode == "latest":
        if IS_WIN:
            suffix = "x86_64-pc-windows-msvc.zip"
        elif IS_MAC:
            suffix = "aarch64-apple-darwin.tar.gz" if IS_ARM else "x86_64-apple-darwin.tar.gz"
        else:
            raise Exception("Codex online install isn't supported on this OS yet.")
        url, version = _github_latest_release("openai/codex", suffix)
        import tempfile
        tmpfd, tmppath = tempfile.mkstemp(suffix=os.path.splitext(suffix)[1])
        os.close(tmpfd)
        try:
            _download_to(url, tmppath)
            if IS_WIN:
                with zipfile.ZipFile(tmppath, "r") as z:
                    z.extractall(bin_dir)
                for root, _, files in os.walk(bin_dir):
                    for f in files:
                        if "codex.exe" in f:
                            extracted = os.path.join(root, f)
                            final = os.path.join(bin_dir, "codex.exe")
                            if extracted != final and os.path.isfile(final):
                                os.remove(final)
                            if extracted != final:
                                shutil.move(extracted, final)
            else:
                with tarfile.open(tmppath, "r:gz") as tar:
                    tar.extractall(path=bin_dir)
                for member in tar.getmembers():
                    if member.isfile() and "codex" in member.name:
                        extracted = os.path.join(bin_dir, member.name)
                        final = os.path.join(bin_dir, "codex")
                        if extracted != final and os.path.exists(final):
                            os.remove(final)
                        if extracted != final:
                            shutil.move(extracted, final)
                        os.chmod(final, 0o755)
        finally:
            try: os.remove(tmppath)
            except OSError: pass
        return

    # Bundled fallback: payload/
    if IS_MAC:
        filename = "codex-mac-arm64.tar.gz" if IS_ARM else "codex-mac-x64.tar.gz"
        archive_path = get_resource_path(f"payload/{filename}")
        if not os.path.exists(archive_path):
            raise Exception(f"macOS Codex payload not found: {filename}")
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=bin_dir)
            for member in tar.getmembers():
                if member.isfile() and "codex" in member.name:
                    extracted_path = os.path.join(bin_dir, member.name)
                    final_path = os.path.join(bin_dir, "codex")
                    if extracted_path != final_path:
                        shutil.move(extracted_path, final_path)
                    os.chmod(final_path, 0o755)
    elif IS_WIN:
        archive_path = get_resource_path("payload/codex-win-x64.zip")
        if not os.path.exists(archive_path):
            raise Exception("Windows Codex payload not found.")
        with zipfile.ZipFile(archive_path, "r") as zip_ref:
            zip_ref.extractall(bin_dir)
            for root, dirs, files in os.walk(bin_dir):
                for file in files:
                    if "codex.exe" in file:
                        extracted_path = os.path.join(root, file)
                        final_path = os.path.join(bin_dir, "codex.exe")
                        if extracted_path != final_path:
                            shutil.move(extracted_path, final_path)


# ---------------------------------------------------------------------------
# Shared helpers for the Node.js-based tools (Claude Code, Gemini, Kimi, Lark)
# ---------------------------------------------------------------------------
def _ensure_node(app_dir):
    """Extract the bundled portable Node.js runtime once. Safe to call repeatedly."""
    node_dir = os.path.join(app_dir, "node")
    if os.path.exists(node_dir):
        return node_dir

    if IS_MAC:
        filename = "node-mac-arm64.tar.gz" if IS_ARM else "node-mac-x64.tar.gz"
        archive_path = get_resource_path(f"payload/{filename}")
        if not os.path.exists(archive_path):
            raise Exception(f"macOS Node payload not found: {filename}")
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=app_dir)
            extracted_folder = [m.name for m in tar.getmembers() if m.isdir()][0].split('/')[0]
        shutil.move(os.path.join(app_dir, extracted_folder), node_dir)
    elif IS_WIN:
        archive_path = get_resource_path("payload/node-win-x64.zip")
        if not os.path.exists(archive_path):
            raise Exception("Windows Node payload not found.")
        with zipfile.ZipFile(archive_path, "r") as zip_ref:
            zip_ref.extractall(path=app_dir)
            extracted_folder = zip_ref.namelist()[0].split('/')[0]
        shutil.move(os.path.join(app_dir, extracted_folder), node_dir)

    return node_dir


def _npm_bin(node_dir):
    if IS_MAC:
        return os.path.join(node_dir, "bin", "npm")
    return os.path.join(node_dir, "npm.cmd")


def _run_npm(args, env):
    """Run an npm command with output captured (never inherited) so no console
    window flashes on Windows, and so a failure's stderr tail is actually visible
    in the error dialog instead of a bare 'non-zero exit status'."""
    try:
        subprocess.run(args, check=True, env=env, capture_output=True, text=True,
                        creationflags=_NO_WINDOW_FLAGS)
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or e.stdout or "").strip().splitlines()[-15:]
        raise Exception("npm failed:\n" + "\n".join(tail)) from e


def _find_payload_tgz(prefix):
    payload_dir = get_resource_path("payload")
    for f in os.listdir(payload_dir):
        if f.startswith(prefix) and f.endswith(".tgz"):
            return os.path.join(payload_dir, f)
    return None


# ---------------------------------------------------------------------------
# Online install helpers (used when source mode = "online")
# ---------------------------------------------------------------------------
def _http_get_json(url, timeout=30):
    """Fetch a JSON document. Returns the parsed object or raises on error."""
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": "ai-tools-installer/1.0",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _download_to(url, dest_path, timeout=180, progress_cb=None):
    """Stream a URL to a local file. Optionally report progress via progress_cb(downloaded_bytes, total_bytes_or_None)."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "ai-tools-installer/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = r.headers.get("Content-Length")
        total = int(total) if total and total.isdigit() else None
        downloaded = 0
        chunk = 64 * 1024
        with open(dest_path, "wb") as out:
            while True:
                buf = r.read(chunk)
                if not buf:
                    break
                out.write(buf)
                downloaded += len(buf)
                if progress_cb:
                    progress_cb(downloaded, total)


def _github_latest_release(repo, asset_suffix):
    """Find the asset URL in the latest GitHub release whose name ends with asset_suffix."""
    api = f"https://api.github.com/repos/{repo}/releases/latest"
    data = _http_get_json(api)
    for asset in data.get("assets", []):
        if asset["name"].endswith(asset_suffix):
            return asset["browser_download_url"], data.get("tag_name", "")
    raise Exception(
        f"Latest GitHub release of {repo} has no asset ending with {asset_suffix!r}. "
        f"Available: {[a['name'] for a in data.get('assets', [])]}"
    )


def _extract_archive(archive_path, dest_dir, member_predicate=None):
    """Extract a .zip or .tar.gz archive. If member_predicate(name) is given,
    only matching top-level files are kept; the rest are discarded."""
    if archive_path.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as z:
            if member_predicate:
                for name in z.namelist():
                    if member_predicate(name):
                        z.extract(name, dest_dir)
            else:
                z.extractall(dest_dir)
    else:
        with tarfile.open(archive_path, "r:*") as t:
            if member_predicate:
                for m in t.getmembers():
                    if member_predicate(m.name):
                        t.extract(m, dest_dir)
            else:
                t.extractall(dest_dir)


def _expose_shim(bin_dir, node_dir, shim_name):
    """Expose a shim created inside node_dir by `npm install -g` via bin_dir (on PATH)."""
    if IS_MAC:
        node_shim = os.path.join(node_dir, "bin", shim_name)
        target_link = os.path.join(bin_dir, shim_name)
        if os.path.exists(target_link) or os.path.islink(target_link):
            os.remove(target_link)
        os.symlink(node_shim, target_link)
    elif IS_WIN:
        node_shim = os.path.join(node_dir, shim_name + ".cmd")
        target_bat = os.path.join(bin_dir, shim_name + ".cmd")
        with open(target_bat, "w") as f:
            f.write(f'@echo off\n"{node_shim}" %*')


def _expose_claude_shim(bin_dir, node_dir, base_url=None, auth_token=None):
    """Same as _expose_shim but, when a custom API endpoint is configured,
    wraps the claude shim so the two Anthropic env vars are set only for that
    command — and nothing else on the system sees them.

    The values are baked into the wrapper file. They are NOT written to the
    user's shell rc, ~/.zshrc, the system registry, or any global env. The user
    can inspect or delete the wrapper to fully remove the configuration."""
    if not base_url and not auth_token:
        # Nothing to override — fall back to the standard shim, so the user
        # continues to hit the official Anthropic endpoint by default.
        _expose_shim(bin_dir, node_dir, "claude")
        return

    if bool(base_url) != bool(auth_token):
        # Exactly one of the two was filled in. That's almost certainly a
        # misconfiguration, so refuse rather than silently half-applying it.
        raise Exception(
            "Claude API configuration error: ANTHROPIC_BASE_URL and "
            "ANTHROPIC_AUTH_TOKEN must both be filled in, or both left empty "
            "to use the official Anthropic endpoint."
        )

    if IS_MAC:
        node_shim = os.path.join(node_dir, "bin", "claude")
        target_sh = os.path.join(bin_dir, "claude")
        # Remove any existing file/symlink before creating the wrapper.
        if os.path.exists(target_sh) or os.path.islink(target_sh):
            os.remove(target_sh)
        lines = ["#!/bin/sh", "# Auto-generated by AI Tools Installer.",
                 "# Edit or delete this file to clear your custom Claude endpoint.",
                 f'export ANTHROPIC_BASE_URL="{base_url}"',
                 f'export ANTHROPIC_AUTH_TOKEN="{auth_token}"',
                 f'exec "{node_shim}" "$@"',
                 ""]
        with open(target_sh, "w") as f:
            f.write("\n".join(lines))
        os.chmod(target_sh, 0o755)
    elif IS_WIN:
        node_shim = os.path.join(node_dir, "claude.cmd")
        target_bat = os.path.join(bin_dir, "claude.cmd")
        # batch `set` is fine with double quotes around values, even when the
        # value itself contains `=` or `/`. No need to escape.
        lines = [
            "@echo off",
            "REM Auto-generated by AI Tools Installer.",
            "REM Edit or delete this file to clear your custom Claude endpoint.",
            f'set ANTHROPIC_BASE_URL={base_url}',
            f'set ANTHROPIC_AUTH_TOKEN={auth_token}',
            f'"{node_shim}" %*',
        ]
        with open(target_bat, "wb") as f:
            f.write(("\r\n".join(lines) + "\r\n").encode("utf-8"))


def install_claude_code(source_mode="bundled", base_url=None, auth_token=None):
    """Install Claude Code CLI. In `online` mode we hit the public npm
    registry for `@anthropic-ai/claude-code@latest`; in `bundled` mode we use
    the offline npm tarball shipped in payload/."""
    bin_dir, app_dir = get_install_dirs()
    node_dir = _ensure_node(app_dir)
    npm_bin = _npm_bin(node_dir)

    if source_mode == "latest":
        _run_npm([npm_bin, "install", "-g", "@anthropic-ai/claude-code@latest"],
                 os.environ.copy())
    else:
        tgz = _find_payload_tgz("anthropic-ai-claude-code")
        if not tgz:
            raise Exception("Claude Code npm package not found in payload.")
        _run_npm([npm_bin, "install", "-g", tgz], os.environ.copy())

    _expose_claude_shim(bin_dir, node_dir, base_url=base_url, auth_token=auth_token)


def install_gemini(source_mode="bundled"):
    bin_dir, app_dir = get_install_dirs()
    node_dir = _ensure_node(app_dir)
    npm_bin = _npm_bin(node_dir)

    if source_mode == "latest":
        _run_npm([npm_bin, "install", "-g", "@google/gemini-cli@latest"],
                 os.environ.copy())
    else:
        tgz = _find_payload_tgz("google-gemini-cli")
        if not tgz:
            raise Exception("Gemini CLI npm package not found in payload.")
        _run_npm([npm_bin, "install", "-g", tgz], os.environ.copy())

    _expose_shim(bin_dir, node_dir, "gemini")


def install_kimi(source_mode="bundled"):
    bin_dir, app_dir = get_install_dirs()
    node_dir = _ensure_node(app_dir)
    npm_bin = _npm_bin(node_dir)

    if source_mode == "latest":
        _run_npm([npm_bin, "install", "-g", "@moonshot-ai/kimi-code@latest"],
                 os.environ.copy())
    else:
        tgz = _find_payload_tgz("moonshot-ai-kimi-code")
        if not tgz:
            raise Exception("Kimi Code CLI npm package not found in payload.")
        _run_npm([npm_bin, "install", "-g", tgz], os.environ.copy())

    _expose_shim(bin_dir, node_dir, "kimi")


def install_feishu(source_mode="bundled"):
    """@larksuite/cli ships a Go binary that its postinstall script downloads
    from GitHub. In `online` mode we let postinstall run (so it always picks
    up the right binary for the user's platform). In `bundled` (offline) mode
    we skip postinstall and place the CI-fetched binary from payload/ into
    the path the launcher expects."""
    bin_dir, app_dir = get_install_dirs()
    node_dir = _ensure_node(app_dir)
    npm_bin = _npm_bin(node_dir)

    if source_mode == "latest":
        # postinstall downloads the right Go binary for the current OS.
        _run_npm([npm_bin, "install", "-g", "@larksuite/cli@latest"],
                 os.environ.copy())
    else:
        tgz = _find_payload_tgz("larksuite-cli")
        if not tgz:
            raise Exception("Feishu (lark-cli) npm package not found in payload.")
        _run_npm([npm_bin, "install", "-g", "--ignore-scripts", tgz], os.environ.copy())

        if IS_MAC:
            pkg_bin_dir = os.path.join(node_dir, "lib", "node_modules", "@larksuite", "cli", "bin")
            filename = "lark-cli-mac-arm64.tar.gz" if IS_ARM else "lark-cli-mac-x64.tar.gz"
            archive_path = get_resource_path(f"payload/{filename}")
            if not os.path.exists(archive_path):
                raise Exception(f"macOS lark-cli payload not found: {filename}")
            os.makedirs(pkg_bin_dir, exist_ok=True)
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(path=pkg_bin_dir)
            os.chmod(os.path.join(pkg_bin_dir, "lark-cli"), 0o755)
        elif IS_WIN:
            pkg_bin_dir = os.path.join(node_dir, "node_modules", "@larksuite", "cli", "bin")
            archive_path = get_resource_path("payload/lark-cli-win-x64.zip")
            if not os.path.exists(archive_path):
                raise Exception("Windows lark-cli payload not found.")
            os.makedirs(pkg_bin_dir, exist_ok=True)
            with zipfile.ZipFile(archive_path, "r") as zip_ref:
                zip_ref.extractall(path=pkg_bin_dir)

    _expose_shim(bin_dir, node_dir, "lark-cli")


# ---------------------------------------------------------------------------
# Tool registry — drives both the GUI rows and the install worker
# ---------------------------------------------------------------------------
TOOLS = [
    {"id": "codex", "icon": "C", "color": "#00FF00", "install": install_codex},
    {"id": "claude", "icon": "A", "color": "#00FF00", "install": install_claude_code},
    {"id": "gemini", "icon": "G", "color": "#00FF00", "install": install_gemini},
    {"id": "kimi", "icon": "K", "color": "#00FF00", "install": install_kimi},
    {"id": "feishu", "icon": "L", "color": "#00FF00", "install": install_feishu},
]


# ---------------------------------------------------------------------------
# Localization
# ---------------------------------------------------------------------------
LANGS = ["en", "zh-Hans", "zh-Hant"]
LANG_LABELS = {"en": "EN", "zh-Hans": "简", "zh-Hant": "繁"}

STRINGS = {
    "en": {
        "app_title": "AI Tools Installer",
        "app_subtitle": "Set up 5 AI coding CLIs — fully offline",
        "codex_title": "Codex CLI",
        "codex_desc": "OpenAI's coding agent for your terminal",
        "claude_title": "Claude Code CLI",
        "claude_desc": "Anthropic's coding agent for your terminal",
        "gemini_title": "Gemini CLI",
        "gemini_desc": "Google's coding agent for your terminal",
        "kimi_title": "Kimi Code CLI",
        "kimi_desc": "Moonshot AI's coding agent for your terminal",
        "feishu_title": "Lark CLI",
        "feishu_desc": "Official CLI for Feishu/Lark AI agents",
        "api_card_title": "Claude API Configuration",
        "api_card_pill": "OPTIONAL",
        "api_card_hint": "Leave both fields blank to use the official Anthropic endpoint. Fill both to route through a custom relay or gateway.",
        "api_base_url_label": "Base URL",
        "api_base_url_placeholder": "https://api.anthropic.com",
        "api_auth_token_label": "Auth Token",
        "api_auth_token_placeholder": "sk-ant-…",
        "source_label": "Source",
        "source_latest": "Latest (online)",
        "source_bundled": "Bundled (offline)",
        "install_button": "Install Now",
        "installing_button": "Installing…",
        "status_idle": "",
        "status_installing_codex": "Installing Codex CLI…",
        "status_installing_claude": "Installing Claude Code (Node.js)…",
        "status_installing_gemini": "Installing Gemini CLI (Node.js)…",
        "status_installing_kimi": "Installing Kimi Code CLI (Node.js)…",
        "status_installing_feishu": "Installing Lark CLI (Node.js)…",
        "status_done": "Installation complete",
        "status_failed": "Installation failed",
        "success_title": "Success",
        "success_body": "Installation successful!\n\nInstalled to:\n{path}\n\nRestart your terminal to use the tools you installed.",
        "error_title": "Error",
        "error_body": "Something went wrong:\n{error}",
        "footer_hint": "Uncheck a tool to skip installing it.",
        "api_misconfig_title": "Claude API configuration incomplete",
        "api_misconfig_body": "Please fill in both Base URL and Auth Token, or leave both empty to use the official Anthropic endpoint.",
    },
    "zh-Hans": {
        "app_title": "AI 工具安装向导",
        "app_subtitle": "离线安装 5 款 AI 编程 CLI",
        "codex_title": "Codex CLI",
        "codex_desc": "OpenAI 出品的终端编程助手",
        "claude_title": "Claude Code CLI",
        "claude_desc": "Anthropic 出品的终端编程助手",
        "gemini_title": "Gemini CLI",
        "gemini_desc": "Google 出品的终端编程助手",
        "kimi_title": "Kimi Code CLI",
        "kimi_desc": "Moonshot AI 出品的终端编程助手",
        "feishu_title": "飞书 CLI",
        "feishu_desc": "飞书官方 CLI，让 AI Agent 直接操作你的飞书",
        "api_card_title": "Claude API 配置",
        "api_card_pill": "可选",
        "api_card_hint": "两项都留空使用 Anthropic 官方接口；同时填写两项可走自定义中转网关。",
        "api_base_url_label": "Base URL",
        "api_base_url_placeholder": "https://api.anthropic.com",
        "api_auth_token_label": "Auth Token",
        "api_auth_token_placeholder": "sk-ant-…",
        "source_label": "安装源",
        "source_latest": "最新 (在线)",
        "source_bundled": "打包版 (离线)",
        "install_button": "立即安装",
        "installing_button": "安装中…",
        "status_idle": "",
        "status_installing_codex": "正在安装 Codex CLI…",
        "status_installing_claude": "正在安装 Claude Code (Node.js)…",
        "status_installing_gemini": "正在安装 Gemini CLI (Node.js)…",
        "status_installing_kimi": "正在安装 Kimi Code CLI (Node.js)…",
        "status_installing_feishu": "正在安装飞书 CLI (Node.js)…",
        "status_done": "安装完成",
        "status_failed": "安装失败",
        "success_title": "安装成功",
        "success_body": "安装成功！\n\n已安装至：\n{path}\n\n请重启终端后使用已安装的工具。",
        "error_title": "出错了",
        "error_body": "安装过程中出现错误：\n{error}",
        "footer_hint": "取消勾选可跳过对应工具的安装。",
        "api_misconfig_title": "Claude API 配置不完整",
        "api_misconfig_body": "请同时填写 Base URL 和 Auth Token，或两项都留空以使用 Anthropic 官方接口。",
    },
    "zh-Hant": {
        "app_title": "AI 工具安裝精靈",
        "app_subtitle": "離線安裝 5 款 AI 程式設計 CLI",
        "codex_title": "Codex CLI",
        "codex_desc": "OpenAI 推出的終端機程式設計助手",
        "claude_title": "Claude Code CLI",
        "claude_desc": "Anthropic 推出的終端機程式設計助手",
        "gemini_title": "Gemini CLI",
        "gemini_desc": "Google 推出的終端機程式設計助手",
        "kimi_title": "Kimi Code CLI",
        "kimi_desc": "Moonshot AI 推出的終端機程式設計助手",
        "feishu_title": "飛書 CLI",
        "feishu_desc": "飛書官方 CLI，讓 AI Agent 直接操作你的飛書",
        "api_card_title": "Claude API 設定",
        "api_card_pill": "選填",
        "api_card_hint": "兩項皆留空使用 Anthropic 官方介接；同時填寫兩項可走自訂中轉。",
        "api_base_url_label": "Base URL",
        "api_base_url_placeholder": "https://api.anthropic.com",
        "api_auth_token_label": "Auth Token",
        "api_auth_token_placeholder": "sk-ant-…",
        "source_label": "安裝來源",
        "source_latest": "最新 (線上)",
        "source_bundled": "打包版 (離線)",
        "install_button": "立即安裝",
        "installing_button": "安裝中…",
        "status_idle": "",
        "status_installing_codex": "正在安裝 Codex CLI…",
        "status_installing_claude": "正在安裝 Claude Code (Node.js)…",
        "status_installing_gemini": "正在安裝 Gemini CLI (Node.js)…",
        "status_installing_kimi": "正在安裝 Kimi Code CLI (Node.js)…",
        "status_installing_feishu": "正在安裝飛書 CLI (Node.js)…",
        "status_done": "安裝完成",
        "status_failed": "安裝失敗",
        "success_title": "安裝成功",
        "success_body": "安裝成功！\n\n已安裝至：\n{path}\n\n請重新啟動終端機後使用已安裝的工具。",
        "error_title": "發生錯誤",
        "error_body": "安裝過程中發生錯誤：\n{error}",
        "footer_hint": "取消勾選可略過該工具的安裝。",
        "api_misconfig_title": "Claude API 設定不完整",
        "api_misconfig_body": "請同時填寫 Base URL 與 Auth Token，或兩項皆留空以使用 Anthropic 官方介接。",
    },
}


def detect_language():
    try:
        loc = locale.getlocale()[0] or ""
    except Exception:
        loc = ""
    if not loc:
        for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
            loc = os.environ.get(var, "")
            if loc:
                break
    loc = loc.lower()
    if "zh" not in loc:
        return "en"
    if any(tag in loc for tag in ("tw", "hk", "mo", "hant")):
        return "zh-Hant"
    return "zh-Hans"


# ---------------------------------------------------------------------------
# Hacker-style theme (unified for Windows and macOS)
# Black/white/green terminal aesthetic matching the landing page
# ---------------------------------------------------------------------------
COLOR_BG = "#000000"           # Pure black background
COLOR_CARD = "#0A0A0A"         # Slightly lighter black for cards
COLOR_CARD_BORDER = "#1A1A1A"  # Dark gray border
COLOR_SHADOW = "#000000"       # No shadow (pure black)
COLOR_TEXT = "#FFFFFF"         # Pure white text
COLOR_SUBTEXT = "#666666"      # Gray subtext
COLOR_ACCENT = "#00FF00"       # Terminal green
COLOR_ACCENT_HOVER = "#00CC00" # Slightly darker green
COLOR_TOGGLE_OFF = "#333333"   # Dark gray for off state
COLOR_DIVIDER = "#1A1A1A"      # Dark divider
RADIUS_CARD = 0                # Sharp corners (no rounding)
RADIUS_BTN = 0                 # Sharp buttons
RADIUS_ICON = 0                # Sharp icons

# Monospace font with fallbacks
if IS_WIN:
    FONT_FAMILY = "Courier New"
    FONT_FALLBACKS = ["Consolas", "Lucida Console", "monospace"]
else:
    FONT_FAMILY = "Monaco"
    FONT_FALLBACKS = ["Menlo", "Courier", "monospace"]

FONT_FAMILY_ZH = "SimHei" if IS_WIN else "PingFang SC"

COLOR_SUCCESS = "#00FF00"      # Terminal green
COLOR_ERROR = "#FF0000"        # Terminal red
COLOR_FIELD_BG = "#0A0A0A"     # Dark input fields
COLOR_FIELD_BORDER = "#333333" # Gray borders



def family_for(lang):
    if lang == "zh-Hant":
        return "PingFang TC" if IS_MAC else FONT_FAMILY_ZH
    if lang == "zh-Hans":
        return FONT_FAMILY_ZH
    return FONT_FAMILY


def get_monospace_font(size=10, weight="normal"):
    """Try to create monospace font with fallbacks"""
    import tkinter.font as tkfont

    # Try primary font
    try:
        return tkfont.Font(family=FONT_FAMILY, size=size, weight=weight)
    except:
        pass

    # Try fallbacks
    for fallback in FONT_FALLBACKS:
        try:
            return tkfont.Font(family=fallback, size=size, weight=weight)
        except:
            continue

    # Ultimate fallback: default fixed font
    return tkfont.Font(family="TkFixedFont", size=size, weight=weight)


# ---------------------------------------------------------------------------
# Canvas drawing helpers
# ---------------------------------------------------------------------------
def rounded_rect(canvas, x1, y1, x2, y2, r, **kwargs):
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def darken(hex_color, factor=0.85):
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    r, g, b = int(r * factor), int(g * factor), int(b * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


class ToggleSwitch:
    def __init__(self, canvas, x, y, value=True, on_color=COLOR_ACCENT, off_color=COLOR_TOGGLE_OFF, command=None):
        self.canvas = canvas
        self.x, self.y = x, y
        self.w, self.h = 40, 22
        self.value = value
        self.on_color = on_color
        self.off_color = off_color
        self.command = command
        self.track_id = rounded_rect(canvas, x, y, x + self.w, y + self.h, self.h / 2,
                                      fill=self._track_color(), outline="")
        self.knob_id = canvas.create_oval(0, 0, 0, 0, fill="white", outline="")
        self._set_knob_pos(animate=False)
        for item in (self.track_id, self.knob_id):
            canvas.tag_bind(item, "<Button-1>", self._on_click)
            canvas.tag_bind(item, "<Enter>", lambda e: canvas.config(cursor="pointinghand" if IS_MAC else "hand2"))
            canvas.tag_bind(item, "<Leave>", lambda e: canvas.config(cursor=""))

    def _track_color(self):
        return self.on_color if self.value else self.off_color

    def _target_cx(self):
        pad = 2
        r = self.h / 2 - pad
        return (self.x + self.w - pad - r) if self.value else (self.x + pad + r)

    def _set_knob_pos(self, animate=True):
        pad = 2
        r = self.h / 2 - pad
        cy = self.y + self.h / 2
        target_cx = self._target_cx()
        if not animate:
            self.canvas.coords(self.knob_id, target_cx - r, cy - r, target_cx + r, cy + r)
            return
        coords = self.canvas.coords(self.knob_id)
        start_cx = (coords[0] + coords[2]) / 2 if coords else target_cx
        self._animate(target_cx, cy, r, start_cx, 0, 6)

    def _animate(self, target_cx, cy, r, start_cx, step, steps):
        if step > steps:
            self.canvas.coords(self.knob_id, target_cx - r, cy - r, target_cx + r, cy + r)
            return
        frac = step / steps
        cx = start_cx + (target_cx - start_cx) * frac
        self.canvas.coords(self.knob_id, cx - r, cy - r, cx + r, cy + r)
        self.canvas.after(9, lambda: self._animate(target_cx, cy, r, start_cx, step + 1, steps))

    def _on_click(self, event):
        self.set(not self.value)

    def set(self, value):
        self.value = value
        self.canvas.itemconfig(self.track_id, fill=self._track_color())
        self._set_knob_pos(animate=True)
        if self.command:
            self.command(self.value)

    def get(self):
        return self.value


class RoundedButton:
    def __init__(self, canvas, x, y, w, h, text, command, bg, fg="white", font=None, radius=RADIUS_BTN):
        self.canvas = canvas
        self.x, self.y, self.w, self.h = x, y, w, h
        self.command = command
        self.bg = bg
        self.hover_bg = darken(bg)
        self.disabled_bg = "#BFBFBF" if IS_WIN else "#C7C7CC"
        self.fg = fg
        self.enabled = True
        self.rect_id = rounded_rect(canvas, x, y, x + w, y + h, radius, fill=bg, outline="")
        self.text_id = canvas.create_text(x + w / 2, y + h / 2, text=text, fill=fg, font=font)
        for item in (self.rect_id, self.text_id):
            canvas.tag_bind(item, "<Enter>", self._on_enter)
            canvas.tag_bind(item, "<Leave>", self._on_leave)
            canvas.tag_bind(item, "<Button-1>", self._on_click)

    def _on_enter(self, event):
        if self.enabled:
            self.canvas.itemconfig(self.rect_id, fill=self.hover_bg)
            self.canvas.config(cursor="pointinghand" if IS_MAC else "hand2")

    def _on_leave(self, event):
        if self.enabled:
            self.canvas.itemconfig(self.rect_id, fill=self.bg)
        self.canvas.config(cursor="")

    def _on_click(self, event):
        if self.enabled and self.command:
            self.command()

    def set_text(self, text):
        self.canvas.itemconfig(self.text_id, text=text)

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.canvas.itemconfig(self.rect_id, fill=self.bg if enabled else self.disabled_bg)


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------
WIN_W = 460
ROW_H = 58
CARD_PAD = 20
# Tools card starts a bit lower now to make room for the Source segmented
# control just below the header. This single source of truth keeps the rest
# of the layout math in sync.
SOURCE_ROW_Y = 84
SOURCE_ROW_H = 36
CARD_Y1 = SOURCE_ROW_Y + SOURCE_ROW_H + 8

# New "Claude API Configuration" card layout (sits between the tools card
# and the install button). Height is fixed so the geometry calc is easy.
API_CARD_Y1 = 100  # placeholder; reset by _layout() after tools card is sized
API_CARD_PAD_X = 20
API_FIELD_H = 32
API_FIELD_GAP = 18
API_LABEL_GAP = 8
API_CARD_H_MIN = 222
TOOLS_GAP = 16

LICENSE_URL = "https://github.com/bandusix/easy-codex-and-claude-cli-setup/blob/main/LICENSE"


class InstallerApp:
    def __init__(self, root):
        self.root = root
        self.lang = detect_language()
        self.installing = False

        self.canvas = tk.Canvas(root, width=WIN_W, height=800, bg=COLOR_BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.lang_items = {}
        self.dynamic_texts = {}
        self.toggles = {}

        # StringVars hold the live text content of the API config fields. They
        # are written into the claude shim during install.
        self.base_url_var = tk.StringVar()
        self.auth_token_var = tk.StringVar()
        self.api_field_widgets = []  # tk.Entry widgets, kept so language switch can refresh placeholder/fg

        self._build_static_chrome()
        self._build_header()
        self._build_lang_switcher()
        self._layout()  # builds tools card, api card, button+status, footer

        self.apply_language(self.lang)
        self._update_api_card_visibility()

    def _build_source_toggle(self):
        """Render a segmented "Source: Latest (online) | Bundled (offline)"
        control. Latest is selected by default. Clicking a pill switches
        modes; the active pill is filled with the accent color, the other is
        outline-only."""
        c = self.canvas
        self.source_mode = "latest"  # default
        self.source_pills = {}

        # "Source" label on the left
        self.dynamic_texts["source_label"] = c.create_text(
            28, SOURCE_ROW_Y + SOURCE_ROW_H / 2, anchor="w", fill=COLOR_SUBTEXT,
            text="", font=self.font(10, "bold"))

        pill_h = 26
        pill_y = SOURCE_ROW_Y + (SOURCE_ROW_H - pill_h) / 2
        # Right-anchor both pills to the right edge of the window. Width is
        # re-measured in apply_language once we know the localized label.
        x_right = WIN_W - 28

        for mode in ("bundled", "latest"):
            slot = {
                "w": 110,                # placeholder; recomputed in apply_language
                "y1": pill_y, "y2": pill_y + pill_h,
                "bg_id": None, "text_id": None,
            }
            self.source_pills[mode] = slot
            x_right = x_right - slot["w"] - 6  # 6px gap
            slot["x1"] = x_right + 6
            slot["x2"] = slot["x1"] + slot["w"]

        # Draw texts and binds for both pills
        for mode, slot in self.source_pills.items():
            self._redraw_source_pill(mode)

        self._render_source_toggle_state()

    def _redraw_source_pill(self, mode):
        """Recreate one pill's background + text. Called when the label
        changes (language switch) or its position needs to shift."""
        c = self.canvas
        slot = self.source_pills[mode]
        if slot["bg_id"] is not None:
            c.delete(slot["bg_id"])
        if slot["text_id"] is not None:
            c.delete(slot["text_id"])

        cx = (slot["x1"] + slot["x2"]) / 2
        cy = (slot["y1"] + slot["y2"]) / 2
        active = (mode == self.source_mode)
        fill = COLOR_ACCENT if active else COLOR_CARD
        slot["bg_id"] = rounded_rect(
            c, slot["x1"], slot["y1"], slot["x2"], slot["y2"],
            (slot["y2"] - slot["y1"]) / 2, fill=fill, outline="")
        label_text = STRINGS[self.lang][f"source_{mode}"]
        text_color = "white" if active else COLOR_TEXT
        slot["text_id"] = c.create_text(
            cx, cy, anchor="center", fill=text_color, text=label_text,
            font=self.font(10, "bold"))
        # Make the pill itself the click target (covers the rounded rect).
        for item in (slot["bg_id"], slot["text_id"]):
            c.tag_bind(item, "<Button-1>", lambda e, m=mode: self._set_source_mode(m))
            c.tag_bind(item, "<Enter>", lambda e: c.config(cursor="pointinghand" if IS_MAC else "hand2"))
            c.tag_bind(item, "<Leave>", lambda e: c.config(cursor=""))

    def _set_source_mode(self, mode):
        if mode == self.source_mode:
            return
        self.source_mode = mode
        self._render_source_toggle_state()

    def _relayout_source_pills(self):
        """Right-anchor the two pills to the window's right edge, sized to
        fit the localized labels."""
        c = self.canvas
        f = self.font(10, "bold")
        # Measure each label so the pill is wide enough.
        for mode, slot in self.source_pills.items():
            label = STRINGS[self.lang][f"source_{mode}"]
            # create_text with anchor=mid lets us use bbox to measure.
            tmp = c.create_text(0, 0, text=label, font=f)
            bb = c.bbox(tmp)
            c.delete(tmp)
            text_w = bb[2] - bb[0]
            slot["w"] = text_w + 24  # horizontal padding inside the pill
        # Re-position right-to-left so the right-most pill anchors to x=W-28.
        x_right = WIN_W - 28
        for mode in ("bundled", "latest"):
            slot = self.source_pills[mode]
            x_right = x_right - slot["w"] - 6
            slot["x1"] = x_right + 6
            slot["x2"] = slot["x1"] + slot["w"]

    def _render_source_toggle_state(self):
        """Repaint the segmented control to reflect the current source_mode."""
        for mode in self.source_pills:
            self._redraw_source_pill(mode)

    # -- fonts -------------------------------------------------------------
    def font(self, size, weight="normal"):
        return tkfont.Font(family=family_for(self.lang), size=size, weight=weight)

    # -- static chrome (things that never change with language) -----------
    def _build_static_chrome(self):
        c = self.canvas
        rounded_rect(c, 28, 28, 28 + 44, 28 + 44, RADIUS_ICON, fill=COLOR_ACCENT, outline="")
        c.create_text(28 + 22, 28 + 23, text=">_", fill="white",
                       font=tkfont.Font(family="Menlo" if IS_MAC else "Consolas", size=15, weight="bold"))

    def _build_header(self):
        c = self.canvas
        self.dynamic_texts["app_title"] = c.create_text(
            86, 40, anchor="w", fill=COLOR_TEXT, text="")
        self.dynamic_texts["app_subtitle"] = c.create_text(
            86, 62, anchor="w", fill=COLOR_SUBTEXT, text="")

    def _build_lang_switcher(self):
        c = self.canvas
        x = WIN_W - 28
        self.lang_items = {}
        for code in reversed(LANGS):
            label = LANG_LABELS[code]
            item = c.create_text(x, 32, anchor="e", text=label)
            c.tag_bind(item, "<Button-1>", lambda e, code=code: self.apply_language(code))
            c.tag_bind(item, "<Enter>", lambda e: c.config(cursor="pointinghand" if IS_MAC else "hand2"))
            c.tag_bind(item, "<Leave>", lambda e: c.config(cursor=""))
            self.lang_items[code] = item
            bbox = c.bbox(item)
            x = bbox[0] - 12

    def _row_cy(self, index):
        return CARD_Y1 + CARD_PAD + ROW_H / 2 + index * ROW_H

    # -- layout ------------------------------------------------------------
    def _layout(self):
        """Compute the vertical geometry and draw all sub-cards. Called once
        at init; also re-runs when language changes the title heights.

        Geometry (top → bottom):
            header        (y: 28..72)
            source toggle (y: SOURCE_ROW_Y..SOURCE_ROW_Y+SOURCE_ROW_H)
            tools card    y1 = CARD_Y1, height = CARD_PAD*2 + ROW_H*5
            api card      y1 = tools card bottom + TOOLS_GAP, height = API_CARD_H_MIN
            button        y1 = api card bottom + 26, height = 46
            status        y1 = button bottom + 22
            footer        anchored near bottom of window."""
        c = self.canvas

        # Source segmented control (Latest / Bundled). Drawn before the tools
        # card so it lives in the "chrome" area between header and the card.
        self._build_source_toggle()

        # Tools card
        tools_y2 = CARD_Y1 + CARD_PAD * 2 + ROW_H * len(TOOLS)
        self.tools_y2 = tools_y2
        x1, x2 = 28, WIN_W - 28
        rounded_rect(c, x1, CARD_Y1 + 3, x2, tools_y2 + 3, RADIUS_CARD, fill=COLOR_SHADOW, outline="")
        rounded_rect(c, x1, CARD_Y1, x2, tools_y2, RADIUS_CARD,
                     fill=COLOR_CARD, outline=COLOR_CARD_BORDER, width=1)

        text_x = 44 + 28 + 14
        toggle_x = x2 - 16 - 40
        for i, tool in enumerate(TOOLS):
            cy = self._row_cy(i)
            if i > 0:
                c.create_line(44, cy - ROW_H / 2, x2 - 16, cy - ROW_H / 2, fill=COLOR_DIVIDER)
            rounded_rect(c, 44, cy - 14, 44 + 28, cy + 14, RADIUS_ICON - 2, fill=tool["color"], outline="")
            c.create_text(44 + 14, cy, text=tool["icon"], fill="white",
                          font=tkfont.Font(size=12, weight="bold"))
            self.dynamic_texts[f"{tool['id']}_title"] = c.create_text(
                text_x, cy - 9, anchor="w", fill=COLOR_TEXT, text="")
            self.dynamic_texts[f"{tool['id']}_desc"] = c.create_text(
                text_x, cy + 9, anchor="w", fill=COLOR_SUBTEXT, text="")
            # Claude toggle gets a callback that shows/hides the API config card.
            if tool["id"] == "claude":
                self.toggles[tool["id"]] = ToggleSwitch(
                    c, toggle_x, cy - 11, value=True,
                    command=lambda v: self._update_api_card_visibility())
            else:
                self.toggles[tool["id"]] = ToggleSwitch(c, toggle_x, cy - 11, value=True)

        # API config card (drawn but may be hidden depending on Claude toggle)
        api_y1 = tools_y2 + TOOLS_GAP
        api_y2 = api_y1 + API_CARD_H_MIN
        self.api_y1, self.api_y2 = api_y1, api_y2
        self.api_card_items = []
        # Card body
        self.api_card_items.append(rounded_rect(
            c, x1, api_y1 + 3, x2, api_y2 + 3, RADIUS_CARD, fill=COLOR_SHADOW, outline=""))
        self.api_card_items.append(rounded_rect(
            c, x1, api_y1, x2, api_y2, RADIUS_CARD,
            fill=COLOR_CARD, outline=COLOR_CARD_BORDER, width=1))
        # Subtle accent stripe on the left edge (Claude brand color #CC785C)
        # to make the card's purpose obvious at a glance.
        self.api_card_items.append(rounded_rect(
            c, x1, api_y1 + 10, x1 + 3, api_y2 - 10, 1.5,
            fill="#CC785C", outline=""))

        field_x1 = x1 + API_CARD_PAD_X
        field_x2 = x2 - API_CARD_PAD_X
        field_w = field_x2 - field_x1

        # Header row: title (left) and a small "OPTIONAL" pill (right)
        self.api_card_items.append(c.create_text(
            field_x1, api_y1 + 22, anchor="nw", fill=COLOR_TEXT, text="",
            font=tkfont.Font(family=family_for(self.lang), size=12, weight="bold")))
        # Optional pill — a tiny rounded rect with text inside it.
        pill_w, pill_h = 58, 18
        pill_x1 = field_x2 - pill_w
        pill_y1 = api_y1 + 24
        self.api_card_items.append(rounded_rect(
            c, pill_x1, pill_y1, pill_x1 + pill_w, pill_y1 + pill_h,
            pill_h / 2, fill="#F1E6DF", outline=""))
        self.api_card_items.append(c.create_text(
            pill_x1 + pill_w / 2, pill_y1 + pill_h / 2, anchor="center",
            fill="#A05A3D", text="",
            font=tkfont.Font(family=family_for(self.lang), size=8, weight="bold")))

        # Hint (allowed to wrap to 2 lines, full width minus pill on the right)
        self.api_card_items.append(c.create_text(
            field_x1, api_y1 + 50, anchor="nw", fill=COLOR_SUBTEXT, text="",
            font=tkfont.Font(family=family_for(self.lang), size=9),
            width=field_w))

        # Field 1 — Base URL
        burl_label_y = api_y1 + 96
        self.api_card_items.append(c.create_text(
            field_x1, burl_label_y, anchor="nw", fill=COLOR_SUBTEXT, text="",
            font=tkfont.Font(family=family_for(self.lang), size=8, weight="bold")))
        burl_entry = tk.Entry(
            c, textvariable=self.base_url_var,
            font=tkfont.Font(family=family_for(self.lang), size=10),
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=COLOR_FIELD_BORDER, highlightcolor=COLOR_ACCENT,
            bg=COLOR_FIELD_BG, fg=COLOR_TEXT, insertbackground=COLOR_TEXT)
        burl_window = c.create_window(
            field_x1, burl_label_y + API_LABEL_GAP + 12, window=burl_entry,
            anchor="nw", width=field_w, height=API_FIELD_H)
        self.api_card_items.append(burl_window)
        self.api_field_widgets.append(("base_url", burl_entry))

        # Field 2 — Auth Token
        token_label_y = burl_label_y + API_LABEL_GAP + 12 + API_FIELD_H + API_FIELD_GAP
        self.api_card_items.append(c.create_text(
            field_x1, token_label_y, anchor="nw", fill=COLOR_SUBTEXT, text="",
            font=tkfont.Font(family=family_for(self.lang), size=8, weight="bold")))
        token_entry = tk.Entry(
            c, textvariable=self.auth_token_var,
            font=tkfont.Font(family=family_for(self.lang), size=10),
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=COLOR_FIELD_BORDER, highlightcolor=COLOR_ACCENT,
            bg=COLOR_FIELD_BG, fg=COLOR_TEXT, insertbackground=COLOR_TEXT,
            show="•")
        token_window = c.create_window(
            field_x1, token_label_y + API_LABEL_GAP + 12, window=token_entry,
            anchor="nw", width=field_w, height=API_FIELD_H)
        self.api_card_items.append(token_window)
        self.api_field_widgets.append(("auth_token", token_entry))

        # Bottom geometry
        btn_y = api_y2 + 26
        self.btn_y = btn_y
        self.btn = RoundedButton(
            c, 28, btn_y, WIN_W - 56, 46, "", self.start_install,
            bg=COLOR_ACCENT, font=None,
        )
        self.status_y = btn_y + 46 + 22
        self.status_id = c.create_text(WIN_W / 2, self.status_y, fill=COLOR_SUBTEXT, text="")

        # Footer (anchored to bottom edge — recomputed in _resize_to_fit)
        self.footer_y = self.status_y + 30

        # Final geometry
        win_h = self.footer_y + 36 + 22
        self.root.geometry(f"{WIN_W}x{win_h}")
        self.canvas.config(width=WIN_W, height=win_h)
        self.canvas.pack_propagate(False)
        self._draw_footer(win_h)

    def _draw_footer(self, win_h):
        c = self.canvas
        if hasattr(self, "footer_id") and self.footer_id is not None:
            c.delete(self.footer_id)
        if hasattr(self, "credit_id") and self.credit_id is not None:
            c.delete(self.credit_id)
        self.footer_id = c.create_text(WIN_W / 2, win_h - 40, fill=COLOR_SUBTEXT, text="")
        credit_color = "#B0B0B3" if IS_MAC else "#A6A6A6"
        self.credit_id = c.create_text(
            WIN_W / 2, win_h - 18,
            text="Released under the MIT License · Copyright © 2026 bandusix",
            fill=credit_color, font=tkfont.Font(size=8),
        )
        c.tag_bind(self.credit_id, "<Button-1>", lambda e: webbrowser.open(LICENSE_URL))
        c.tag_bind(self.credit_id, "<Enter>", lambda e: c.config(cursor="pointinghand" if IS_MAC else "hand2"))
        c.tag_bind(self.credit_id, "<Leave>", lambda e: c.config(cursor=""))

    def _update_api_card_visibility(self):
        """Show the API config card only when the Claude toggle is on. The
        card itself is always laid out — we just toggle the items' `state`
        so they're hidden, and the install button stays in place."""
        claude_on = self.toggles["claude"].get()
        state = "normal" if claude_on else "hidden"
        for item in self.api_card_items:
            self.canvas.itemconfigure(item, state=state)
        for _, widget in self.api_field_widgets:
            # Hidden Entry windows still receive focus; setting state on the
            # containing window item is enough, but we also disable the widget
            # so Tab navigation skips it.
            try:
                widget.configure(state=("normal" if claude_on else "disabled"))
            except tk.TclError:
                pass

    # -- language application ----------------------------------------------
    def apply_language(self, lang):
        self.lang = lang
        self.root.title(STRINGS[lang]["app_title"])

        f_title = self.font(17, "bold")
        f_sub = self.font(11)
        f_row_title = self.font(13, "bold")
        f_row_desc = self.font(10)
        f_btn = self.font(13, "bold")
        f_status = self.font(10)
        f_footer = self.font(9)
        f_lang = self.font(11, "bold")
        f_lang_inactive = self.font(11)
        f_field = self.font(10)

        c = self.canvas
        S = STRINGS[lang]
        c.itemconfig(self.dynamic_texts["app_title"], text=S["app_title"], font=f_title)
        c.itemconfig(self.dynamic_texts["app_subtitle"], text=S["app_subtitle"], font=f_sub)
        for tool in TOOLS:
            tid = tool["id"]
            c.itemconfig(self.dynamic_texts[f"{tid}_title"], text=S[f"{tid}_title"], font=f_row_title)
            c.itemconfig(self.dynamic_texts[f"{tid}_desc"], text=S[f"{tid}_desc"], font=f_row_desc)
        if hasattr(self, "footer_id") and self.footer_id is not None:
            c.itemconfig(self.footer_id, text=S["footer_hint"], font=f_footer)

        # API card labels (text order: title, pill, hint, base_url label, token label)
        # English labels render uppercase for the tracked-out look; CJK labels
        # render as-is. The pill text is its own key.
        def _label(s):
            return s.upper() if self.lang == "en" else s
        api_texts = [
            S["api_card_title"],
            S["api_card_pill"],
            S["api_card_hint"],
            _label(S["api_base_url_label"]),
            _label(S["api_auth_token_label"]),
        ]
        # Iterate by item type so we skip polygon/shape/window items.
        text_item_idx = 0
        for item in self.api_card_items:
            if self.canvas.type(item) == "text":
                self.canvas.itemconfig(item, text=api_texts[text_item_idx])
                text_item_idx += 1
                if text_item_idx >= len(api_texts):
                    break

        # Source label + pills. Label first…
        if "source_label" in self.dynamic_texts:
            c.itemconfig(self.dynamic_texts["source_label"], text=S["source_label"],
                         font=self.font(10, "bold"))
        # …then re-measure and reposition the two pills so localized labels fit.
        self._relayout_source_pills()
        self._render_source_toggle_state()

        # Update entry widget fonts
        for key, widget in self.api_field_widgets:
            try:
                widget.configure(font=f_field)
            except tk.TclError:
                pass
        # Placeholder-like UX: we use greyed text via a small hint shown only
        # when the field is empty and unfocused. Tk's Entry doesn't support a
        # native placeholder, so we emulate it with a `bind` callback that
        # swaps the foreground color when the field is empty.
        self._refresh_field_placeholders()

        if not self.installing:
            self.btn.set_text(S["install_button"])
        self.btn.canvas.itemconfig(self.btn.text_id, font=f_btn)
        c.itemconfig(self.status_id, text=STRINGS[lang]["status_idle"], font=f_status)

        for code, item in self.lang_items.items():
            active = code == lang
            c.itemconfig(item, fill=COLOR_ACCENT if active else COLOR_SUBTEXT,
                         font=f_lang if active else f_lang_inactive)

    def _refresh_field_placeholders(self):
        S = STRINGS[self.lang]
        placeholders = {
            "base_url": S["api_base_url_placeholder"],
            "auth_token": S["api_auth_token_placeholder"],
        }
        for key, widget in self.api_field_widgets:
            if not widget.get():
                widget.configure(fg=COLOR_FIELD_BORDER, show="")
                widget.insert(0, placeholders[key])
                widget.configure(fg=COLOR_FIELD_BORDER)
                # Bind focus-in to clear placeholder, focus-out to restore
                def make_handlers(w, ph):
                    def on_focus_in(_e):
                        if w.get() == ph:
                            w.delete(0, tk.END)
                        w.configure(fg=COLOR_TEXT)
                        if key == "auth_token":
                            w.configure(show="•")
                    def on_focus_out(_e):
                        if not w.get():
                            w.insert(0, ph)
                            w.configure(fg=COLOR_FIELD_BORDER, show="")
                    return on_focus_in, on_focus_out
                fi, fo = make_handlers(widget, placeholders[key])
                widget.bind("<FocusIn>", fi, add="+")
                widget.bind("<FocusOut>", fo, add="+")

    # -- status helper -------------------------------------------------------
    def set_status(self, text, color):
        self.canvas.itemconfig(self.status_id, text=text, fill=color)

    # -- install lifecycle ---------------------------------------------------
    def _collect_api_config(self):
        """Returns (base_url, auth_token) — both empty strings means "use
        Anthropic default". If only one is filled, raises with a localized
        message."""
        S = STRINGS[self.lang]
        placeholder_url = S["api_base_url_placeholder"]
        placeholder_token = S["api_auth_token_placeholder"]
        base_url = self.base_url_var.get().strip()
        auth_token = self.auth_token_var.get().strip()
        # Treat placeholder text as empty.
        if base_url == placeholder_url:
            base_url = ""
        if auth_token == placeholder_token:
            auth_token = ""
        if bool(base_url) != bool(auth_token):
            messagebox.showerror(S["api_misconfig_title"], S["api_misconfig_body"])
            raise Exception("Claude API configuration incomplete.")
        return base_url, auth_token

    def start_install(self):
        if self.installing:
            return
        S = STRINGS[self.lang]
        selected = [tool for tool in TOOLS if self.toggles[tool["id"]].get()]
        if not selected:
            return

        # Validate the API fields up-front so we don't half-install.
        try:
            base_url, auth_token = self._collect_api_config()
        except Exception:
            return

        self.installing = True
        self.btn.set_enabled(False)
        self.btn.set_text(S["installing_button"])
        self.set_status(S[f"status_installing_{selected[0]['id']}"], COLOR_SUBTEXT)

        thread = threading.Thread(
            target=self._install_worker,
            args=(selected, self.source_mode, base_url, auth_token),
            daemon=True,
        )
        thread.start()

    def _install_worker(self, selected, source_mode, base_url, auth_token):
        S = STRINGS[self.lang]
        total = len(selected)
        try:
            for idx, tool in enumerate(selected, start=1):
                # Show progress: "1/5 Installing Codex CLI..."
                progress_prefix = f"[{idx}/{total}] "
                status_text = progress_prefix + S[f"status_installing_{tool['id']}"]
                self.root.after(0, lambda t=status_text: self.set_status(t, COLOR_ACCENT))

                if tool["id"] == "claude":
                    tool["install"](source_mode=source_mode,
                                    base_url=base_url, auth_token=auth_token)
                else:
                    tool["install"](source_mode=source_mode)
            self.root.after(0, self._on_install_success)
        except TypeError as e:
            err = ("Installer signature mismatch: " + str(e) +
                   "\nIf you customized install_*, make sure it accepts "
                   "the new kwargs (source_mode=…).")
            self.root.after(0, lambda: self._on_install_error(err))
        except Exception as e:
            err = str(e)
            self.root.after(0, lambda: self._on_install_error(err))

    def _on_install_success(self):
        S = STRINGS[self.lang]
        self.installing = False
        self.btn.set_enabled(True)
        self.btn.set_text(S["install_button"])
        self.set_status(S["status_done"], COLOR_SUCCESS)
        bin_dir, _ = get_install_dirs()
        messagebox.showinfo(S["success_title"], S["success_body"].format(path=bin_dir))

    def _on_install_error(self, err):
        S = STRINGS[self.lang]
        self.installing = False
        self.btn.set_enabled(True)
        self.btn.set_text(S["install_button"])
        self.set_status(S["status_failed"], COLOR_ERROR)
        messagebox.showerror(S["error_title"], S["error_body"].format(error=err))


if __name__ == "__main__":
    root = tk.Tk()
    app = InstallerApp(root)
    root.mainloop()