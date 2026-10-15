"""Dump the control-plane OpenAPI schema to ui/openapi.json.

    uv run --project services python ui/scripts/dump-openapi.py
"""

import json
from pathlib import Path

from corvus_services.api.app import create_app
from corvus_services.api.settings import ApiSettings

# A dev token only satisfies the "auth configured" check; the schema does not depend on it.
settings = ApiSettings(dev_token="schema-dump")  # type: ignore[arg-type]
schema = create_app(settings).openapi()
out = Path(__file__).resolve().parent.parent / "openapi.json"
out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
print(f"wrote {out}")
