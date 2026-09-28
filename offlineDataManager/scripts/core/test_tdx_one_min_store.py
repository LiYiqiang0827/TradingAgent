from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from core.one_min_store import get_one_min
from core.tdx_one_min_store import (
    finalize_tdx_one_min_day,
    normalize_existing_one_min_partitions,
    remove_one_min_partition,
    stage_tdx_one_min_batch,
)


def _rows(code: str, day: str = "20260924") -> list[dict]:
    dashed = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    return [
        {
            "ts_code": code, "trade_date": day,
            "datetime": f"{dashed} {stamp}:00", "time_idx": idx,
            "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1,
            "vol": 1000.0, "amount": 10000.0,
        }
        for stamp, idx in [("09:31", 0), ("15:00", 239)]
    ]


def _full_rows(code: str, day: str = "20260924") -> list[dict]:
    dashed = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    stamps = list(pd.date_range(f"{dashed} 09:31", f"{dashed} 11:30", freq="min"))
    stamps += list(pd.date_range(f"{dashed} 13:01", f"{dashed} 15:00", freq="min"))
    return [
        {
            "ts_code": code, "trade_date": day, "datetime": stamp,
            "time_idx": idx, "open": 10.0, "high": 10.2, "low": 9.9,
            "close": 10.1, "vol": 1000.0, "amount": 10000.0,
        }
        for idx, stamp in enumerate(stamps)
    ]


def test_stage_and_finalize_tdx_partition(tmp_path: Path, monkeypatch) -> None:
    from core import offline_db_client
    from core import tdx_one_min_store

    stage = tmp_path / "stage"
    store = tmp_path / "store"
    catalog = tmp_path / "catalog.duckdb"
    rows = _rows("000001.SZ") + _rows("600000.SH")
    staged = stage_tdx_one_min_batch([{"rows": rows}], stage)
    assert staged["rows"] == 4
    monkeypatch.setattr(
        offline_db_client, "get_basic",
        lambda *_args, **_kwargs: pd.DataFrame({
            "ts_code": ["000001.SZ", "600000.SH"],
            "name": ["平安银行", "浦发银行"],
        }),
    )
    monkeypatch.setattr(
        offline_db_client, "get_day",
        lambda *_args, **_kwargs: pd.DataFrame({
            "ts_code": ["000001.SZ", "600000.SH"],
            "trade_date": ["20260924", "20260924"],
        }),
    )
    monkeypatch.setattr(
        offline_db_client, "get_adj_factor",
        lambda *_args, **_kwargs: pd.DataFrame({
            "ts_code": ["000001.SZ", "600000.SH"],
            "trade_date": ["20260924", "20260924"],
            "adj_factor": [2.0, 3.0],
        }),
    )
    monkeypatch.setattr(
        tdx_one_min_store, "_latest_adj_factor_reference",
        lambda *_args, **_kwargs: pd.DataFrame({
            "ts_code": ["000001.SZ", "600000.SH"],
            "trade_date": ["20260924", "20260924"],
            "adj_factor": [2.0, 3.0],
        }),
    )
    result = finalize_tdx_one_min_day(
        "20260924", stage, root=store, catalog_path=catalog,
        source_host="test.example:7709",
    )
    assert result["rows"] == 4
    frame = get_one_min(trade_date="20260924", root=store)
    assert frame.groupby("ts_code")["time_idx"].apply(list).tolist() == [[0, 239], [0, 239]]
    assert set(frame["adj_factor"]) == {2.0, 3.0}


def test_normalize_existing_partition_removes_0930_and_reindexes(tmp_path: Path) -> None:
    store = tmp_path / "store"
    catalog = tmp_path / "catalog.duckdb"
    day = "20250102"
    target_dir = store / "year=2025" / "month=01" / f"trade_date={day}"
    target_dir.mkdir(parents=True)
    target = target_dir / "part-000.parquet"
    records = []
    for code in ["000001.SZ", "600000.SH"]:
        for stamp, idx in [("09:30", 0), ("09:31", 1), ("13:01", 121), ("15:00", 240)]:
            records.append({
                "ts_code": code, "name": code, "trade_date": int(day),
                "datetime": pd.Timestamp(f"2025-01-02 {stamp}:00"), "time_idx": idx,
                "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1,
                "vol": 1000, "amount": 10000.0, "adj_factor": 2.0,
            })
    pq.write_table(pa.Table.from_pandas(pd.DataFrame(records), preserve_index=False), target)
    manifest_dir = store / "_manifest"
    manifest_dir.mkdir()
    (manifest_dir / f"trade_date={day}.json").write_text(
        json.dumps({"trade_date": day, "target_file": str(target)}), encoding="utf-8"
    )
    result = normalize_existing_one_min_partitions(
        end_date=day, root=store, catalog_path=catalog,
    )
    assert result[0]["old_rows"] == 8
    frame = get_one_min(trade_date=day, root=store)
    assert len(frame) == 6
    assert frame.groupby("ts_code")["time_idx"].apply(list).tolist() == [
        [0, 120, 239], [0, 120, 239],
    ]


def test_finalize_reindexes_legacy_csv_fallback(tmp_path: Path, monkeypatch) -> None:
    from core import offline_db_client
    from core import tdx_one_min_store

    stage = tmp_path / "stage"
    store = tmp_path / "store"
    catalog = tmp_path / "catalog.duckdb"
    day = "20260924"
    stage_tdx_one_min_batch([{"rows": _full_rows("000001.SZ", day)}], stage)

    legacy = []
    stamps = [pd.Timestamp("2026-09-24 09:30")]
    stamps += [row["datetime"] for row in _full_rows("600000.SH", day)]
    for idx, stamp in enumerate(stamps):
        legacy.append({
            "ts_code": "600000.SH", "name": "浦发银行", "trade_date": int(day),
            "datetime": stamp, "time_idx": idx, "open": 10.0, "high": 10.2,
            "low": 9.9, "close": 10.1, "vol": 1000, "amount": 10000.0,
            "adj_factor": 3.0,
        })
    target_dir = store / "year=2026" / "month=09" / f"trade_date={day}"
    target_dir.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pandas(pd.DataFrame(legacy), preserve_index=False),
        target_dir / "part-000.parquet",
    )
    monkeypatch.setattr(
        offline_db_client, "get_basic",
        lambda *_args, **_kwargs: pd.DataFrame({
            "ts_code": ["000001.SZ", "600000.SH"], "name": ["平安银行", "浦发银行"],
        }),
    )
    monkeypatch.setattr(
        offline_db_client, "get_day",
        lambda *_args, **_kwargs: pd.DataFrame({
            "ts_code": ["000001.SZ", "600000.SH"], "trade_date": [day, day],
        }),
    )
    factors = pd.DataFrame({
        "ts_code": ["000001.SZ", "600000.SH"], "trade_date": [day, day],
        "adj_factor": [2.0, 3.0],
    })
    monkeypatch.setattr(offline_db_client, "get_adj_factor", lambda *_a, **_k: factors)
    monkeypatch.setattr(tdx_one_min_store, "_latest_adj_factor_reference", lambda *_a, **_k: factors)

    result = finalize_tdx_one_min_day(
        day, stage, root=store, catalog_path=catalog, source_host="test.example:7709",
    )
    frame = get_one_min(trade_date=day, root=store)
    assert result["data_source"] == "tdx_with_csv_fallback"
    assert result["csv_fallback_rows"] == 240
    assert len(frame) == 480
    assert frame.groupby("ts_code")["time_idx"].apply(list).tolist() == [
        list(range(240)), list(range(240)),
    ]


def test_remove_one_min_partition_cleans_files_manifest_and_catalog(tmp_path: Path) -> None:
    from core.one_min_store import _open_catalog

    store = tmp_path / "store"
    catalog = tmp_path / "catalog.duckdb"
    day = "20260922"
    target_dir = store / "year=2026" / "month=09" / f"trade_date={day}"
    target_dir.mkdir(parents=True)
    (target_dir / "part-000.parquet").write_bytes(b"stale")
    manifest = store / "_manifest" / f"trade_date={day}.json"
    manifest.parent.mkdir()
    manifest.write_text("{}", encoding="utf-8")
    with _open_catalog(catalog) as conn:
        conn.execute("""
            INSERT INTO one_min_ingest_catalog (
                trade_date, source_path, source_size, source_mtime_ns,
                source_present, status, discovered_at, last_checked_at
            ) VALUES (?, ?, 0, 0, TRUE, 'imported', current_timestamp, current_timestamp)
        """, [day, "legacy.csv"])

    assert remove_one_min_partition(day, root=store, catalog_path=catalog)
    assert not target_dir.exists()
    assert not manifest.exists()
    with _open_catalog(catalog) as conn:
        assert conn.execute(
            "SELECT count(*) FROM one_min_ingest_catalog WHERE trade_date=?", [day]
        ).fetchone()[0] == 0
