#!/usr/bin/env python3
"""Persist a verified latest TPEx institutional snapshot for public consumers."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from institutional_flow import fetch_tpex_institutional_official  # noqa: E402


def write_snapshot(output: Path, timeout: int) -> dict[str, object]:
    result = fetch_tpex_institutional_official(timeout=timeout)
    payload: dict[str, object] = {
        "status": "ok",
        "data_date": result["data_date"],
        "count": result["count"],
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": "TPEx OpenAPI via GitHub Actions",
        "rows": result["rows"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "tpex-institutional-latest.json")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    payload = write_snapshot(args.output, args.timeout)
    print(f"TPEx snapshot {payload['data_date']} with {payload['count']} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
