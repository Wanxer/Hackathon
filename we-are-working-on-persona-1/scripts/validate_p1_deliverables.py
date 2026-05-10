from __future__ import annotations

import json
from pathlib import Path

from p1_pipeline import validate_deliverables


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    checks = validate_deliverables(root)
    print(json.dumps(checks, ensure_ascii=False, indent=2))
