from scheduler import scheduler_updateData as scheduler


def test_full_update_refreshes_trade_calendar_before_reading_target(monkeypatch):
    calls = []

    def fake_spawn(service_name, extra_args=None, sync=False, abort_on_failure=True):
        calls.append((service_name, extra_args, sync))
        return 0

    def fake_target_date():
        assert calls and calls[0][0] == "service_tradecal"
        return "19990101"

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(scheduler, "spawn_service", fake_spawn)
    monkeypatch.setattr(scheduler, "get_target_date", fake_target_date)
    monkeypatch.setattr(scheduler, "get_conn", lambda _name: FakeConn())
    monkeypatch.setattr(scheduler, "get_ctrl", lambda _conn, _key: "19990101")

    scheduler.task_full_update()

    assert calls == [
        (
            "service_tradecal",
            ["--trade-date", scheduler.datetime.now().strftime("%Y%m%d")],
            True,
        )
    ]
