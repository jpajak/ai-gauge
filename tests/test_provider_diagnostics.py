import logging

from usage_view.providers.diagnostics import log_page_diagnosis, page_diagnosis


def test_page_diagnosis_summarizes_expected_rows_without_body_text():
    diagnosis = page_diagnosis(
        {
            "logged_out": False,
            "session": None,
            "weekly_all": {"percent": 12},
            "title": "Claude",
            "url": "https://claude.ai/api/challenge_redirect?token=secret#frag",
            "body_text": "Can't reach Claude. Check your connection. secret body value",
        },
        ("session", "weekly_all"),
    )

    assert diagnosis["url"] == "https://claude.ai/api/challenge_redirect"
    assert diagnosis["url_has_query"] is True
    assert diagnosis["url_has_fragment"] is True
    assert diagnosis["rows"] == {"session": False, "weekly_all": True}
    assert diagnosis["has_connectivity_text"] is True
    assert "secret body value" not in repr(diagnosis)
    assert "token=secret" not in repr(diagnosis)


def test_log_page_diagnosis_omits_raw_body_and_query(caplog):
    logger = logging.getLogger("tests.provider_diagnostics")
    caplog.set_level(logging.INFO, logger=logger.name)

    log_page_diagnosis(
        logger,
        provider="claude",
        classification="layout_changed",
        payload={
            "logged_out": False,
            "session": None,
            "weekly_all": None,
            "title": "Claude",
            "url": "https://claude.ai/settings/usage?token=secret",
            "body_text": "Plan usage limits Current session 15% used secret body value",
        },
        expected_rows=("session", "weekly_all"),
    )

    assert "classification=layout_changed" in caplog.text
    assert "rows=session=0,weekly_all=0" in caplog.text
    assert "flags=percent=1,usage=1,security=0,connectivity=0" in caplog.text
    assert "url_query=True" in caplog.text
    assert "token=secret" not in caplog.text
    assert "secret body value" not in caplog.text
