"""Environment variables renamed from the product's BTTCO era.

Every ``BTTCO_*`` variable now has a ``RYOZAN_*`` name. The old name keeps
working so existing deployments, CI runners and shells do not break, but
reading a value through the old name logs a one-time deprecation warning
naming the replacement. Remove an entry once nothing sets the old name.
"""

import logging
import os
from collections.abc import Mapping

logger = logging.getLogger("ryozan.config")

# New name -> legacy name.
RENAMED_ENV_VARS: dict[str, str] = {
    "ELEVENLABS_RYOZAN_AGENT_ID": "ELEVENLABS_BTTCO_AGENT_ID",
    "RYOZAN_MCP_UPSTREAM_URL": "BTTCO_MCP_UPSTREAM_URL",
    "RYOZAN_MCP_READ_TIMEOUT": "BTTCO_MCP_READ_TIMEOUT",
    "RYOZAN_MCP_CONNECT_TIMEOUT": "BTTCO_MCP_CONNECT_TIMEOUT",
    "RYOZAN_TEST_DATABASE_URL": "BTTCO_TEST_DATABASE_URL",
    "RYOZAN_E2E_SANDBOX": "BTTCO_E2E_SANDBOX",
    "RYOZAN_ACCOUNT_STATUS_PG_TESTS": "BTTCO_ACCOUNT_STATUS_PG_TESTS",
    "RYOZAN_CHECKIN_PG_TESTS": "BTTCO_CHECKIN_PG_TESTS",
    "RYOZAN_MAILBOX_PG_TESTS": "BTTCO_MAILBOX_PG_TESTS",
    "RYOZAN_API_BASE": "BTTCO_API_BASE",
    "RYOZAN_TOKEN": "BTTCO_TOKEN",
}

_warned: set[str] = set()


def warn_legacy_env(new_name: str, environ: Mapping[str, str] | None = None) -> None:
    """Log once when only the legacy name of ``new_name`` is set."""
    legacy = RENAMED_ENV_VARS[new_name]
    env = os.environ if environ is None else environ
    if new_name in env or legacy not in env or legacy in _warned:
        return
    _warned.add(legacy)
    logger.warning("%s is deprecated; set %s instead. The old name still works for now.", legacy, new_name)


def getenv(new_name: str, default: str | None = None) -> str | None:
    """Read ``new_name`` from the environment, falling back to its legacy name."""
    value = os.environ.get(new_name)
    if value is not None:
        return value
    legacy = RENAMED_ENV_VARS[new_name]
    value = os.environ.get(legacy)
    if value is None:
        return default
    warn_legacy_env(new_name)
    return value
