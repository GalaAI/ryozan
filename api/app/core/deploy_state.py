"""
Deployment metadata helper for health probes and local deploy-state checks.

BTTCO already had health probes, but it did not expose which branch and commit
were actually live. This module keeps that truth in one place so the API,
operator docs, and local comparison script all report the same deploy state.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache

_COMMIT_ENV_VARS = (
    "APP_GIT_SHA",
    "RAILWAY_GIT_COMMIT_SHA",
    "VERCEL_GIT_COMMIT_SHA",
    "SOURCE_COMMIT_SHA",
    "COMMIT_SHA",
    "GIT_COMMIT_SHA",
)
_BRANCH_ENV_VARS = (
    "APP_GIT_BRANCH",
    "RAILWAY_GIT_BRANCH",
    "VERCEL_GIT_COMMIT_REF",
    "SOURCE_BRANCH",
    "GIT_BRANCH",
)
_ENVIRONMENT_VARS = (
    "RAILWAY_ENVIRONMENT_NAME",
    "VERCEL_ENV",
    "APP_ENV",
)


def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def _git_output(*args: str) -> str | None:
    try:
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL).strip() or None
    except Exception:
        return None


def _detect_platform() -> str:
    if _first_env(("RAILWAY_GIT_COMMIT_SHA", "RAILWAY_GIT_BRANCH", "RAILWAY_ENVIRONMENT_NAME")):
        return "railway"
    if _first_env(("VERCEL_GIT_COMMIT_SHA", "VERCEL_GIT_COMMIT_REF", "VERCEL_ENV")):
        return "vercel"
    if _git_output("rev-parse", "HEAD"):
        return "git"
    return "unknown"


def _short_sha(commit: str | None) -> str | None:
    if not commit:
        return None
    return commit[:7]


@lru_cache
def get_deploy_state() -> dict[str, str | bool | None]:
    commit = _first_env(_COMMIT_ENV_VARS) or _git_output("rev-parse", "HEAD")
    branch = _first_env(_BRANCH_ENV_VARS) or _git_output("rev-parse", "--abbrev-ref", "HEAD")
    platform = _detect_platform()
    environment = _first_env(_ENVIRONMENT_VARS) or ("local" if platform == "git" else None)

    return {
        "platform": platform,
        "environment": environment,
        "branch": branch,
        "commit": commit,
        "commit_short": _short_sha(commit),
        "commit_known": bool(commit),
        "branch_known": bool(branch),
    }
