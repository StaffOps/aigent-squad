"""Export a FastAPI app's OpenAPI document without starting the server.

Usage (from the component directory, so its package is importable):
    python ../scripts/export_openapi.py app.main:app openapi.json

The output is deterministic (stable key order, trailing newline) so CI can
detect drift with `git diff --exit-code`.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 3 or ":" not in argv[1]:
        print("usage: export_openapi.py <module>:<attr> <output.json>", file=sys.stderr)
        return 2
    module_name, attr = argv[1].split(":", 1)
    sys.path.insert(0, str(Path.cwd()))
    app = getattr(importlib.import_module(module_name), attr)
    Path(argv[2]).write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
