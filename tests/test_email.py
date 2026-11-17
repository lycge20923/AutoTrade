from __future__ import annotations

import smtplib

import pytest

from autotrade.notify.email import EmailError, EmailSender, sender_from_settings


class FakeSMTP:
    instances: list[FakeSMTP] = []

    def __init__(self, host, port, context=None, timeout=None, fail_login=False):
        self.host, self.port = host, port
        self.logins: list[tuple[str, str]] = []
        self.messages = []
        self.fail_login = fail_login
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if self.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        self.logins.append((user, password))

    def send_message(self, msg):
        self.messages.append(msg)


@pytest.fixture(autouse=True)
def _reset():
    FakeSMTP.instances.clear()


def test_send_builds_text_and_html(make_settings) -> None:
    settings = make_settings("GMAIL_ADDRESS=me@gmail.com\nGMAIL_APP_PASSWORD=abcd efgh ijkl mnop\n")
    sender = sender_from_settings(settings)
    sender.smtp_factory = FakeSMTP
    sender.send("you@example.com", "主旨", "純文字", "<b>HTML</b>")

    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.gmail.com", 465)
    assert smtp.logins == [("me@gmail.com", "abcdefghijklmnop")]  # 空格已移除
    msg = smtp.messages[0]
    assert msg["To"] == "you@example.com" and msg["Subject"] == "主旨"
    assert {p.get_content_type() for p in msg.iter_parts()} == {"text/plain", "text/html"}


def test_auth_error_has_friendly_message() -> None:
    sender = EmailSender(
        "smtp.gmail.com", 465, "me@gmail.com", "x",
        smtp_factory=lambda *a, **k: FakeSMTP(*a, fail_login=True, **k),
    )
    with pytest.raises(EmailError, match="應用程式密碼"):
        sender.send("me@gmail.com", "s", "t")


def test_network_error_is_wrapped() -> None:
    def boom(*a, **k):
        raise OSError("Network is unreachable")

    with pytest.raises(EmailError, match="寄信失敗"):
        EmailSender("h", 1, "u", "p", smtp_factory=boom).send("x", "s", "t")


def test_missing_credentials(make_settings) -> None:
    with pytest.raises(EmailError, match="GMAIL_ADDRESS"):
        sender_from_settings(make_settings(""))


def test_recipient_defaults_to_sender(make_settings) -> None:
    assert make_settings("GMAIL_ADDRESS=me@gmail.com\n").email_recipient == "me@gmail.com"
    s = make_settings("GMAIL_ADDRESS=me@gmail.com\nNOTIFY_TO=other@example.com\n")
    assert s.email_recipient == "other@example.com"
