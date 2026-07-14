from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__ as CURRENT_VERSION

# Canonical public repo (hardcoded). Env GROK_AM_GITHUB_REPO can still override for forks.
DEFAULT_GITHUB_REPO = "ChisaAlter/grokbuild-tools"


@dataclass
class UpdateInfo:
    current: str
    latest: str
    has_update: bool
    release_url: str | None = None
    zipball_url: str | None = None
    tag: str | None = None
    body: str | None = None
    source: str = ""  # releases | git | error
    message: str = ""


_SEMVER_RE = re.compile(
    r"v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?"
)


def parse_version(text: str) -> tuple[int, int, int, str]:
    """Return (major, minor, patch, prerelease) for comparison."""
    m = _SEMVER_RE.search(text.strip())
    if not m:
        return (0, 0, 0, text)
    pre = m.group("pre") or ""
    return (int(m.group("major")), int(m.group("minor")), int(m.group("patch")), pre)


def version_gt(a: str, b: str) -> bool:
    """True if a > b (semver-ish). Empty prerelease is greater than with prerelease."""
    am, an, ap, apre = parse_version(a)
    bm, bn, bp, bpre = parse_version(b)
    if (am, an, ap) != (bm, bn, bp):
        return (am, an, ap) > (bm, bn, bp)
    # 1.0.0 > 1.0.0-beta
    if apre == bpre:
        return False
    if apre == "":
        return True
    if bpre == "":
        return False
    return apre > bpre


def project_root() -> Path:
    """Repo root (parent of src/grok_account_manager)."""
    return Path(__file__).resolve().parents[2]


def detect_github_repo(explicit: str | None = None) -> str | None:
    """
    Resolve owner/repo from:
    1) explicit argument / settings
    2) env GROK_AM_GITHUB_REPO
    3) git remote origin (if this install is a git clone)
    """
    for cand in (
        (explicit or "").strip(),
        (os.environ.get("GROK_AM_GITHUB_REPO") or "").strip(),
        DEFAULT_GITHUB_REPO.strip(),
    ):
        if cand:
            return cand.replace("https://github.com/", "").replace(".git", "").strip("/")

    root = project_root()
    try:
        out = subprocess.check_output(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
    except Exception:
        return None
    # git@github.com:owner/repo.git or https://github.com/owner/repo.git
    m = re.search(r"github\.com[:/](?P<repo>[\w.-]+/[\w.-]+)", out)
    if m:
        return m.group("repo").removesuffix(".git")
    return None


def _http_json(url: str, timeout: float = 20.0) -> Any:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"grok-account-manager/{CURRENT_VERSION}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_for_update(
    *,
    github_repo: str | None = None,
    current: str | None = None,
) -> UpdateInfo:
    """Query GitHub Releases latest tag and compare to current version."""
    cur = current or CURRENT_VERSION
    repo = detect_github_repo(github_repo)
    if not repo:
        return UpdateInfo(
            current=cur,
            latest=cur,
            has_update=False,
            source="error",
            message=(
                "未配置 GitHub 仓库。请设置环境变量 GROK_AM_GITHUB_REPO=owner/repo，"
                "或在设置里填写，或确保本目录 git remote origin 指向 GitHub。"
            ),
        )
    try:
        data = _http_json(f"https://api.github.com/repos/{repo}/releases/latest")
    except urllib.error.HTTPError as e:
        # No releases yet — fall back to tags
        if e.code == 404:
            try:
                tags = _http_json(f"https://api.github.com/repos/{repo}/tags?per_page=5")
                if not tags:
                    return UpdateInfo(
                        current=cur,
                        latest=cur,
                        has_update=False,
                        source="error",
                        message=f"仓库 {repo} 尚无 release/tag。",
                        release_url=f"https://github.com/{repo}",
                    )
                latest = str(tags[0].get("name") or "")
                return UpdateInfo(
                    current=cur,
                    latest=latest.lstrip("v"),
                    has_update=version_gt(latest, cur),
                    release_url=f"https://github.com/{repo}/releases",
                    zipball_url=tags[0].get("zipball_url"),
                    tag=latest,
                    source="tags",
                    message="",
                )
            except Exception as e2:
                return UpdateInfo(
                    current=cur,
                    latest=cur,
                    has_update=False,
                    source="error",
                    message=f"检查更新失败: {e2}",
                )
        return UpdateInfo(
            current=cur,
            latest=cur,
            has_update=False,
            source="error",
            message=f"GitHub API 错误 HTTP {e.code}: {e.read()[:120]!r}",
            release_url=f"https://github.com/{repo}/releases",
        )
    except Exception as e:
        return UpdateInfo(
            current=cur,
            latest=cur,
            has_update=False,
            source="error",
            message=f"检查更新失败: {e}",
        )

    tag = str(data.get("tag_name") or data.get("name") or "")
    latest = tag.lstrip("v")
    return UpdateInfo(
        current=cur,
        latest=latest or cur,
        has_update=bool(latest) and version_gt(latest, cur),
        release_url=data.get("html_url") or f"https://github.com/{repo}/releases",
        zipball_url=data.get("zipball_url"),
        tag=tag,
        body=(data.get("body") or "")[:1500],
        source="releases",
        message="",
    )


def _run_git(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out
    except Exception as e:
        return 1, str(e)


def apply_update(
    info: UpdateInfo,
    *,
    github_repo: str | None = None,
) -> tuple[bool, str]:
    """
    Apply update:
    1) If install is a git clone with origin → fetch + checkout tag / pull
    2) Else download zipball and overlay project files (keeps .venv, local data elsewhere)
    """
    root = project_root()
    repo = detect_github_repo(github_repo)

    # Prefer git if available
    if (root / ".git").is_dir():
        code, out = _run_git(["rev-parse", "--is-inside-work-tree"], root)
        if code == 0:
            _run_git(["fetch", "--tags", "--force", "origin"], root)
            tag = info.tag or info.latest
            if tag:
                # try checkout tag
                c1, o1 = _run_git(["checkout", tag], root)
                if c1 == 0:
                    # reinstall package editable
                    _reinstall(root)
                    return True, f"已通过 git 更新到 {tag}。\n请重启应用生效。\n{o1[-300:]}"
                c2, o2 = _run_git(["pull", "--ff-only", "origin", "HEAD"], root)
                if c2 == 0:
                    _reinstall(root)
                    return True, f"已 git pull 更新。\n请重启应用。\n{o2[-300:]}"
                return False, f"git 更新失败:\n{o1}\n{o2}"

    # Zip overlay
    url = info.zipball_url
    if not url and repo and info.tag:
        url = f"https://api.github.com/repos/{repo}/zipball/{info.tag}"
    if not url:
        return False, "没有可下载的更新包地址（zipball）。请配置 GitHub 仓库并发布 Release。"

    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": f"grok-account-manager/{CURRENT_VERSION}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
            data = resp.read()
    except Exception as e:
        return False, f"下载更新失败: {e}"

    tmp = Path(tempfile.mkdtemp(prefix="gam-update-"))
    zip_path = tmp / "update.zip"
    zip_path.write_bytes(data)
    extract_dir = tmp / "extract"
    extract_dir.mkdir()
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        # GitHub zipball has a single top-level folder
        kids = [p for p in extract_dir.iterdir() if p.is_dir()]
        src_root = kids[0] if len(kids) == 1 else extract_dir
        _overlay_tree(src_root, root)
        _reinstall(root)
        return True, f"已下载并安装 {info.tag or info.latest}。\n请重启应用生效。"
    except Exception as e:
        return False, f"安装更新失败: {e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _overlay_tree(src: Path, dst: Path) -> None:
    """Copy update files over install, skipping venv/git/cache."""
    skip_names = {
        ".venv",
        "venv",
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "dist",
        "build",
        "*.egg-info",
    }
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        parts = set(rel.parts)
        if parts & {".venv", "venv", ".git", "__pycache__", ".pytest_cache"}:
            continue
        if any(p.endswith(".egg-info") for p in rel.parts):
            continue
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _reinstall(root: Path) -> None:
    """Best-effort editable reinstall into current interpreter."""
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(root), "-q"],
            cwd=str(root),
            timeout=180,
            check=False,
        )
    except Exception:
        pass


def open_release_page(info: UpdateInfo) -> None:
    import webbrowser

    url = info.release_url or "https://github.com"
    webbrowser.open(url)
