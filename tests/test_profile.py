from aigauge.webview import profile as profile_module


class FakeSignal:
    def connect(self, slot):
        pass


class FakeCookieStore:
    cookieAdded = FakeSignal()
    cookieRemoved = FakeSignal()

    def loadAllCookies(self):
        pass


class FakeProfile:
    PersistentCookiesPolicy = profile_module.QWebEngineProfile.PersistentCookiesPolicy

    def __init__(self, name):
        self.name = name
        self.calls = []

    def cookieStore(self):
        return FakeCookieStore()

    def __getattr__(self, attr):
        if attr.startswith("set"):
            return lambda *args: self.calls.append((attr, args))
        raise AttributeError(attr)


def test_get_profile_requests_english_pages(monkeypatch):
    monkeypatch.setattr(profile_module, "QWebEngineProfile", FakeProfile)
    monkeypatch.setattr(profile_module, "_profiles", {})

    profile = profile_module.get_profile("codex")

    languages = [args[0] for attr, args in profile.calls if attr == "setHttpAcceptLanguage"]
    assert len(languages) == 1
    assert languages[0].startswith("en-US")
    assert profile_module.get_profile("codex") is profile
