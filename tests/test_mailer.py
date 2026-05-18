"""Privacy test: recipients must go in Bcc, not To, so they don't see each other."""
from __future__ import annotations

from dataclasses import dataclass
from email import message_from_string
from unittest.mock import MagicMock, patch

import pytest

from hexa_agent.mailer import MailerError, send_email


@dataclass
class _Cfg:
    smtp_host: str = "smtp.example.com"
    smtp_port: int = 587
    smtp_use_tls: bool = True
    smtp_username: str = "bot@example.com"
    smtp_password: str = "secret"
    mail_from: str = "HEXA Bot <bot@example.com>"
    mail_to: list = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.mail_to is None:
            self.mail_to = ["one@example.com", "two@example.com"]


@patch("hexa_agent.mailer.smtplib.SMTP")
def test_recipients_go_into_bcc_not_to(MockSMTP):
    smtp_instance = MagicMock()
    MockSMTP.return_value.__enter__.return_value = smtp_instance

    cfg = _Cfg()
    send_email(
        cfg,
        subject="hi",
        text_body="hello",
        html_body="<p>hello</p>",
    )

    # send_message should have been called once with the EmailMessage.
    assert smtp_instance.send_message.called
    sent_msg = smtp_instance.send_message.call_args[0][0]

    to_header = sent_msg.get("To", "")
    bcc_header = sent_msg.get("Bcc", "")

    # The visible To header must not include any of the real recipients.
    assert "one@example.com" not in to_header
    assert "two@example.com" not in to_header

    # Both recipients should be in the Bcc header so SMTP can deliver to
    # them, but they will not see each other in the rendered message
    # (Python's send_message strips the Bcc header before serialising).
    assert "one@example.com" in bcc_header
    assert "two@example.com" in bcc_header


@patch("hexa_agent.mailer.smtplib.SMTP")
def test_explicit_recipients_override_config_and_still_go_to_bcc(MockSMTP):
    smtp_instance = MagicMock()
    MockSMTP.return_value.__enter__.return_value = smtp_instance

    cfg = _Cfg()
    send_email(
        cfg,
        subject="hi",
        text_body="hello",
        html_body="<p>hello</p>",
        recipients=["only-this-one@example.com"],
    )

    sent_msg = smtp_instance.send_message.call_args[0][0]
    bcc = sent_msg.get("Bcc", "")
    assert bcc == "only-this-one@example.com"
    # Original config recipients must not leak when an override is given.
    assert "two@example.com" not in bcc


def test_validation_runs_before_smtp():
    cfg = _Cfg(smtp_username="", smtp_password="")
    with pytest.raises(MailerError):
        send_email(cfg, subject="x", text_body="x", html_body="<p>x</p>")
