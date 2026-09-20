"""Mechanical news-context aggregation helpers. No IO/network/finance modules."""
import numpy as np
import pandas as pd

_CERT = "OBSERVED_COLLECTION_UNCERTIFIED"
_WINDOWS = (3, 10, 20)


def aggregate_news(articles: pd.DataFrame, themes: list[str],
                   days: list[str], coverage: pd.DataFrame) -> pd.DataFrame:
    """Build theme x day grid of observed-only news metrics.

    Rolling densities are OBSERVED-density only; missing days are never
    treated as confirmed zero news. observed_unique_n sums DAILY unique counts,
    not unique events over the entire window. Cross-day repetition is retained.
    Kimi draft's global hash dedup was rejected: a future lower-ID article could
    erase an earlier novelty value. Dedup is strictly within theme/session now.
    """
    days = list(days)  # exact session days, already chronological
    if days != sorted(set(days)) or len(themes) != len(set(themes)):
        raise ValueError("duplicate_or_unsorted_grid")
    day_index = {d: i for i, d in enumerate(days)}

    cov = coverage.copy()
    if cov["trade_date"].duplicated().any():
        raise ValueError("coverage trade_date not unique")
    cov_map = cov.set_index("trade_date")["coverage_state"]
    states = [cov_map.get(d, "UNKNOWN") for d in days]

    a = articles.copy()
    if len(a):
        dup = a.duplicated(subset=["article_id", "theme_code"])
        if dup.any():
            raise ValueError("duplicate (article_id, theme_code)")
        a = a[a["available_date"].isin(day_index)]

    # per (theme, day) daily aggregates
    records = {}
    if len(a):
        for (th, d), g in a.groupby(["theme_code", "available_date"]):
            records[(th, d)] = {
                "observed_news_count": len(g),
                "unique_news_count": g["text_sha256"].nunique(),
                "sources_width": g["source"].nunique(),
                "repeat_fraction": float((g["repeat_similarity"] >= 0.85).mean()),
                "sources": set(g["source"]),
            }

    rows = []
    for th in sorted(themes):
        consec = 0
        day_stats = []
        for d in days:
            r = records.get((th, d), {
                "observed_news_count": 0, "unique_news_count": 0,
                "sources_width": 0, "repeat_fraction": np.nan, "sources": set(),
            })
            day_stats.append(r)
        # novel facts are theme-specific via representative rows in this theme
        if len(a):
            ath = a[a["theme_code"] == th]
            rep_th = ath.sort_values("article_id").drop_duplicates(["available_date", "text_sha256"])
            facts_by_day = rep_th.groupby("available_date")["novel_fact_count"].sum()
        else:
            facts_by_day = pd.Series(dtype=float)
        novel = [float(facts_by_day.get(d, 0.0)) for d in days]

        uniq = np.array([s["unique_news_count"] for s in day_stats], dtype=float)
        srcs = [s["sources"] for s in day_stats]
        cert = np.array([st == _CERT for st in states])

        for i, d in enumerate(days):
            s = day_stats[i]
            if s["unique_news_count"] > 0:
                consec += 1
            else:
                consec = 0
            row = {
                "theme_code": th,
                "available_date": d,
                "observed_news_count": s["observed_news_count"],
                "unique_news_count": s["unique_news_count"],
                "sources_width": s["sources_width"],
                "repeat_fraction": s["repeat_fraction"],
                "novel_fact_count": novel[i],
                "coverage_state": states[i],
                "consecutive_observed_news_sessions": consec,
            }
            for n in _WINDOWS:
                lo = max(0, i - n + 1)
                win = slice(lo, i + 1)
                row[f"observed_unique_{n}"] = float(uniq[win].sum())
                row[f"observed_sources_{n}"] = len(set().union(*srcs[win])) if i >= lo else 0
                row[f"observed_novel_fact_{n}"] = float(np.sum(novel[win]))
                row[f"history_sessions_{n}"] = i - lo + 1
                row[f"covered_sessions_{n}"] = int(cert[win].sum())
                ok = (row[f"history_sessions_{n}"] == n) and bool(cert[win].all())
                row[f"density_{n}"] = (float(uniq[win].sum()) / n) if ok else np.nan
            rows.append(row)
    return pd.DataFrame(rows)
