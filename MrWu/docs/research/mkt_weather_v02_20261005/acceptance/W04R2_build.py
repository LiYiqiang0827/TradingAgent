"""One bounded official card refresh with immutable sample/label checks."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import pandas as pd
import numpy as np
from pypdf import PdfReader
import fitz

ROOT = Path(__file__).resolve().parents[4]
REPORT = ROOT / "docs/research/mkt_weather_v02_20261005"
RUN = Path("D:/ProjectRocketData/processed/TradingAgent/outputs/market_sentiment/mkt_weather_v02_20261005_01")
sys.path.insert(0, str(ROOT))
from policyStudy.policy.market_sentiment.human import create_packets
from policyStudy.policy.market_sentiment.cards import reference_2025, raw_values, card_rows, FONT_PATH

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")

source_path = REPORT / "data/mkt_daily.csv"
frozen = [source_path, REPORT / "human/private_key/selection_manifest.csv", REPORT / "human/packets/Harry_labels.csv", REPORT / "human/packets/Li_labels.csv", REPORT / "human/packets/nominations_template.csv"]
before = {str(path): sha(path) for path in frozen}
daily = pd.read_csv(source_path, float_precision="round_trip")
dates = pd.read_csv(frozen[1]).trade_date.tolist()
result = create_packets(daily, REPORT / "human")
after = {str(path): sha(path) for path in frozen}
assert before == after, "Source, sample or independent labels changed"
assert result["seed"] == 20261005 and result["count"] == 15
for name in ("Harry", "Li"):
    labels = pd.read_csv(result["paths"][f"{name}_labels"], keep_default_na=False)
    assert labels.drop(columns="trade_date").eq("").to_numpy().all()
assert not (REPORT / "human/packets/facts.csv").exists()
assert not (REPORT / "human/packets/facts.md").exists()
references = reference_2025(daily)
observations = raw_values(daily[daily.trade_date.str.startswith("2025")])
assert len(observations) == 243
hand_results = []
for _, row in references.iterrows():
    values = sorted(observations[row.metric].dropna().tolist())
    for quantile, column in ((.1, "p10"), (.5, "p50"), (.9, "p90")):
        position = (len(values) - 1) * quantile
        lower = int(np.floor(position))
        upper = int(np.ceil(position))
        hand = values[lower] + (values[upper] - values[lower]) * (position - lower)
        assert abs(hand - row[column]) < 1e-10, (row.metric, column)
    hand_results.append({"metric": row.metric, "n_valid": len(values), "linear_quantiles_hand_checked": True})
reference_file = pd.read_csv(result["paths"]["reference_2025"], float_precision="round_trip")
pd.testing.assert_frame_equal(reference_file, references, check_exact=True)
text = Path(result["paths"]["cards_html"]).read_text(encoding="utf-8")
assert text.count("<section class='card'>") == 15 and text.count("<tr><td>") == 150
assert not any(field in text for field in ("mkt_hit", "mkt_cont", "mkt_act", "mkt_weather", "mkt_relay", "mkt_forecast", "machine_", "human_bins", "weather_threshold"))
pdf = Path(result["paths"]["cards_pdf"])
reader = PdfReader(pdf)
assert len(reader.pages) == 15, f"Expected 15 pages, found {len(reader.pages)}"
assert all(dates[i] in page.extract_text() for i, page in enumerate(reader.pages))
pdf_text = "\n".join(page.extract_text() for page in reader.pages)
assert not any(field in pdf_text for field in ("mkt_hit", "mkt_cont", "mkt_act", "mkt_weather", "mkt_relay", "mkt_forecast", "分类阈值"))
font_cmap = __import__("fontTools.ttLib", fromlist=["TTFont"]).TTFont(FONT_PATH).getBestCmap()
missing_glyphs = sorted({char for char in pdf_text if not char.isspace() and ord(char) not in font_cmap})
assert not missing_glyphs, missing_glyphs
doc = fitz.open(pdf)
dense = max(range(len(doc)), key=lambda index: len(doc[index].get_text()))
outside = []
for index, page in enumerate(doc):
    for block in page.get_text("blocks"):
        if min(block[0], block[1]) < 0 or block[2] > page.rect.width or block[3] > page.rect.height:
            outside.append({"page": index + 1, "rect": list(block[:4])})
assert not outside
poppler = Path("C:/Users/HarryWu_IOKI/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe")
rendered = {}
for name, page in (("first", 1), ("dense", dense + 1)):
    prefix = REPORT / f"acceptance/W04R2_{name}"
    subprocess.run([str(poppler), "-f", str(page), "-l", str(page), "-singlefile", "-r", "125", "-png", str(pdf), str(prefix)], check=True, capture_output=True)
    rendered[name] = {"page": page, "path": str(prefix.with_suffix(".png"))}
summary = {"task_id": "W04R2", "status": "ready_for_visual_review", "generated_at": datetime.now(timezone.utc).isoformat(),
           "source_days": len(daily), "reference_days_2025": len(observations), "quantile_method": "linear", "quantiles": [.1, .5, .9],
           "unchanged_source_sample_labels": before == after, "frozen_fingerprints": before, "seed": result["seed"],
           "sample_count": result["count"], "pdf_pages": len(reader.pages), "items_per_card": 10,
           "reference_metrics": hand_results, "reference_csv_roundtrip_exact": True, "raw_rates_not_smoothed": True,
           "no_missing_font_glyphs": True, "no_text_outside_pages": True, "blind_no_readings_weather_answers_thresholds": True,
           "rendered": rendered, "paths": result["paths"], "font_path": str(FONT_PATH), "font_license": str(FONT_PATH.with_name("NotoSansSC-card-OFL.txt")),
           "tests": {"initial_worker_passed": 13, "simulated_files_only": "human/private_key/tests/tmp retained outside submission; see controller revision test evidence"},
           "known_issues": ["Both independent labels remain blank; definition acceptance still awaits labels.", "Approximately 15 remembered nominations are still absent."]}
write_json(REPORT / "acceptance/W04R2_validation.json", summary)
task = json.loads((RUN / "tasks/W04R2.json").read_text(encoding="utf-8"))
task.update({"status": "ready_for_visual_review", "changed_files": [str(ROOT / f"policyStudy/policy/market_sentiment/{name}") for name in ("human.py", "cards.py", "tests/test_human.py")], "checks": summary, "known_issues": summary["known_issues"], "paths": result["paths"], "next_owner": "root"})
write_json(RUN / "tasks/W04R2.json", task)
print(json.dumps({"status": summary["status"], "pages": len(reader.pages), "items_per_card": 10, "reference_metrics_n": len(references), "frozen_inputs_unchanged": True, "renders": rendered}, ensure_ascii=False))
