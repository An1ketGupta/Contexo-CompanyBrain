"""Verify the FastAPI app boots and lists its routes."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app


def main() -> None:
    print(f"app: {app.title} v{app.version}")
    print(f"routes: {len(app.routes)}")
    for r in app.routes:
        methods = getattr(r, "methods", None)
        path = getattr(r, "path", "?")
        verb = next(iter(methods)) if methods else "-"
        print(f"  {verb:7} {path}")


if __name__ == "__main__":
    main()
