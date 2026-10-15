# Source manifest

Hashes below identify the exact public review snapshot. Python modules copied unchanged from the reviewed research source retain the same SHA-256 as the internal run. `test_market_activity_core.py`, `run_tests.py`, documentation and verification code were created for this portable public bundle.

| Relative path | Purpose | Lines | SHA-256 |
|---|---|---:|---|
| `code/early_limitup_overlays.py` | early-seal peer overlays and statistics | 289 | `7f62c35be962130b6156ffa8b0f9d4fc91bea32fec4825600b1b8ed3442ff1aa` |
| `code/early_limitup_returns.py` | audited entry/exit event state machine; private stage dependency omitted | 116 | `20d251f990353df089e217b5a9012fbd307b538c386ba2ca3694ffe0a3c089a1` |
| `code/market_activity_core.py` | turnover statistics and four-phase state machine | 94 | `240e02101e228b0ae139516fde29eb605cb3e16211e3d20c53cf059c4c98b5b5` |
| `code/market_activity_overlay_core.py` | causal label join and per-cell statistics | 192 | `e4dd923231c7eb4ca5879ff47c23c9a036376a7f69aac6aff3621a719dc31854` |
| `code/rebound_state_machine.py` | bounded wait-for-rebound policy state machine | 88 | `c15b97fc111990e71a3446541f00add4bfa98876272fb7eecf125a76333bdb31` |
| `code/research_extra_minute_core.py` | M03/M04/M16/M17 first-trigger logic | 181 | `7d91b31d88d71ead7415fd4e9c1a35c362aacbd41a71181813ed9161b012afd7` |
| `code/research_gpu_rank_core.py` | causal GPU ranking features and labels | 291 | `365a4e5526e08e402bc3c15291628a08b1d74611dd3673031a3994e7083aefb8` |
| `code/research_news_context_helpers.py` | causal news rolling aggregation | 100 | `b9acba51bd1f12d6bb40b7c2c910fa7e0719bed83d726e18b02d3974565abda6` |
| `tests/test_early_limitup_overlays.py` | overlays/statistics tests | 191 | `e3a710cc82ce29f54ce99751b57d238d841fb028d3d4a9f97e228bf6d87b4d2a` |
| `tests/test_extra_minute_core.py` | four minute-mechanism tests; one private-adapter integration explicitly skipped | 227 | `f1a059f704acf988a48087e5e3d8880d0bb397c41f384c101d7a3d8be2115bb9` |
| `tests/test_gpu_rank_core.py` | ranking/label causality tests | 60 | `5eeed22e5f161f5c90eef2c91a03496a1c7b327ce5f44c2105180bdfed9f7cb5` |
| `tests/test_market_activity_core.py` | portable regime formula and lag tests | 45 | `00886dc7a85933230b81d46ed7228c831c492d1bfd7ac8e47e11e993fb42d2f4` |
| `tests/test_market_activity_overlay_core.py` | overlay denominator/as-of/statistics tests | 123 | `857f95843edcccd8bb8b6fd614614c05bab6a27283c6e5702818dd3808ed12ab` |
| `tests/test_news_context_helpers.py` | news rolling-window and missingness tests | 116 | `d8901eca22136f1c45f72fbaeaf6bf3878add5cbe61b192cae6734a99dc7411e` |
| `tests/test_rebound_state_machine.py` | rebound state-machine tests | 64 | `a56cf26e083d6e3c0ec63ce5bcfdd2eb7855f9e2a99430af705506bca7ed090f` |

The top-level `manifest.json` additionally hashes every public file, including `run_tests.py`, `verify_package.py`, this document, requirements and the narrative reports. `verify_package.py` deliberately does not hash `manifest.json` itself; the Git commit SHA is the outer identity for that manifest.

Portable public suite result on Python 3.12: **91 tests run, 90 passed, 1 explicitly skipped, 0 failures**. The skip is the minute integration test requiring the private data-stage adapter.

Production artifact identities referenced in the report:

- Market activity v2 acceptance SHA-256: `1b1e0718cf87839d959b696793037a467dab54ee4590b451a9d8ab36ac219475`.
- Market activity overlay acceptance SHA-256: `3638a4e8500e36039e0402c0854e2686a3f0a298b1d5449972ea12f1c00849c6`.
- Early-limitup acceptance SHA-256: `8b7a69a7567b227a05fbc0cb2717f741ee9a6dd9bf664c1802915d1f17982d75`.
- Original eight minute signal acceptance SHA-256: `59da4f90d9c95fd260f3abad0929cc1b88772963805dc3ffd7e32cf3629f1669`.
- Extra four minute signal acceptance SHA-256: `24d296f69ef45285bc28d282bef1a21802c638f301e651f6f6e1222ddec005b5`.
- GPU ST-corrected package manifest SHA-256: `6fa7487cb906fbe09d7bd86222875bb23b6f2f151ce8a2d3446d2a4e38928ce1`.
- Rebound-stress package manifest SHA-256: `f4becc959848f314d59823ef9c66152824f0130ef91be26c6bc790b2435bbf84`.

No raw artifact is embedded here; these identities are for cross-party comparison when the same licensed data is available.
