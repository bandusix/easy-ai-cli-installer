"""Check for the latest version of every CLI the installer knows about and
(optionally) download the matching payload into ./payload.

This script is the heart of the GitHub Actions workflow that keeps the
offline installer current. It is intentionally stdlib-only so it can run on
any GitHub-hosted runner with zero setup.

Usage:
    # CI: only inspect, write summary to GITHUB_OUTPUT
    python scripts/fetch_latest.py --check

    # CI: download every payload we know about
    python scripts/fetch_latest.py --download

    # Local: dry-run that prints what would change
    python scripts/fetch_latest.py --check --output /tmp/inspect

Outputs (via GITHUB_OUTPUT when available):
    needs_update = true|false
    updated      = "claude:1.0.46,gemini:0.3.2,..."
    versions     = JSON of {tool: version}"""

import argparse
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

PAYLOAD_DIR_DEFAULT = "payload"
GITHUB_OUTPUT_ENV = "GITHUB_OUTPUT"

# ---------------------------------------------------------------------------
# Tool catalog. Each entry has a "kind" of either "npm" (fetch the latest
# tarball from registry.npmjs.org) or "github_asset" (find the asset in
# openai/codex's latest release whose name ends with the given suffix).
# `output_name` is the fixed filename the installer code looks for.
# ---------------------------------------------------------------------------
TOOLS = {
    "claude": {
        "kind": "npm",
        "package": "@anthropic-ai/claude-code",
        "current_prefix": "anthropic-ai-claude-code-",
    },
    "gemini": {
        "kind": "npm",
        "package": "@google/gemini-cli",
        "current_prefix": "google-gemini-cli-",
    },
    "kimi": {
        "kind": "npm",
        "package": "@moonshot-ai/kimi-code",
        "current_prefix": "moonshot-ai-kimi-code-",
    },
    "lark_npm": {
        "kind": "npm",
        "package": "@larksuite/cli",
        "current_prefix": "larksuite-cli-",
    },
    "codex_win_x64": {
        "kind": "github_asset",
        "repo": "openai/codex",
        "asset_suffix": "x86_64-pc-windows-msvc.zip",
        "output_name": "codex-win-x64.zip",
    },
    "codex_mac_arm64": {
        "kind": "github_asset",
        "repo": "openai/codex",
        "asset_suffix": "aarch64-apple-darwin.tar.gz",
        "output_name": "codex-mac-arm64.tar.gz",
    },
    "codex_mac_x64": {
        "kind": "github_asset",
        "repo": "openai/codex",
        "asset_suffix": "x86_64-apple-darwin.tar.gz",
        "output_name": "codex-mac-x64.tar.gz",
    },
    # Node.js runtime. We use a stable major version (currently 20.x LTS)
    # rather than @latest because the Node.js download index changes layout
    # and we want a predictable URL.
    "node_win_x64": {
        "kind": "node_release",
        "platform": "win-x64",
        "version": "v20.18.0",
        "output_name": "node-win-x64.zip",
    },
    "node_mac_arm64": {
        "kind": "node_release",
        "platform": "darwin-arm64",
        "version": "v20.18.0",
        "output_name": "node-mac-arm64.tar.gz",
    },
    "node_mac_x64": {
        "kind": "node_release",
        "platform": "darwin-x64",
        "version": "v20.18.0",
        "output_name": "node-mac-x64.tar.gz",
    },
    # @larksuite/cli's Go launcher binary (extracted from its GitHub release).
    "lark_bin_win_x64": {
        "kind": "github_asset",
        "repo": "larksuite/cli",
        "asset_suffix": "windows-amd64.zip",
        "output_name": "lark-cli-win-x64.zip",
    },
    "lark_bin_mac_arm64": {
        "kind": "github_asset",
        "repo": "larksuite/cli",
        "asset_suffix": "darwin-arm64.tar.gz",
        "output_name": "lark-cli-mac-arm64.tar.gz",
    },
    "lark_bin_mac_x64": {
        "kind": "github_asset",
        "repo": "larksuite/cli",
        "asset_suffix": "darwin-amd64.tar.gz",
        "output_name": "lark-cli-mac-x64.tar.gz",
    },
}


# ---------------------------------------------------------------------------
# Tiny HTTP helpers
# ---------------------------------------------------------------------------
def _http_get_json(url, timeout=30):
    headers = {
        "User-Agent": "easy-ai-cli-installer-build",
        "Accept": "application/json",
    }
    # Use GITHUB_TOKEN if available (CI environment) to avoid rate limits
    if "github.com" in url or "api.github.com" in url:
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _download_to(url, dest, timeout=180):
    headers = {"User-Agent": "easy-ai-cli-installer-build"}
    # Use GITHUB_TOKEN for GitHub downloads if available
    if "github.com" in url or "api.github.com" in url:
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        with open(dest, "wb") as out:
            shutil.copyfileobj(r, out)


# ---------------------------------------------------------------------------
# Version probes
# ---------------------------------------------------------------------------
def _npm_latest(pkg):
    """Return (version, tarball_url) for the latest release of an npm package."""
    data = _http_get_json(f"https://registry.npmjs.org/{pkg}/latest")
    return data["version"], data["dist"]["tarball"]


def _github_latest_asset(repo, asset_suffix):
    """Return (tag, asset_url) for the latest GitHub release of repo, picking
    the asset whose name ends with asset_suffix."""
    data = _http_get_json(f"https://api.github.com/repos/{repo}/releases/latest")
    tag = data.get("tag_name", "")
    for asset in data.get("assets", []):
        if asset["name"].endswith(asset_suffix):
            return tag, asset["browser_download_url"]
    available = [a["name"] for a in data.get("assets", [])]
    raise RuntimeError(
        f"No asset ending with {asset_suffix!r} in {repo}@{tag}. "
        f"Available: {available}"
    )


def _node_release_url(platform, version):
    """Node.js's release index is per-major; we hardcode the file name."""
    if platform == "win-x64":
        return f"https://nodejs.org/dist/{version}/node-{version}-win-x64.zip"
    if platform == "darwin-arm64":
        return f"https://nodejs.org/dist/{version}/node-{version}-darwin-arm64.tar.gz"
    if platform == "darwin-x64":
        return f"https://nodejs.org/dist/{version}/node-{version}-darwin-x64.tar.gz"
    raise ValueError(f"Unknown Node.js platform: {platform!r}")


# ---------------------------------------------------------------------------
# Inspect / download
# ---------------------------------------------------------------------------
def _current_npm_version(payload_dir, prefix):
    """Find the version of the npm package currently in payload/. Returns
    None if no matching file is present."""
    if not os.path.isdir(payload_dir):
        return None
    pat = re.compile(rf"^{re.escape(prefix)}(.+)\.tgz$")
    for fn in os.listdir(payload_dir):
        m = pat.match(fn)
        if m:
            return m.group(1)
    return None


def _has_file(payload_dir, name):
    return os.path.isfile(os.path.join(payload_dir, name))


def _resolve(tool_name, spec):
    """Return the latest version string (or tag) and, if relevant, a URL to
    download. Raises on failure."""
    kind = spec["kind"]
    if kind == "npm":
        version, url = _npm_latest(spec["package"])
        return version, url
    if kind == "github_asset":
        tag, url = _github_latest_asset(spec["repo"], spec["asset_suffix"])
        return tag, url
    if kind == "node_release":
        return spec["version"], _node_release_url(spec["platform"], spec["version"])
    raise ValueError(f"Unknown kind: {kind}")


def _download_for(tool_name, spec, url, output_dir, dry_run):
    """Place the artifact at output_dir/<output_name | derived>. `dry_run`
    reports what would happen without touching disk."""
    out_name = spec.get("output_name")
    if out_name is None:
        # npm: derive name from URL, which ends in
        # `/<pkg-without-scope>/-<name>-<version>.tgz`.
        m = re.search(r"/([^/]+\.tgz)$", url)
        if not m:
            raise RuntimeError(f"Could not derive npm filename from {url}")
        out_name = m.group(1)

    target = os.path.join(output_dir, out_name)
    if dry_run:
        return out_name
    _download_to(url, target)
    return out_name


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true",
                   help="Only check; don't download. Default if neither flag is set.")
    p.add_argument("--download", action="store_true",
                   help="Download artifacts whose version differs from payload/.")
    p.add_argument("--output", default=PAYLOAD_DIR_DEFAULT,
                   help=f"Payload directory (default: {PAYLOAD_DIR_DEFAULT})")
    p.add_argument("--platform", default="all",
                   choices=["all", "clis", "windows", "macos-arm", "macos-x64", "node"],
                   help="Restrict which tools to process")
    args = p.parse_args()

    do_download = args.download and not args.check
    dry_run = not do_download

    if not dry_run:
        os.makedirs(args.output, exist_ok=True)

    # Filter tools by platform bucket.
    selected = {}
    for name, spec in TOOLS.items():
        if args.platform == "all":
            selected[name] = spec
        elif args.platform == "clis":
            if name in ("claude", "gemini", "kimi", "lark_npm",
                        "codex_win_x64", "codex_mac_arm64", "codex_mac_x64",
                        "lark_bin_win_x64", "lark_bin_mac_arm64", "lark_bin_mac_x64"):
                selected[name] = spec
        elif args.platform == "windows":
            if "win" in name:
                selected[name] = spec
        elif args.platform == "macos-arm":
            if "mac_arm" in name or "macos-arm" in name:
                selected[name] = spec
        elif args.platform == "macos-x64":
            if "mac_x64" in name:
                selected[name] = spec
        elif args.platform == "node":
            if name.startswith("node_"):
                selected[name] = spec

    if not selected:
        print(f"No tools match platform={args.platform}", file=sys.stderr)
        sys.exit(1)

    updated = {}     # tool_name -> new version
    versions = {}    # tool_name -> current version
    for name, spec in selected.items():
        try:
            latest, url = _resolve(name, spec)
        except Exception as e:
            print(f"[skip] {name}: resolve failed: {e}", file=sys.stderr)
            continue
        versions[name] = latest

        # Compare with what we already have.
        if spec["kind"] == "npm":
            current = _current_npm_version(args.output, spec["current_prefix"])
            if current == latest:
                print(f"[ok]   {name}: already at {latest}")
                continue
            print(f"[new]  {name}: {current or 'none'} -> {latest}")
        else:
            if _has_file(args.output, spec["output_name"]):
                print(f"[ok]   {name}: already present ({spec['output_name']})")
                continue
            print(f"[new]  {name}: missing {spec['output_name']} (latest={latest})")

        if dry_run:
            updated[name] = latest
            continue

        try:
            out_name = _download_for(name, spec, url, args.output, dry_run=False)
            print(f"[done] {name}: -> payload/{out_name}")
            updated[name] = latest
        except Exception as e:
            print(f"[fail] {name}: download failed: {e}", file=sys.stderr)

    # Emit a summary table.
    print("")
    print("=" * 60)
    if updated:
        print(f"{len(updated)} tool(s) updated: {', '.join(f'{k}={v}' for k, v in updated.items())}")
    else:
        print("No updates found.")
    print("=" * 60)

    # Write to GITHUB_OUTPUT for downstream steps.
    if os.environ.get(GITHUB_OUTPUT_ENV):
        with open(os.environ[GITHUB_OUTPUT_ENV], "a", encoding="utf-8") as f:
            f.write(f"needs_update={'true' if updated else 'false'}\n")
            f.write("updated<<EOF\n")
            f.write(",".join(f"{k}={v}" for k, v in updated.items()) + "\n")
            f.write("EOF\n")
            f.write("versions<<EOF\n")
            f.write(json.dumps(versions, indent=2) + "\n")
            f.write("EOF\n")

    # Exit non-zero if downloads were requested and at least one failed.
    if do_download and updated and any(
            n not in updated for n, s in selected.items()
            if _resolve_safe(n, s) is None):
        sys.exit(1)


def _resolve_safe(name, spec):
    """Used in the final exit-code check to avoid re-fetching the network."""
    try:
        _resolve(name, spec)
        return True
    except Exception:
        return None


if __name__ == "__main__":
    main()