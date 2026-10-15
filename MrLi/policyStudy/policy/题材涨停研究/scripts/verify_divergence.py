"""Reconcile historical minute closes and protect frozen v1 artifacts."""
import hashlib
import json
import pandas as pd

from study_divergence import SOURCE, OUT
from build_case_dossiers import normalize, save


def main():
    day=normalize(pd.read_pickle(SOURCE/"cache/day.pkl")).set_index(["trade_date","ts_code"])
    checks=[]; requested=0
    for path in sorted((OUT/"cache").glob("minute_*.pkl")):
        date=path.stem.split("_")[1]
        requested+=json.loads(path.with_suffix(".json").read_text())["requested"]
        minute=pd.read_pickle(path)
        stamp=pd.Timestamp(date).strftime("%Y-%m-%d")+" 15:00:00"
        rows=minute.loc[minute.datetime.eq(stamp)].copy()
        assert not rows.ts_code.duplicated().any()
        rows["trade_date"]=date
        joined=rows.set_index(["trade_date","ts_code"]).join(day[["close"]],how="left")
        for key,r in joined.iterrows():
            checks.append({"date":key[0],"ts_code":key[1],"price":r.price,"close":r.close})
    a=pd.DataFrame(checks)
    complete=a.price.notna()&a.close.notna()
    mismatches=a.loc[complete&((a.price-a.close).abs()>.005)]
    manifest=json.loads((SOURCE/"prediction_manifest.json").read_text()); hashes={}
    for stem,filename in [("predictions","predictions.json"),("labels","labels.json"),("model","frozen_model.json")]:
        digest=hashlib.sha256((SOURCE/filename).read_bytes()).hexdigest()
        hashes[stem]={"sha256":digest,"unchanged":digest==manifest[stem+"_sha256"]}
    result={"requested_stock_days":requested,"minute_15_stock_days":len(a),
            "comparable_stock_days":int(complete.sum()),"missing_15_stock_days":requested-len(a),
            "mismatches":mismatches.to_dict("records"),"missing_daily_close":int((~complete).sum()),"v1_hashes":hashes}
    save(OUT/"verification.json",result)
    assert len(mismatches)==0 and all(r["unchanged"] for r in hashes.values()),result
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
