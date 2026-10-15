from __future__ import annotations

import argparse
import json
from pathlib import Path

from policyStudy.policy.market_review.build_agent_packet import compact_packet, read_json, write_json


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compress MR_PACKET for direct GPT/Codex analysis and writing"
    )
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--catalysts", type=Path)
    args = parser.parse_args()

    catalyst_rows = read_json(args.catalysts) if args.catalysts else []
    if not isinstance(catalyst_rows, list):
        raise ValueError("catalysts JSON must be a list")

    compact = compact_packet(read_json(args.packet), catalyst_rows)
    encoded = json.dumps(compact, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) > 65536:
        raise ValueError(f"context packet exceeds 64 KiB scoped-read limit: {len(encoded)} bytes")
    write_json(args.output, compact)
    print(
        json.dumps(
            {"status": "PASS", "bytes": len(encoded), "output": str(args.output.resolve())},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
