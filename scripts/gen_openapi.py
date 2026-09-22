#!/usr/bin/env python3
"""
Regenerate openapi.json and openapi.yaml from the running FastAPI app.

Both files are produced from a single in-memory copy of `app.openapi()`,
so they always agree with each other and with what the server actually
serves at /openapi.json. The only editorial additions are the `servers`
block and the `components.securitySchemes` / root `security` documentation
(public metadata) — everything else mirrors the code.

Usage:
    python scripts/gen_openapi.py            # write both files
    python scripts/gen_openapi.py --check    # fail (exit 1) if files are stale
"""
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

# Production / local documentation servers. Purely editorial metadata:
# FastAPI does not emit a `servers` block, and it has no bearing on /openapi.json.
SERVERS = [
    {
        "url": "https://api.ognon-radar.local",
        "description": "Production server",
    },
    {
        "url": "http://localhost:8000",
        "description": "Local development",
    },
]

# Client-identity/API-key auth documentation. Purely editorial metadata like
# SERVERS: FastAPI will not emit a securitySchemes block on its own because
# auth is enforced via plain Optional header dependencies (core/auth.py), not
# FastAPI Security() dependencies. The API is intentionally public — these are
# optional credentials (root `security: []`), not requirements.
SECURITY_SCHEMES = {
    "X-Client-ID": {
        "type": "apiKey",
        "in": "header",
        "name": "X-Client-ID",
        "description": (
            "Client identity for job tracking. A bearer token generated locally "
            "by the client (e.g. by the web client, kept in localStorage). "
            "Scopes jobs, captures, screenshots, and webhooks to one client. "
            "Anyone who holds it can access that client's data — treat it as a "
            "credential."
        ),
    },
    "X-API-Key": {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": (
            "Optional API key. A per-client key minted via "
            "POST /api/v1/client/key replaces X-Client-ID on subsequent "
            "requests. When the deployment config sets security.api_key, a "
            "matching key is required and grants admin (cross-client) access. "
            "The default local config leaves it empty, which disables key "
            "enforcement."
        ),
    },
}

# Preferred top-level key order for a readable, conventional document.
KEY_ORDER = ["openapi", "info", "servers", "paths", "components", "security", "tags"]


def build_spec() -> dict:
    """Return the OpenAPI document exactly as the app serves it (plus servers)."""
    sys.path.insert(0, str(REPO_ROOT))
    from main import app  # noqa: E402  (needs repo root on sys.path)

    spec = app.openapi()
    spec["servers"] = SERVERS
    # Document the optional credential headers. Root `security: []` states the
    # posture explicitly: the API is public, these schemes are optional.
    spec.setdefault("components", {})["securitySchemes"] = SECURITY_SCHEMES
    spec["security"] = []

    ordered = {}
    for key in KEY_ORDER:
        if key in spec:
            ordered[key] = spec.pop(key)
    ordered.update(spec)  # any stragglers keep their original order
    return ordered


def main() -> int:
    spec = build_spec()

    openapi_json = REPO_ROOT / "openapi.json"
    openapi_yaml = REPO_ROOT / "openapi.yaml"

    json_doc = json.dumps(spec, indent=2, ensure_ascii=False) + "\n"
    yaml_doc = yaml.safe_dump(
        spec,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100_000,
    )

    if "--check" in sys.argv:
        stale = []
        # A missing file is stale too — otherwise deleting the committed spec
        # would silently pass CI instead of being caught as drift.
        if not openapi_json.exists() or openapi_json.read_text() != json_doc:
            stale.append(str(openapi_json))
        if not openapi_yaml.exists() or openapi_yaml.read_text() != yaml_doc:
            stale.append(str(openapi_yaml))
        if stale:
            print(f"STALE: {' '.join(stale)} — run `make openapi`")
            return 1
        print("OK: openapi.json and openapi.yaml are up to date")
        return 0

    openapi_json.write_text(json_doc)
    openapi_yaml.write_text(yaml_doc)
    print(f"Wrote {openapi_json} ({openapi_json.stat().st_size} bytes)")
    print(f"Wrote {openapi_yaml} ({openapi_yaml.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())