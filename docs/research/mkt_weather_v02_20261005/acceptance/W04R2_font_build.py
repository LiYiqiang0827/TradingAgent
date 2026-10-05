"""Offline card-only font derivation, preserving original chart fonts."""
from pathlib import Path
import shutil
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools import subset

ROOT = Path(__file__).resolve().parents[4]
SOURCE = Path("D:/ProjectRocketData/processed/TradingAgent/outputs/speculation_sentiment/sentiment_cycle_20261005_01/render/NotoSansSC-variable.ttf")
OUTPUT = ROOT / "docs/research/mkt_weather_v02_20261005/reproduction/fonts"
text = "".join((ROOT / f"policyStudy/policy/market_sentiment/{name}").read_text(encoding="utf-8") for name in ("cards.py", "human.py"))
font = TTFont(SOURCE)
font = instantiateVariableFont(font, {"wght": 400}, inplace=True)
options = subset.Options()
options.name_IDs = ["*"]
options.name_legacy = True
options.name_languages = ["*"]
subsetter = subset.Subsetter(options=options)
subsetter.populate(unicodes=sorted({*range(32, 127), *(ord(char) for char in text)}))
subsetter.subset(font)
for record in font["name"].names:
    if record.nameID in (1, 4, 6, 16):
        record.string = "RocketCardSansSC".encode(record.getEncoding())
    elif record.nameID in (2, 17):
        record.string = "Regular".encode(record.getEncoding())
OUTPUT.mkdir(parents=True, exist_ok=True)
font.save(OUTPUT / "NotoSansSC-card.ttf")
shutil.copyfile(ROOT / "docs/research/mrwu_sentiment_cycle_20261005/reproduction/fonts/OFL.txt", OUTPUT / "NotoSansSC-card-OFL.txt")
print("Card font and original SIL OFL copied; original font files unchanged")
