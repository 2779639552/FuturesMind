"""Tests for the multi-platform daily pipeline in scheduler.py (2026-08-25).

The daily pipeline now collects from weibo → xueqiu → zhihu → eastmoney_guba in
sequence instead of a single platform, and one platform's failure (e.g. missing
cookie) must be logged and skipped without stopping the rest.

No subprocess is spawned and no database is touched — `get_db`,
`_run_platform_collection` and `subprocess.run` are all patched.
"""

from unittest.mock import MagicMock, patch

import pytest

from scheduler import _run_daily_pipeline

_PLATFORMS = ("weibo", "xueqiu", "zhihu", "eastmoney_guba")


def _run(platform_callable):
    mock_db = MagicMock()
    mock_run = MagicMock(side_effect=platform_callable)
    with patch("scheduler.get_db", return_value=mock_db), patch(
        "scheduler._run_platform_collection", mock_run
    ), patch("scheduler.subprocess.run", return_value=MagicMock()):
        _run_daily_pipeline()
    return mock_db, mock_run


def test_pipeline_collects_all_four_platforms_in_order():
    mock_db, mock_run = _run(lambda platform, **kwargs: None)

    assert mock_run.call_count == 4
    ordered = [c.args[0] for c in mock_run.call_args_list]
    assert ordered == list(_PLATFORMS)
    for c in mock_run.call_args_list:
        assert c.kwargs.get("per_kw") == 15
        assert c.kwargs.get("since_days") == 7

    # pipeline_started + pipeline_complete both logged (aggregate step succeeded)
    alerts = [a.args[0] for a in mock_db.create_alert.call_args_list]
    assert "pipeline_started" in alerts
    assert "pipeline_complete" in alerts


def test_platform_failure_does_not_stop_pipeline():
    def flaky(platform, **kwargs):
        if platform == "xueqiu":
            raise RuntimeError("cookie expired")

    mock_db, mock_run = _run(flaky)

    # all 4 platforms attempted despite xueqiu raising
    assert mock_run.call_count == 4

    # a collection_error alert was written for the failed platform
    error_calls = [
        c for c in mock_db.create_alert.call_args_list if c.args[0] == "collection_error"
    ]
    assert len(error_calls) == 1
    assert error_calls[0].args[1] == "xueqiu pipeline error"
    assert error_calls[0].kwargs.get("severity") == "error"

    # the pipeline still ran the aggregate + completed alert
    alerts = [a.args[0] for a in mock_db.create_alert.call_args_list]
    assert "pipeline_complete" in alerts


def test_aggregate_failure_logs_pipeline_error():
    # If the aggregate/backtest/generate subprocess fails, the pipeline must log
    # pipeline_error (not pipeline_complete) without crashing the scheduler.
    mock_db = MagicMock()
    with patch("scheduler.get_db", return_value=mock_db), patch(
        "scheduler._run_platform_collection", return_value=None
    ), patch("scheduler.subprocess.run", side_effect=TimeoutError("aggregate slow")):
        _run_daily_pipeline()
    alert_types = [a.args[0] for a in mock_db.create_alert.call_args_list]
    assert "pipeline_error" in alert_types
    assert "pipeline_complete" not in alert_types
