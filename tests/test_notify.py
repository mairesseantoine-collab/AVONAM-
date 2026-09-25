import common.notify as notify


def test_disabled_when_not_configured(monkeypatch):
    for k in ("ALERT_SMTP_HOST", "ALERT_SMTP_USER", "ALERT_SMTP_PASSWORD", "ALERT_EMAIL_TO"):
        monkeypatch.delenv(k, raising=False)
    assert notify.is_configured() is False
    ok, reason = notify.send_email("test", "corps")
    assert ok is False
    assert "non configur" in reason


def test_configured_detection(monkeypatch):
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ALERT_SMTP_USER", "me@example.com")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("ALERT_EMAIL_TO", "me@example.com")
    assert notify.is_configured() is True


def test_send_failure_is_caught(monkeypatch):
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.invalid.local")
    monkeypatch.setenv("ALERT_SMTP_USER", "me@example.com")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("ALERT_EMAIL_TO", "me@example.com")
    monkeypatch.setenv("ALERT_SMTP_PORT", "2525")
    # Le serveur n'existe pas : send_email doit retourner False sans lever.
    ok, reason = notify.send_email("test", "corps")
    assert ok is False
    assert "échec" in reason
