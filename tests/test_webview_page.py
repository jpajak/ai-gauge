from aigauge.webview.page import _safe_source_id


def test_safe_source_id_drops_query_and_fragment():
    source = "https://accounts.google.com/v3/signin?login_hint=person@example.com#frag"

    assert _safe_source_id(source) == "https://accounts.google.com/v3/signin"
