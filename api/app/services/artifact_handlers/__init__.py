"""
Artifact handler registrations.

Importing this package triggers every handler module to run its
`artifacts.register(...)` call at module load time. The artifact
primitive (app.services.artifacts) stays lean; each concrete type
lives in its own file here.

Add new artifact types by dropping a new module in this directory
and importing it below.
"""

from app.services.artifact_handlers import (
    quote_proposal,  # noqa: F401
    slot_proposal,  # noqa: F401
)
