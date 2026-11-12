#!/usr/bin/env bash
# Ryozan lint-fix — run the exact CI lint/format commands locally with the pinned version.
#
# Usage:
#   ./scripts/lint-fix.sh              # check + auto-fix (default)
#   ./scripts/lint-fix.sh --check      # CI parity (no fixes); fails if drift exists
#   ./scripts/lint-fix.sh --format     # only format, no lint
#
# Why this exists:
#   CI installs `ruff==0.17.4` (see .github/workflows/api.yml + api/requirements-dev.txt).
#   If you run a different ruff locally, format output differs and CI fails on drift
#   that wasn't in your diff. This script isolates the pinned version in /tmp so it
#   doesn't conflict with whatever ruff is on your PATH.
#
# Windows + CRLF caveat:
#   With `core.autocrlf=true`, Windows checkouts have CRLF line endings while the
#   committed content is LF. Ruff sees the CRLF and may report "would reformat",
#   but CI (Linux, LF) sees the same files as already-formatted. If `--check`
#   reports drift on files you haven't edited, it's CRLF noise, not real drift —
#   `git checkout -- <file>` resets to the LF version and `--check` passes again.

set -euo pipefail

RUFF_PIN="ruff==0.17.4"
VENV_DIR="${TMPDIR:-/tmp}/ryozan-ruff-pin-venv"
SCOPE=( "app/" "main.py" )
MODE="${1:-fix}"

# Resolve repo root regardless of where this script is called from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}/api"

# Set up isolated venv with the pinned ruff (only if not already set up).
if [[ ! -x "${VENV_DIR}/bin/ruff" && ! -x "${VENV_DIR}/Scripts/ruff.exe" ]]; then
  echo "→ Creating isolated venv at ${VENV_DIR} with ${RUFF_PIN}"
  python -m venv "${VENV_DIR}"
fi

# Locate pip + ruff (handles both Unix-style and Windows-style venvs).
if [[ -d "${VENV_DIR}/bin" ]]; then
  VENV_BIN="${VENV_DIR}/bin"
  PIP="${VENV_BIN}/pip"
  RUFF="${VENV_BIN}/ruff"
elif [[ -d "${VENV_DIR}/Scripts" ]]; then
  VENV_BIN="${VENV_DIR}/Scripts"
  PIP="${VENV_BIN}/pip.exe"
  RUFF="${VENV_BIN}/ruff.exe"
else
  echo "✗ Could not find venv bin dir at ${VENV_DIR}/bin or ${VENV_DIR}/Scripts" >&2
  exit 1
fi

# Install ruff if it's not yet in this venv.
if [[ ! -x "${RUFF}" ]]; then
  "${PIP}" install --quiet "${RUFF_PIN}"
fi

# Ensure pinned version is installed (in case venv existed but was empty).
INSTALLED_VERSION="$("${RUFF}" --version 2>/dev/null | awk '{print $2}' || echo "")"
EXPECTED_VERSION="${RUFF_PIN#ruff==}"
if [[ "${INSTALLED_VERSION}" != "${EXPECTED_VERSION}" ]]; then
  echo "→ Installing ${RUFF_PIN} (was: ${INSTALLED_VERSION:-not installed})"
  "${PIP}" install --quiet "${RUFF_PIN}"
fi

echo "→ Using $("${RUFF}" --version) at ${RUFF}"
echo

case "${MODE}" in
  --check)
    echo "═══ Ruff lint (check only) ═══"
    "${RUFF}" check "${SCOPE[@]}"
    echo
    echo "═══ Ruff format (check only) ═══"
    "${RUFF}" format --check "${SCOPE[@]}"
    echo
    echo "✓ CI parity check passed."
    ;;
  --format)
    echo "═══ Ruff format ═══"
    "${RUFF}" format "${SCOPE[@]}"
    ;;
  fix|"")
    echo "═══ Ruff format ═══"
    "${RUFF}" format "${SCOPE[@]}"
    echo
    echo "═══ Ruff check --fix ═══"
    "${RUFF}" check --fix "${SCOPE[@]}" || {
      echo
      echo "⚠ Ruff check found issues that require manual fixes (see above)."
      exit 1
    }
    echo
    echo "✓ Lint + format pass complete. Run with --check to confirm CI parity."
    ;;
  *)
    echo "Unknown mode: ${MODE}" >&2
    echo "Usage: $0 [--check|--format|--fix]" >&2
    exit 2
    ;;
esac
