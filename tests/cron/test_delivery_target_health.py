from cron.scheduler import _is_stale_discord_target_error, _record_delivery_target_result


def test_unknown_channel_errors_are_classified_as_stale_discord_targets():
    assert _is_stale_discord_target_error("404 Not Found (error code: 10003): Unknown Channel")
    assert not _is_stale_discord_target_error("403 Forbidden (error code: 50013): Missing Permissions")


def test_record_delivery_target_marks_and_clears_discord_stale(monkeypatch):
    calls = []

    def mark(platform, chat_id, **kwargs):
        calls.append(("mark", platform, chat_id, kwargs))

    def clear(platform, chat_id, **kwargs):
        calls.append(("clear", platform, chat_id, kwargs))

    monkeypatch.setattr("gateway.status.mark_stale_delivery_target", mark)
    monkeypatch.setattr("gateway.status.clear_stale_delivery_target", clear)

    _record_delivery_target_result(
        "discord",
        "123",
        "456",
        "404 Not Found (error code: 10003): Unknown Channel",
        job_id="job1",
    )
    _record_delivery_target_result("discord", "123", "456", None, job_id="job1")
    _record_delivery_target_result("telegram", "123", None, "Unknown Channel", job_id="job1")

    assert calls == [
        (
            "mark",
            "discord",
            "123",
            {
                "thread_id": "456",
                "error_code": "unknown_channel",
                "error_message": "404 Not Found (error code: 10003): Unknown Channel",
                "job_id": "job1",
            },
        ),
        ("clear", "discord", "123", {"thread_id": "456"}),
    ]
