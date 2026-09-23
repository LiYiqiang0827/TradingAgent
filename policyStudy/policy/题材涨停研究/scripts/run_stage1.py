"""Reproducible Stage 1 research; all market reads go through data_provider.

Run with the repository .venv/bin/python. Local caches and granular reports live
under the existing policy's ignored data directory, never in the source DBs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, confusion_matrix
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from coreClient import data_provider as provider
from stage1_engine import (canonical, number, chain_features, eligible_market, market_state,
                           theme_and_leader_features, intraday_rank, EMOTION_FEATURES,
                           THEME_FEATURES, LEADER_FEATURES)

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "stage1_v1"
START, END, FREEZE = "20260101", "20260831", "20260430"


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    return value


def write_json(path, value):
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def cached(out, name, method, **kwargs):
    path = out / "cache" / (name + ".pkl")
    if path.exists():
        return pd.read_pickle(path)
    frame = getattr(provider, method)(source="database_only", **kwargs)
    frame.to_pickle(path)
    print(f"cached {name}: {len(frame)} rows", flush=True)
    return frame


def load_core(out):
    args = dict(start_date=START, end_date=END)
    cal = cached(out, "calendar", "get_tradecal", **args)
    dates = sorted(cal.cal_date.astype(str).str.replace("-", "").unique())
    kpl = cached(out, "kpl", "get_kpl_list", tags=["涨停", "炸板", "跌停"], **args)
    day = cached(out, "day", "get_day", qfq=False, start_date="20251201", end_date=END)
    basic = cached(out, "basic", "get_daily_basic", **args)
    limits = cached(out, "limits", "get_stk_limit", **args)
    index = cached(out, "index", "get_index_daily", **args)
    for frame in [kpl, day, basic, limits, index]:
        frame["trade_date"] = frame.trade_date.astype(str).str.replace("-", "")
    return dates, kpl, day, basic, limits, index


def extract(out, with_minutes=True):
    dates, kpl, day, basic, limits, index = load_core(out)
    day = chain_features(day)
    groups = [{str(k): g.copy() for k, g in f.groupby("trade_date")} for f in [kpl, day, basic, limits, index]]
    kg, dg, bg, lg, ig = groups
    previous_ups, previous_heights, previous_close = [], {}, {}
    memberships, past_themes, history = {}, {}, []
    records, intradays, audit = [], [], []
    for i, date in enumerate(dates):
        daily = dg.get(date, pd.DataFrame())
        if daily.empty or date not in lg or date not in kg or date not in ig:
            raise RuntimeError(f"Mandatory daily data absent: {date}; do not treat as zero activity")
        market = eligible_market(daily, lg[date])
        raw = kg[date].copy()
        duplicate = raw.duplicated(["ts_code", "tag"]).sum()
        if duplicate:
            raise RuntimeError(f"Duplicate KPL stock/tag at {date}")
        permitted = ~raw.name.fillna("").str.contains("ST|退", case=False, regex=True)
        permitted &= ~raw.ts_code.str.endswith(".BJ")
        candidates = raw.loc[permitted]
        orphan = candidates.loc[~candidates.ts_code.isin(market.ts_code)]
        usable = candidates.loc[candidates.ts_code.isin(market.ts_code)].copy()
        ups = usable.loc[usable.tag == "涨停"].copy()
        breaks = usable.loc[usable.tag == "炸板"].copy()
        ups["primary"] = ups.lu_desc.map(canonical)
        if ups.primary.eq("").any():
            raise RuntimeError(f"Missing primary attribution: {date}")
        def height(row):
            import re
            m = re.fullmatch(r"(\d+)连板", str(row.status))
            return int(m.group(1)) if m else previous_heights.get(row.ts_code, 0)+1
        ups["height"] = ups.apply(height, axis=1)
        news = cached(out, "news_"+date, "get_news", src="cls", start_date=date, end_date=date)
        if len(news) >= 5000:
            raise RuntimeError(f"News default cap reached: {date}; completeness not established")
        cutoff = datetime.strptime(date, "%Y%m%d").strftime("%Y-%m-%d")+" 18:00:00"
        news = news.loc[news.datetime <= cutoff].copy()
        state = market_state(market, ups, breaks, previous_ups, previous_close, history, ig[date])
        theme, leaders, unknown = theme_and_leader_features(
            ups, breaks, bg.get(date, pd.DataFrame(columns=["ts_code"])), daily, news,
            state, past_themes, memberships, i)
        next_trade_day = datetime.strptime(dates[i+1], "%Y%m%d").strftime("%Y-%m-%d") if i+1 < len(dates) else None
        inactive = [{"theme": t, "phase": "退潮/暂时消失", "breadth": 0}
                    for t, past in past_themes.items() if t not in set(ups.primary) and i-past[-1]["ordinal"] <= 5]
        rotation = {
            "top3_share": sum(sorted([t["share"] for t in theme], reverse=True)[:3]),
            "hhi": sum(t["share"]**2 for t in theme),
            "coexistence_count": sum(t["breadth"] >= 4 for t in theme),
            "amount_ratio": state["amount_ratio"],
            "interpretation": "缩量时优先观察集中主线" if state["amount_ratio"] < .85 else "放量允许多主题共存" if state["amount_ratio"] > 1.15 else "常态轮动",
        }
        record = {"date": date, "decision_available_assumed": next_trade_day+" 06:00:00 Asia/Shanghai" if next_trade_day else "next_trading_day 06:00:00 Asia/Shanghai",
                  "field_time_reconstruction_only": True, "market": state, "themes": theme,
                  "leaders": leaders, "inactive_themes": inactive, "rotation": rotation,
                  "unknown_breaks": unknown}
        records.append(record)
        counts = {"date": date, "raw_kpl_up": int((raw.tag == "涨停").sum()),
                  "raw_kpl_break": int((raw.tag == "炸板").sum()), "valid_up": len(ups),
                  "excluded_st_bj_or_delist_name": int((~permitted).sum()),
                  "no_eligible_daily": len(orphan), "no_eligible_daily_codes": orphan.ts_code.tolist(),
                  "eligible_market": len(market), "missing_limit_rows": int((~daily.ts_code.isin(lg[date].ts_code)).sum()),
                  "news_cls_until_18": len(news), "unknown_breaks": len(unknown), "valid_breaks": len(breaks)}
        # Catch inconsistent event/price dates, while retaining audit information.
        joined = ups.merge(market[["ts_code", "close", "up_limit"]], on="ts_code")
        counts["up_price_mismatch"] = int((abs(joined.close-joined.up_limit) > .011).sum())
        audit.append(counts)
        if with_minutes and i:
            prev = records[-2]
            minute = cached(out, "minute_"+date, "get_minute", trade_date=date,
                            ts_codes=[r["ts_code"] for r in prev["leaders"]])
            if not minute.empty and not minute.trade_date.astype(str).str.replace("-", "").eq(date).all():
                raise RuntimeError("Minute response date mismatch")
            for clock in ["09:45", "10:30", "14:00"]:
                rankings = intraday_rank(prev["themes"], prev["leaders"], minute, previous_close, clock)
                candidates_now = [r for r in rankings if r["eligible"]]
                intradays.append({"date": date, "feature_date": dates[i-1], "clock": clock,
                                  "theme": candidates_now[0]["theme"] if candidates_now else None,
                                  "leader": candidates_now[0]["leader"] if candidates_now else None,
                                  "rankings": rankings})
        history.append(state)
        for t in theme:
            past_themes.setdefault(t["theme"], []).append(t)
        for r in ups.to_dict("records"):
            memberships[r["ts_code"]] = (r["primary"], i)
        previous_ups = ups.ts_code.tolist()
        previous_heights = dict(zip(ups.ts_code, ups.height))
        previous_close = dict(zip(daily.ts_code, daily.close))
        if i % 20 == 0 or i == len(dates)-1:
            print(f"features {i+1}/{len(dates)} {date}", flush=True)
    write_json(out/"features.json", records)
    write_json(out/"intraday_predictions.json", intradays)
    write_json(out/"audit.json", audit)
    manifest = {
        "created_at": datetime.now().isoformat(), "start": START, "end": END,
        "protocol_sha256": digest(Path(__file__).with_name("stage1_protocol.md")),
        "source": "data_provider.database_only", "feature_sha256": digest(out/"features.json"),
        "cache_sha256": {p.name: digest(p) for p in sorted((out/"cache").glob("*.pkl"))},
        "source_db_stat": {p.name: {"bytes": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
                           for p in (ROOT/"offlineDataManager/data").glob("*.db")},
        "code_sha256": {p.name: digest(p) for p in [Path(__file__), Path(__file__).with_name("stage1_engine.py"), ROOT/"coreClient/data_provider.py"]},
    }
    write_json(out/"manifest.json", manifest)


def winners(counter):
    return sorted(k for k, v in counter.items() if v == max(counter.values())) if counter else []


def make_labels(out, records):
    """Evaluation-only outcome construction, separate from feature generation."""
    _, _, day, _, _, index = load_core(out)
    returns = day.set_index(["trade_date", "ts_code"]).pct_chg.to_dict()
    benchmark = index.loc[index.ts_code == "000300.SH"].set_index("trade_date").pct_chg.to_dict()
    dates = [r["date"] for r in records]
    result = []
    for i, record in enumerate(records):
        date = record["date"]
        label = {"date": date, "emotion": None, "mainline": [], "mainline_next_day": [],
                 "leaders": {}, "height_leaders": {}, "maturity": {}}
        if i+1 < len(dates):
            tomorrow = dates[i+1]
            values = [returns.get((tomorrow, r["ts_code"]), np.nan) for r in record["leaders"]]
            arr = np.array(values, dtype=float)
            coverage = np.isfinite(arr).sum()/max(1, len(arr))
            label["emotion_price_coverage"] = coverage
            if len(arr) and coverage >= .95 and tomorrow in benchmark:
                excess = float(np.nanmedian(arr))-benchmark[tomorrow]
                bad = float(np.mean(arr[np.isfinite(arr)] < -3))
                label["emotion"] = "cold" if excess < -.5 or bad >= .3 else "hot" if excess > .5 else "neutral"
                label["emotion_evidence"] = {"median_excess": excess, "loss_over_3pct_share": bad}
                label["maturity"]["emotion"] = tomorrow
            label["mainline_next_day"] = winners({t["theme"]: t["breadth"] for t in records[i+1]["themes"]})
        if i+3 < len(dates):
            cnt = Counter()
            for future in records[i+1:i+4]:
                cnt.update({t["theme"]: t["breadth"] for t in future["themes"]})
            label["mainline"] = winners(cnt)
            label["future3_breadth"] = dict(cnt)
            label["maturity"]["mainline"] = dates[i+3]
        if i+2 < len(dates):
            forward = dates[i+1:i+3]
            future_heights = {}
            for f in records[i+1:i+3]:
                for l in f["leaders"]:
                    future_heights[l["ts_code"]] = max(future_heights.get(l["ts_code"], 0), l["height"])
            for theme in record["themes"]:
                pool = [l for l in record["leaders"] if l["theme"] == theme["theme"]]
                outcome, heights = {}, {}
                for l in pool:
                    code = l["ts_code"]
                    vals = [returns.get((d, code), np.nan) for d in forward]
                    if all(np.isfinite(v) for v in vals) and all(d in benchmark for d in forward):
                        outcome[code] = round((np.prod([1+v/100 for v in vals])-np.prod([1+benchmark[d]/100 for d in forward]))*100, 8)
                        heights[code] = future_heights.get(code, 0)
                # Do not choose a winner among only the survivors with data.
                if len(outcome) == len(pool) and pool:
                    label["leaders"][theme["theme"]] = {"winners": winners(outcome), "returns": outcome, "n": len(pool)}
                    label["height_leaders"][theme["theme"]] = winners(heights) if max(heights.values()) > 0 else []
            label["maturity"]["leaders"] = dates[i+2]
        result.append(label)
    write_json(out/"labels.json", result)
    return result


def fit_model(rows, targets, features, minimum):
    if len(rows) < minimum or len(set(targets)) < 2:
        return None
    x = pd.DataFrame(rows).reindex(columns=features).astype(float).replace([np.inf, -np.inf], np.nan)
    model = make_pipeline(SimpleImputer(strategy="constant", fill_value=0, add_indicator=False),
                          StandardScaler(), LogisticRegression(C=.3, class_weight="balanced", max_iter=1000, random_state=17))
    model.fit(x, targets)
    return model


def train(records, labels, cutoff):
    emotion_rows, emotion_y, theme_rows, theme_y, leader_rows, leader_y = [], [], [], [], [], []
    training = {"cutoff": cutoff, "max_feature_date": {}, "max_outcome_date": {}}
    for record, label in zip(records, labels):
        if record["date"] >= cutoff:
            continue
        date = record["date"]
        for task in ["emotion", "mainline", "leaders"]:
            mature = label["maturity"].get(task)
            if mature and mature <= cutoff:
                training["max_feature_date"][task] = date
                training["max_outcome_date"][task] = mature
                if task == "emotion" and label["emotion"] is not None:
                    emotion_rows.append(record["market"]); emotion_y.append(label["emotion"])
                elif task == "mainline":
                    for t in record["themes"]:
                        theme_rows.append(t); theme_y.append(int(t["theme"] in label["mainline"]))
                elif task == "leaders":
                    for l in record["leaders"]:
                        outcome = label["leaders"].get(l["theme"])
                        if outcome and outcome["n"] >= 2:
                            leader_rows.append(l); leader_y.append(int(l["ts_code"] in outcome["winners"]))
    models = [fit_model(emotion_rows, emotion_y, EMOTION_FEATURES, 20),
              fit_model(theme_rows, theme_y, THEME_FEATURES, 80),
              fit_model(leader_rows, leader_y, LEADER_FEATURES, 80)]
    training["sample_counts"] = dict(zip(["emotion", "theme", "leader"], map(len, [emotion_y, theme_y, leader_y])))
    training["coefficients"] = {}
    for name, model, features in zip(["emotion", "theme", "leader"], models, [EMOTION_FEATURES, THEME_FEATURES, LEADER_FEATURES]):
        if model is not None:
            clf = model.steps[-1][1]
            training["coefficients"][name] = {"classes": clf.classes_.tolist(), "features": features,
                                              "standardized_coefficients": clf.coef_.tolist(), "intercept": clf.intercept_.tolist()}
    return models, training


def scores(model, rows, features):
    if not rows:
        return []
    if model is None:
        return [r["rule_score"]/100 for r in rows]
    return model.predict_proba(pd.DataFrame(rows).reindex(columns=features).astype(float))[:, list(model.classes_).index(1)].tolist()


def predict_one(record, models):
    em, tm, lm = models
    emotion = em.predict(pd.DataFrame([record["market"]]).reindex(columns=EMOTION_FEATURES).astype(float))[0] if em is not None else record["market"]["baseline_prediction"]
    ts = scores(tm, record["themes"], THEME_FEATURES)
    ls = scores(lm, record["leaders"], LEADER_FEATURES)
    ranked = sorted(zip(record["themes"], ts), key=lambda p: (-p[1], p[0]["theme"]))
    leaders = {}
    for row, score in zip(record["leaders"], ls):
        leaders.setdefault(row["theme"], []).append({"ts_code": row["ts_code"], "name": row["name"], "score": score})
    for pool in leaders.values():
        pool.sort(key=lambda r: (-r["score"], r["ts_code"]))
    baseline = sorted(record["themes"], key=lambda r: (-r["rule_score"], r["theme"]))
    width_baseline = sorted(record["themes"], key=lambda r: (-r["breadth"], r["theme"]))
    main = ranked[0][0]["theme"] if ranked else None
    market_veto = record["market"]["state"] == "退潮/恐慌"
    return {"date": record["date"], "emotion": str(emotion), "emotion_rule": record["market"]["baseline_prediction"],
            "market_state": record["market"]["state"], "action": "不做/观察" if market_veto or emotion == "cold" else "观察候选，尚未授权交易",
            "mainline": main, "top3": [p[0]["theme"] for p in ranked[:3]],
            "ranked_themes": [{"theme": t["theme"], "score": s, "phase": t["phase"]} for t, s in ranked],
            "leader": leaders[main][0]["ts_code"] if main else None, "leaders_by_theme": leaders,
            "mainline_rule": baseline[0]["theme"] if baseline else None,
            "mainline_breadth_baseline": width_baseline[0]["theme"] if width_baseline else None,
            "score_is_calibrated_probability": False}


def predict(out):
    records = read_json(out/"features.json")
    labels = make_labels(out, records)
    predictions = []
    frozen_models, frozen_training = None, None
    for i, record in enumerate(records):
        cutoff = min(record["date"], FREEZE)
        if cutoff == FREEZE and frozen_models is not None:
            models, training = frozen_models, frozen_training
        else:
            models, training = train(records, labels, cutoff)
            if cutoff == FREEZE:
                frozen_models, frozen_training = models, training
        p = predict_one(record, models)
        p["train_cutoff"] = cutoff
        p["train_max_outcome_date"] = training["max_outcome_date"]
        p["train_sample_counts"] = training["sample_counts"]
        predictions.append(p)
        if i % 20 == 0:
            print(f"predict {i+1}/{len(records)} {record['date']}", flush=True)
    write_json(out/"predictions.json", predictions)
    write_json(out/"frozen_model.json", frozen_training)
    write_json(out/"prediction_manifest.json", {"predictions_sha256": digest(out/"predictions.json"),
                                               "labels_sha256": digest(out/"labels.json"),
                                               "model_sha256": digest(out/"frozen_model.json")})


def proportion(ok, count):
    if not count:
        return {"correct": 0, "n": 0, "accuracy": None, "wilson95": None}
    p, z = ok/count, 1.95996398454
    center = (p+z*z/(2*count))/(1+z*z/count)
    half = z*np.sqrt(p*(1-p)/count+z*z/(4*count*count))/(1+z*z/count)
    return {"correct": ok, "n": count, "accuracy": p, "wilson95": [center-half, center+half]}


def metrics(pairs):
    result = {"days": len(pairs)}
    emotional = [(p, y) for p, y in pairs if y["emotion"] is not None]
    truth, pred = [y["emotion"] for p, y in emotional], [p["emotion"] for p, y in emotional]
    result["emotion"] = proportion(sum(a == b for a, b in zip(truth, pred)), len(truth))
    result["emotion"]["balanced_accuracy"] = balanced_accuracy_score(truth, pred) if truth else None
    result["emotion"]["confusion_labels"] = ["cold", "neutral", "hot"]
    result["emotion"]["confusion_matrix"] = confusion_matrix(truth, pred, labels=["cold", "neutral", "hot"]).tolist() if truth else []
    result["emotion"]["majority_baseline"] = max(Counter(truth).values())/len(truth) if truth else None
    result["emotion_rule"] = proportion(sum(p["emotion_rule"] == y["emotion"] for p, y in emotional), len(emotional))
    main = [(p, y) for p, y in pairs if y["mainline"]]
    for name, field in [("mainline", "mainline"), ("mainline_rule", "mainline_rule"), ("mainline_breadth_baseline", "mainline_breadth_baseline")]:
        result[name] = proportion(sum(p[field] in y["mainline"] for p, y in main), len(main))
    result["mainline_top3"] = proportion(sum(bool(set(p["top3"]) & set(y["mainline"])) for p, y in main), len(main))
    result["oracle_mainline_in_current_universe"] = proportion(sum(any(t in p["leaders_by_theme"] for t in y["mainline"]) for p, y in main), len(main))
    tomorrow = [(p, y) for p, y in pairs if y["mainline_next_day"]]
    result["mainline_next_day"] = proportion(sum(p["mainline"] in y["mainline_next_day"] for p, y in tomorrow), len(tomorrow))
    conditional, singleton, height_correct, height_n, end, false_trade, traded = [], [], 0, 0, [], 0, 0
    for p, y in pairs:
        chosen = y["leaders"].get(p["mainline"])
        if chosen:
            hit = p["leader"] in chosen["winners"]
            (conditional if chosen["n"] >= 2 else singleton).append(hit)
            hl = y["height_leaders"].get(p["mainline"], [])
            if chosen["n"] >= 2 and hl:
                height_correct += p["leader"] in hl; height_n += 1
        if y["mainline"] and "leaders" in y["maturity"]:
            if p["mainline"] not in y["mainline"]:
                end.append(False)
            elif chosen:
                end.append(p["leader"] in chosen["winners"])
        if y["emotion"] is not None and p["action"] != "不做/观察":
            traded += 1; false_trade += y["emotion"] == "cold"
    result["leader_conditional_multi"] = proportion(sum(conditional), len(conditional))
    result["leader_singleton"] = proportion(sum(singleton), len(singleton))
    result["leader_height_secondary"] = proportion(height_correct, height_n)
    result["end_to_end"] = proportion(sum(end), len(end))
    result["activity_coverage"] = traded/max(1, len(emotional))
    result["cold_when_allowed"] = proportion(false_trade, traded)
    return result


def evaluate(out):
    ps, ys = read_json(out/"predictions.json"), read_json(out/"labels.json")
    records = read_json(out/"features.json")
    pairs = list(zip(ps, ys))
    sections = {"development_walk_forward": lambda d: d <= FREEZE,
                "validation": lambda d: "20260501" <= d <= "20260630",
                "test": lambda d: "20260701" <= d <= END}
    summary = {name: metrics([(p, y) for p, y in pairs if test(p["date"])]) for name, test in sections.items()}
    summary["by_month"] = {month: metrics([(p, y) for p, y in pairs if p["date"].startswith(month)]) for month in sorted({p["date"][:6] for p in ps})}
    yl = {y["date"]: y for y in ys}
    pl = {p["date"]: p for p in ps}
    summary["intraday"] = {}
    ids = read_json(out/"intraday_predictions.json")
    for split, test in sections.items():
        summary["intraday"][split] = {}
        for clock in ["09:45", "10:30", "14:00"]:
            selected = [r for r in ids if test(r["feature_date"]) and r["clock"] == clock and yl[r["feature_date"]]["mainline"]]
            covered = [r for r in selected if r["theme"] is not None]
            exact = sum(r["theme"] in yl[r["feature_date"]]["mainline"] for r in covered)
            paired_base = sum(pl[r["feature_date"]]["mainline"] in yl[r["feature_date"]]["mainline"] for r in covered)
            leader_n, leader_ok = 0, 0
            for r in covered:
                ly = yl[r["feature_date"]]["leaders"].get(r["theme"])
                if ly and ly["n"] >= 2:
                    leader_n += 1; leader_ok += r["leader"] in ly["winners"]
            summary["intraday"][split][clock] = {
                "mainline_including_abstention_as_failure": proportion(exact, len(selected)),
                "mainline_conditional_on_coverage": proportion(exact, len(covered)),
                "same_sample_preopen_mainline": proportion(paired_base, len(covered)),
                "coverage": len(covered)/max(1, len(selected)),
                "leader_conditional_multi": proportion(leader_ok, leader_n),
                "label_starts_before_cutoff": True,
                "interpretation": "Current-day confirmation against a horizon starting at open; not an incremental-return forecast."}
    # Error ledger includes every day, not only selected successful anecdotes.
    rows = []
    for p, y, r in zip(ps, ys, records):
        label = y["leaders"].get(p["mainline"])
        rows.append({"date": p["date"], "state": r["market"]["state"], "emotion_pred": p["emotion"], "emotion_label": y["emotion"],
                     "emotion_correct": p["emotion"] == y["emotion"] if y["emotion"] else None,
                     "mainline_pred": p["mainline"], "mainline_label": "|".join(y["mainline"]),
                     "mainline_correct": p["mainline"] in y["mainline"] if y["mainline"] else None,
                     "top3": "|".join(p["top3"]), "leader": p["leader"],
                     "leader_label": "|".join(label["winners"]) if label else None,
                     "leader_correct": p["leader"] in label["winners"] if label else None,
                     "candidate_count": label["n"] if label else None, "action": p["action"]})
    pd.DataFrame(rows).to_csv(out/"daily_ledger.csv", index=False, encoding="utf-8-sig")
    audit = read_json(out/"audit.json")
    summary["audit_totals"] = {field: sum(r[field] for r in audit) for field in ["raw_kpl_up", "raw_kpl_break", "valid_up", "no_eligible_daily", "unknown_breaks", "valid_breaks", "up_price_mismatch", "news_cls_until_18"]}
    summary["target_status"] = "NOT_MET_AND_LABELS_ARE_PROXIES"
    summary["missing_truth"] = ["human_blind_emotion_labels", "human_blind_lifecycle_labels", "full_theme_prelimit_leader_universe", "historical_first_publication_versions", "09:25_auction_snapshots", "causal_defensive_offensive_capital_flow"]
    write_json(out/"metrics.json", summary)
    report(out, summary)
    print(json.dumps({s: {k: summary[s][k] for k in ["days", "emotion", "mainline", "mainline_top3", "leader_conditional_multi", "end_to_end"]} for s in sections}, ensure_ascii=False, indent=2))


def report(out, summary):
    def fmt(m):
        return "无样本" if not m["n"] else f"{100*m['accuracy']:.1f}%（{m['correct']}/{m['n']}；95%区间 {100*m['wilson95'][0]:.1f}–{100*m['wilson95'][1]:.1f}%）"
    lines = ["# A股情绪、题材与龙头研究 v1：真实回放结果", "", "结论：尚未达到95%/95%/100%的目标。以下是预定义代理标签的历史回放，不是实盘盈利率，也不是人工认定的情绪或龙头正确率。", "",
             "采用1—4月滚动开发、5—6月验证、7—8月锁定测试。统计模型冻结于4月30日，只用截至当日已完成标签窗口的样本。", "",
             "| 指标 | 5—6月验证 | 7—8月测试 |", "|---|---|---|"]
    for label, key in [("情绪：次日可做性三分类", "emotion"), ("未来3日主线 top1", "mainline"), ("未来3日主线 top3", "mainline_top3"), ("题材内延续强者（至少2候选）", "leader_conditional_multi"), ("主线+个股整链", "end_to_end"), ("简单涨停宽度基线", "mainline_breadth_baseline")]:
        lines.append(f"| {label} | {fmt(summary['validation'][key])} | {fmt(summary['test'][key])} |")
    lines += ["", "## 每月结果", "", "| 月份 | 可做性三分类 | 主线 top1 | 龙头条件命中 |", "|---|---|---|---|"]
    for month, s in summary["by_month"].items():
        lines.append(f"| {month} | {fmt(s['emotion'])} | {fmt(s['mainline'])} | {fmt(s['leader_conditional_multi'])} |")
    lines += ["", "## 盘中确认（7—8月）", "", "| 时点 | 主线命中（拒判也计入分母） | 同样本盘前主线 | 条件龙头 | 覆盖率 |", "|---|---|---|---|---|"]
    for clock, s in summary["intraday"]["test"].items():
        lines.append(f"| {clock} | {fmt(s['mainline_including_abstention_as_failure'])} | {fmt(s['same_sample_preopen_mainline'])} | {fmt(s['leader_conditional_multi'])} | {s['coverage']:.1%} |")
    lines += ["", "盘中标签从当日开盘起算，因此这里只考核随着信息增加的确认能力，不能称为从该盘中时点起算的预测收益。分钟数据是价格/成交量观察点，不能验证09:25集合竞价、分钟内炸板回封或真实封单变化。", "",
              "## 数据审计", "", "```json", json.dumps(summary["audit_totals"], ensure_ascii=False, indent=2), "```", "",
              "炸板无法明确归因的部分记为未知，题材封板率是已归因子集的估计。缺少合格日线的KPL事件记录保留在audit.json，不静默算作有效样本。主线标签只代表本库可用沪深非ST、10%/20%涨跌停股票。", "",
              "## 复查文件", "", "- daily_ledger.csv：160天逐日预测、真值代理与对错。", "- features.json：市场、生命周期候选、题材/个股原始证据与新闻引用。", "- predictions.json、labels.json：分离的预测与事后标签。", "- frozen_model.json：训练截止、样本数和标准化系数。", "- intraday_predictions.json：候选固定在前日的三个盘中截面。", "- audit.json、manifest.json：排除项、完整性与数据/代码指纹。", "",
              "## 尚未证明的能力", "", "- 当天情绪和生命周期缺少独立人工盲标；规则自解释不能自己证明正确。", "- KPL/新闻均为事后采集快照，没有首发版本；本回放可检验代码时间边界，不能排除数据源历史修订。", "- 新闻自动特征只是关键词命中，尚未验证GPT的产业逻辑判断贡献。", "- 龙头候选仅覆盖已涨停股，未验证全题材成分股中启动前找龙头。", "- 八个月的时间相关样本不足以支持接近必胜的普遍结论。Wilson区间未校正序列相关。", ""]
    (out/"研究结果.md").write_text("\n".join(lines), encoding="utf-8")


def snapshot(out, date, blind=False):
    records = [r for r in read_json(out/"features.json") if r["date"] <= date]
    if not records or records[-1]["date"] != date:
        raise ValueError("Requested as-of date absent")
    target = out/"snapshots"/date
    target.mkdir(parents=True, exist_ok=True)
    packet = {"asof_feature_date": date, "current": records[-1], "past_five_sessions": records[-6:-1],
              "instructions": "Use only this packet; do not read other market data, future labels or aggregate results."}
    if blind:
        excluded = {"score", "rule_score", "state", "phase", "market_score", "baseline_prediction", "interpretation"}
        def facts_only(value):
            if isinstance(value, dict):
                return {k: facts_only(v) for k, v in value.items() if k not in excluded}
            if isinstance(value, list):
                return [facts_only(v) for v in value]
            return value
        packet = facts_only(packet)
        refs = ROOT/"policyStudy/skills/a-share-theme-research/references"
        packet["reference_texts_without_results"] = {name: (refs/name).read_text(encoding="utf-8")
                                                     for name in ["methodology.md", "blind-protocol.md", "feature-dictionary.md", "attribution-and-roles.md"]}
        filename = "blind_decision_packet.json"
    else:
        predictions = [p for p in read_json(out/"predictions.json") if p["date"] <= date]
        packet["prediction"] = predictions[-1]
        filename = "decision_packet.json"
    write_json(target/filename, packet)
    print(target/filename)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["all", "extract", "predict", "evaluate", "snapshot"])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-minutes", action="store_true")
    parser.add_argument("--date")
    parser.add_argument("--blind", action="store_true", help="snapshot: export facts and no-answer references, without baseline judgments")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/"cache").mkdir(exist_ok=True)
    if args.mode in ["all", "extract"]:
        extract(args.output, not args.no_minutes)
    if args.mode in ["all", "predict"]:
        predict(args.output)
    if args.mode in ["all", "evaluate"]:
        evaluate(args.output)
    if args.mode == "snapshot":
        if not args.date:
            parser.error("snapshot requires --date YYYYMMDD")
        snapshot(args.output, args.date, blind=args.blind)


if __name__ == "__main__":
    main()
