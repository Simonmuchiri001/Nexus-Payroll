"""Vercel serverless entrypoint for the Nexus Enterprise API.

Vercel invokes this module as a Python function. The ASGI app keeps its
existing `/api/v1` prefix internally, so the rewrite in `vercel.json`
must preserve the path: `/api/v1/...` -> `/api/index.py` with the original
path intact (Vercel passes the full path to the function).

In production, `NEXUS_ENVIRONMENT=production`, `NEXUS_DATABASE_URL`
(Neon pooled, SSL required) and `NEXUS_JWT_SECRET` come from Vercel
environment variables. Never commit real values.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `vercel dev` / plain `uv run` without a pip install: the API package
# lives under backend/src in the monorepo. On Vercel the installed wheel
# already provides `nexus_api`, so this is a harmless no-op there.
_BACKEND_SRC = Path(__file__).resolve().parent.parent / "backend" / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from nexus_api.main import create_app  # noqa: E402

app = create_app()
