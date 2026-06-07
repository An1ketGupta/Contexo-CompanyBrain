"""List embedding-capable models available to this API key."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google import genai

from app.config import get_settings


def main() -> None:
    client = genai.Client(api_key=get_settings().gemini_api_key)
    for m in client.models.list():
        actions = getattr(m, "supported_actions", None) or []
        if "embedContent" in actions:
            print(f"  EMBED  {m.name}  dims={getattr(m, 'output_token_limit', '?')} actions={actions}")
        elif any("embed" in a.lower() for a in actions):
            print(f"  ?      {m.name}  actions={actions}")


if __name__ == "__main__":
    main()
