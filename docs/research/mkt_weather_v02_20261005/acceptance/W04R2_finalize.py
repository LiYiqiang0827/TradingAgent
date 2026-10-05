"""Record completed visual review and the precise bounded change list."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
REPORT = ROOT / "docs/research/mkt_weather_v02_20261005"
RUN = Path("D:/ProjectRocketData/processed/TradingAgent/outputs/market_sentiment/mkt_weather_v02_20261005_01")
changed = [ROOT / f"policyStudy/policy/market_sentiment/{name}" for name in ("human.py", "cards.py", "tests/test_human.py")]
changed += [REPORT / name for name in (
    "human/README.md", "human/packets/README.md", "human/packets/cards.html", "human/packets/cards.pdf", "human/packets/reference_2025.csv",
    "human/private_key/audit_raw_facts.csv", "human/private_key/archived_facts.csv", "human/private_key/archived_facts.md", "human/private_key/sampling_summary.json",
    "reproduction/fonts/NotoSansSC-card.ttf", "reproduction/fonts/NotoSansSC-card-OFL.txt")]
changed += sorted((REPORT / "acceptance").glob("W04R2*"))
deleted = [REPORT / "human/packets/facts.csv", REPORT / "human/packets/facts.md"]
validation = json.loads((REPORT / "acceptance/W04R2_validation.json").read_text(encoding="utf-8"))
validation.update(status="ready_for_root_acceptance", visual_review={"first_page": "opened and inspected: legible Chinese, aligned 10 rows, no clipping", "densest_page": "page 3 opened and inspected: all four promotion groups and references fit, no glyph loss", "pdf_page_count": 15},
                  changed_files=[str(path) for path in changed], deleted_blind_wide_files=[str(path) for path in deleted],
                  excluded_from_submission=[str(REPORT / "human/private_key/tests")],
                  synthetic_test_directory_removed=False,
                  cleanup_rejection="Tool policy rejected the scoped PowerShell recursive-delete command; no deletion occurred and no alternate deletion route was used.")
validation["tests"].update(result="13 passed; post-guard targeted regression 2 passed, 11 deselected", font_originals_not_modified=True)
(REPORT / "acceptance/W04R2_validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
task = json.loads((RUN / "tasks/W04R2.json").read_text(encoding="utf-8"))
task.update(status="ready_for_root_acceptance", changed_files=[str(path) for path in [*changed, *deleted]], checks=validation,
            known_issues=validation["known_issues"] + [validation["cleanup_rejection"]], paths=validation["paths"], next_owner="root")
(RUN / "tasks/W04R2.json").write_text(json.dumps(task, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
print("W04R2 ready for root acceptance; exact change list recorded; tests tree excluded from submission")
