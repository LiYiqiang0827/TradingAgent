from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    value = hashlib.sha256()
    value.update(path.read_bytes())
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize a deterministically validated, Codex-reviewed market-review run")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--glm-validation", type=Path, help="Optional when a bounded GLM draft was used")
    parser.add_argument("--codex-reviewed", action="store_true")
    args = parser.parse_args()
    if not args.codex_reviewed:
        raise ValueError("Final acceptance requires an explicit Codex substantive review")

    packet_validation = read_json(args.run_dir / "PACKET_VALIDATION.json")
    data_quality = read_json(args.run_dir / "DATA_QUALITY.json")
    glm_validation = read_json(args.glm_validation) if args.glm_validation else None
    market_packet = read_json(args.run_dir / "MR_PACKET.json")
    report_text = args.report.read_text(encoding="utf-8")
    errors = []
    if packet_validation.get("status") != "PASS":
        errors.append("packet validation did not pass")
    if data_quality.get("required_missing"):
        errors.append("required market sources are missing")
    if glm_validation and (glm_validation.get("validator") != "market-review-draft-v1" or glm_validation.get("status") != "PASS"):
        errors.append("GLM bounded draft validation did not pass")
    if re.search(r"\b(?:If|Then)\b|If\s*(?:-|→)\s*Then", report_text, flags=re.IGNORECASE):
        errors.append("report contains forbidden If-Then structure")
    required_topics = {
        "market_characterization": ("今日定性", "先说结论", "情绪阶段"),
        "ladder": ("连板梯队", "高标路线"),
        "themes": ("题材结构", "题材分级"),
        "migration": ("资金迁移",),
        "past_mainlines": ("过去三周主线追踪",),
        "first_pullback": ("低位首板", "首次回调", "候选观察"),
        "no_trade_permission": ("允许空仓", "保持空仓", "可空仓"),
    }
    missing_topics = [name for name, alternatives in required_topics.items() if not any(text in report_text for text in alternatives)]
    if missing_topics:
        errors.append(f"report is missing topics: {missing_topics}")

    trade_date = str(market_packet.get("metadata", {}).get("trade_date") or "")
    if len(trade_date) == 8:
        date_forms = {
            trade_date,
            f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}",
            f"{int(trade_date[4:6])}月{int(trade_date[6:])}日",
        }
        if not any(value in report_text for value in date_forms):
            errors.append("report does not identify the packet trade date")

    artifacts = {
        "report": {"path": str(args.report.resolve()), "sha256": digest(args.report)},
        "market_packet": {"path": str((args.run_dir / "MR_PACKET.json").resolve()), "sha256": digest(args.run_dir / "MR_PACKET.json")},
    }
    agent_packet = args.run_dir / "AGENT_PACKET.json"
    if agent_packet.is_file():
        artifacts["agent_packet"] = {"path": str(agent_packet.resolve()), "sha256": digest(agent_packet)}
    external_workers = {}
    if glm_validation:
        worker = glm_validation.get("worker") or {}
        external_workers["glm"] = {
            "model": worker.get("model", "unspecified"),
            "reasoning_effort": worker.get("reasoning_effort", "unspecified"),
            "role": "bounded draft",
            "content_was_unverified": True,
        }

    result = {
        "status": "PASS" if not errors else "FAIL",
        "trade_date": trade_date,
        "codex_substantive_review": True,
        "acceptance_policy": "deterministic market-data validation plus Codex substantive review",
        "errors": errors,
        "warnings": packet_validation.get("warnings", []),
        "artifacts": artifacts,
        "external_workers": external_workers,
        "accepted_at": datetime.now().astimezone().isoformat(),
    }
    target = args.run_dir / "RUN_ACCEPTANCE.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(target.resolve()), "errors": errors}, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
