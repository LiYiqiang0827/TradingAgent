# Code scope

These are exact SHA-256-addressed snapshots of the pure calculation modules used in the 2026-09-20 study. They contain no data, secrets, local paths, network access or order execution.

`early_limitup_returns.py` is retained for line-by-line audit of the entry/exit state machine, but imports the larger private `research_exit_policy` stage module, which is deliberately not published because it includes local orchestration and data-layout coupling. In this public bundle, importing that file directly will fail unless the reviewer supplies a compatible `research_exit_policy`; it is for reading, not standalone execution. Its formula and cost behavior were independently replayed in the original environment. The public test suite therefore tests `early_limitup_overlays.py` but does not import `early_limitup_returns.py`.

One integration test in `test_extra_minute_core.py` is skipped because it imports the private stage adapter; all pure-core tests remain active. The test runner reports the skip explicitly.
