"""research_gpu_rank_core.py - mechanical numeric preparation module.

No IO, no network, no permission changes, no order placement.
All research strategy / parameters are decided by the caller.
"""

import json
import time

import numpy as np
import pandas as pd

PRICE_FIELDS = [
    "return1", "mom5", "mom10", "mom20", "mom60", "mom120",
    "rank20", "rank60", "rank120", "volrank", "volatility20",
    "turnover_rate", "volume_ratio", "market_breadth",
]
CONTEXT_FIELDS = [
    "own_flow5", "peer_flow5", "peer_breadth", "peer_active",
    "peer_flow_positive", "peer_limit_rate", "peer_members",
    "theme_count", "known_theme_fraction", "peer_flow_coverage",
    "peer_breadth_coverage",
]
REGIMES = ("supportive", "mixed", "defensive")

_NOT_LISTED = "NOT_LISTED_ON_ACCEPTED_DAY"
_TOL = 0.011
_ENTRY_MULT = 1.003
_EXIT_MULT = 0.997
_NOTIONAL = 100000.0
_COMM = 0.0003
_MIN_COMM = 5.0
_TRANSFER = 0.00001
_SELLTAX = 0.0005

_UNKNOWN = "LABEL_UNKNOWN"


def _safe_div(num, den):
    out = np.full(len(num), np.nan, dtype=np.float64)
    m = (den > 0) & np.isfinite(num) & np.isfinite(den)
    out[m.values if hasattr(m, "values") else m] = (
        np.asarray(num)[np.asarray(m)] / np.asarray(den)[np.asarray(m)]
    )
    return out


def _f32(df, cols):
    return df[cols].astype(np.float32)


def make_features(frame):
    """Return (features DataFrame float32, names_price, names_context)."""
    f = frame
    n = len(f)
    out = {}

    for c in PRICE_FIELDS:
        if c not in f.columns:
            raise ValueError(f"missing price field: {c}")
        out[c] = pd.to_numeric(f[c], errors="coerce").to_numpy(dtype=np.float64)

    adjclose = pd.to_numeric(f["close"], errors="coerce").to_numpy(np.float64) * pd.to_numeric(
        f["adj_factor"], errors="coerce").to_numpy(np.float64)
    adjclose = pd.Series(adjclose, index=f.index)
    vol = pd.to_numeric(f["vol"], errors="coerce")

    def ma(name):
        s = pd.to_numeric(f[name], errors="coerce")
        # ma is on adjusted scale already
        return s

    derived = {
        "adjclose_ma5_ratio": _safe_div(adjclose, ma("ma5")) - 1.0,
        "adjclose_ma10_ratio": _safe_div(adjclose, ma("ma10")) - 1.0,
        "adjclose_ma20_ratio": _safe_div(adjclose, ma("ma20")) - 1.0,
        "adjclose_hi20_ratio": _safe_div(adjclose, pd.to_numeric(f["hi20"], errors="coerce")) - 1.0,
        "adjclose_lo10_ratio": _safe_div(adjclose, pd.to_numeric(f["lo10"], errors="coerce")) - 1.0,
        "vol_vol5_ratio": _safe_div(vol, pd.to_numeric(f["vol5"], errors="coerce")) - 1.0,
        "vol_vol20_ratio": _safe_div(vol, pd.to_numeric(f["vol20"], errors="coerce")) - 1.0,
    }
    for k, v in derived.items():
        out[k] = np.asarray(v, dtype=np.float64)
    names_price = PRICE_FIELDS + list(derived.keys())

    for c in CONTEXT_FIELDS:
        if c not in f.columns:
            raise ValueError(f"missing context field: {c}")
        out[c] = pd.to_numeric(f[c], errors="coerce").to_numpy(dtype=np.float64)
    names_context = list(CONTEXT_FIELDS)

    reg = f["regime"].astype(str) if "regime" in f.columns else pd.Series([""] * n, index=f.index)
    known = reg.isin(REGIMES)
    for r in REGIMES:
        col = f"regime_{r}"
        out[col] = ((reg == r) & known).astype(np.float64).to_numpy()
        names_context.append(col)
    out["regime_unknown_flag"] = (~known).astype(np.float64).to_numpy()
    names_context.append("regime_unknown_flag")

    X = pd.DataFrame(out, index=f.index)
    X = X.replace([np.inf, -np.inf], np.nan)  # inf -> NaN, no fill, no scaling
    return _f32(X, list(X.columns)).replace([np.inf, -np.inf], np.nan), names_price, names_context


def _valid_bar(o, h, l, c, v, ul, dl):
    vals = np.stack([o, h, l, c, v, ul, dl], axis=1)
    ok = np.isfinite(vals).all(axis=1) & (vals > 0).all(axis=1)
    ok &= (l <= o + _TOL) & (o <= h + _TOL) & (l <= c + _TOL) & (c <= h + _TOL)
    ok &= (o >= dl - _TOL) & (o <= ul + _TOL)
    ok &= (ul > dl) & (l <= h)
    return ok


def make_labels(frame):
    """T+1 open buy, T+6 open sell via exact (ts_code, day_index) joins."""
    f = frame
    n = len(f)
    key = pd.MultiIndex.from_arrays([f["ts_code"], f["day_index"]])
    if not key.is_unique:
        raise AssertionError("(ts_code, day_index) must be unique")
    pos = pd.Series(np.arange(n), index=key)

    def take(field, day_off):
        tgt = pd.MultiIndex.from_arrays([f["ts_code"], f["day_index"] + day_off])
        hit = pos.reindex(tgt)
        vals = np.full(n, np.nan)
        m = hit.notna().to_numpy()
        col = pd.to_numeric(f[field], errors="coerce").to_numpy(np.float64)
        vals[m] = col[hit[m].to_numpy(np.int64)]
        st = np.full(n, "", dtype=object)
        st[m] = f["st_status"].to_numpy(object)[hit[m].to_numpy(np.int64)] \
            if "st_status" in f.columns else ""
        return vals, m, hit, st

    raw = ["open", "high", "low", "close", "vol", "up_limit", "down_limit", "adj_factor"]
    e = {c: take(c, 1) for c in raw}
    x = {c: take(c, 6) for c in raw}
    e_st = take("open", 1)[3]
    entry_date = take("open", 1)[2]
    exit_date = take("open", 6)[2]

    trade_dates = f["trade_date"]
    e_date = np.full(n, pd.NaT, dtype=object)
    x_date = np.full(n, pd.NaT, dtype=object)
    em = entry_date.notna().to_numpy()
    xm = exit_date.notna().to_numpy()
    e_date[em] = trade_dates.to_numpy()[entry_date[em].to_numpy(np.int64)]
    x_date[xm] = trade_dates.to_numpy()[exit_date[xm].to_numpy(np.int64)]
    e_idx = np.where(em, f.day_index.to_numpy(np.int64) + 1, -1)
    x_idx = np.where(xm, f.day_index.to_numpy(np.int64) + 6, -1)

    status = np.full(n, _UNKNOWN, dtype=object)
    e_ok = _valid_bar(*[e[c][0] for c in ["open", "high", "low", "close", "vol", "up_limit", "down_limit"]])
    x_ok = _valid_bar(*[x[c][0] for c in ["open", "high", "low", "close", "vol", "up_limit", "down_limit"]])

    e_open, e_ul = e["open"][0], e["up_limit"][0]
    x_open, x_dl = x["open"][0], x["down_limit"][0]
    entry_fill = e_open * _ENTRY_MULT
    exit_fill = x_open * _EXIT_MULT

    status[~em | ~xm] = "missing endpoints"
    pend = status == _UNKNOWN
    st_mask = pend & em & (e_st != _NOT_LISTED)
    status[st_mask] = "UNKNOWN_ST"
    status[st_mask & (e_st == "ST_LISTED")] = "ST_LISTED"
    for side, valid in ((e, e_ok), (x, x_ok)):
        factor = side['adj_factor'][0]
        valid &= np.isfinite(factor) & (factor > 0)
    bad_e = pend & ~st_mask & ~e_ok
    status[bad_e] = "invalidbar"
    bad_x = pend & ~st_mask & e_ok & ~x_ok
    status[bad_x] = "invalidbar"
    base = pend & ~st_mask & e_ok & x_ok
    status[base & ~(entry_fill < e_ul - 0.005)] = "entryupperblocked"
    done_e = base & (entry_fill < e_ul - 0.005)
    status[done_e & ~(exit_fill > x_dl + 0.005)] = "exitlowerblocked"
    ok = done_e & (exit_fill > x_dl + 0.005)
    status[ok] = "COMPLETED_PROXY"

    net = np.full(n, np.nan)
    if ok.any():
        shares = _NOTIONAL / entry_fill[ok]
        proceeds = shares * (x["adj_factor"][0][ok] / e["adj_factor"][0][ok]) * exit_fill[ok]
        buyfee = np.maximum(_MIN_COMM, _NOTIONAL * _COMM) + _NOTIONAL * _TRANSFER
        sellfee = np.maximum(_MIN_COMM, proceeds * _COMM) + proceeds * (_TRANSFER + _SELLTAX)
        net[ok] = (proceeds - sellfee - _NOTIONAL - buyfee) / (_NOTIONAL + buyfee)

    win = np.full(n, np.nan)
    win[ok] = (net[ok] > 0).astype(np.float64)

    res = pd.DataFrame({
        "label_status": status,
        "net_return": net,
        "win": win,
        "entry_date": e_date,
        "exit_date": x_date,
        "entry_index": e_idx,
        "exit_index": x_idx,
    }, index=f.index)
    assert len(res) == n
    return res


class _CheckCallback:
    def __init__(self, check_fn):
        self.check_fn = check_fn

    def __call__(self):
        import xgboost as xgb

        outer = self

        class Cb(xgb.callback.TrainingCallback):
            def after_iteration(self, model, epoch, evals_log):
                if epoch % 20 == 0:
                    outer.check_fn()
                return False

        return Cb()


def train_gpu(X, y, train_mask, objective, depth, check_fn):
    if objective not in ("reg:squarederror", "binary:logistic"):
        raise ValueError("objective must be reg:squarederror or binary:logistic")
    if type(depth) is not int or depth not in (3, 5):
        raise ValueError("depth must be 3 or 5")
    mask = np.asarray(train_mask)
    if mask.ndim != 1 or mask.dtype != bool or len(mask) != len(X):
        raise ValueError("train_mask must be bool array with len == len(X)")
    yv = np.asarray(y, dtype=np.float64)
    if len(yv) != len(X):
        raise ValueError("y length mismatch")
    if mask.sum() < 100:
        raise ValueError("need at least 100 training rows")
    yt = yv[mask]
    if not np.isfinite(yt).all():
        raise ValueError("y[train_mask] must be all finite")
    if objective == "binary:logistic" and set(np.unique(yt)) != {0., 1.}:
        raise ValueError("classifier requires both classes present")

    import xgboost as xgb

    params = {
        "device": "cuda",
        "tree_method": "hist",
        "nthread": 8,
        "max_bin": 128,
        "max_depth": depth,
        "eta": 0.05,
        "seed": 20260920,
        "subsample": 1.0,
        "colsample_bytree": 1.0,
        "lambda": 10.0,
        "min_child_weight": 50.0,
        "objective": objective,
    }
    check_fn()
    Xt = X.iloc[mask]
    dtrain = xgb.QuantileDMatrix(Xt.to_numpy(np.float32), label=yt, max_bin=128)

    check_fn()
    t0 = time.time()
    booster = xgb.train(
        params,
        dtrain,
        num_boost_round=300,
        callbacks=[_CheckCallback(check_fn)()],
        verbose_eval=False,
    )
    elapsed = time.time() - t0
    check_fn()

    cfg = json.loads(booster.save_config())
    dev = str(cfg["learner"]["generic_param"]["device"])
    if not dev.startswith("cuda"):
        raise RuntimeError(f"XGBoost did not train on CUDA device: {dev}")

    import cupy as cp
    device_x = cp.asarray(X.to_numpy(np.float32))
    prediction = cp.asnumpy(booster.inplace_predict(device_x))
    del device_x
    cp.get_default_memory_pool().free_all_blocks()
    metadata = {
        "model_params": params,
        "num_boost_round": 300,
        "training_rows": int(mask.sum()),
        "elapsed_seconds": elapsed,
        "device": dev,
    }
    return booster, prediction, metadata
