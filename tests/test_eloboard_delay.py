import importlib


def _reload(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("ELOBOARD_DELAY", raising=False)
    else:
        monkeypatch.setenv("ELOBOARD_DELAY", value)
    import collectors.eloboard as mod
    return importlib.reload(mod)


def test_delay_never_below_two_seconds(monkeypatch):
    assert _reload(monkeypatch, "0.5").DEFAULT_DELAY == 2.0
    assert _reload(monkeypatch, "0").DEFAULT_DELAY == 2.0
    assert _reload(monkeypatch, "abc").DEFAULT_DELAY == 2.0
    assert _reload(monkeypatch, None).DEFAULT_DELAY == 2.0


def test_delay_can_be_raised(monkeypatch):
    assert _reload(monkeypatch, "3.5").DEFAULT_DELAY == 3.5
