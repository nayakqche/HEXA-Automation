"""Simple SMTP mailer that sends a multipart text+html message."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol, Sequence

logger = logging.getLogger(__name__)


class _MailConfig(Protocol):
    smtp_host: str
    smtp_port: int
    smtp_use_tls: bool
    smtp_username: str
    smtp_password: str
    mail_from: str

    @property
    def mail_to(self) -> list[str]:  # pragma: no cover - structural typing
        ...


class MailerError(RuntimeError):
    pass


def _validate(cfg: _MailConfig) -> list[str]:
    errors: list[str] = []
    if not cfg.smtp_username:
        errors.append("SMTP username is required")
    if not cfg.smtp_password:
        errors.append("SMTP password is required")
    if not cfg.mail_from:
        errors.append("Mail-From is required")
    if not cfg.mail_to:
        errors.append("At least one recipient is required")
    return errors


def send_email(
    cfg: _MailConfig,
    *,
    subject: str,
    text_body: str,
    html_body: str,
    recipients: Sequence[str] | None = None,
) -> None:
    """Send a multipart email using the SMTP settings in ``cfg``.

    ``cfg`` can be either a :class:`hexa_agent.config.Config` or a
    :class:`hexa_agent.settings.Settings`; both expose the same fields.
    Raises ``MailerError`` if validation or sending fails.
    """
    errors = _validate(cfg)
    if errors:
        raise MailerError("Email config invalid: " + "; ".join(errors))

    mail_to = cfg.mail_to if not isinstance(cfg.mail_to, str) else [cfg.mail_to]  # type: ignore[unreachable]
    to_list = list(recipients) if recipients else list(mail_to)
    if not to_list:
        raise MailerError("No recipients configured.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.mail_from
    # Privacy: real recipients go in Bcc so they cannot see each other.
    # The visible "To" header shows just the sender so the email is well
    # formed (some SMTP servers reject mail with no To header).
    msg["To"] = cfg.mail_from
    msg["Bcc"] = ", ".join(to_list)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    logger.info(
        "Sending email via %s:%d to %s (subject=%r)",
        cfg.smtp_host,
        cfg.smtp_port,
        to_list,
        subject,
    )

    try:
        if cfg.smtp_port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=context, timeout=30) as smtp:
                smtp.login(cfg.smtp_username, cfg.smtp_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
                smtp.ehlo()
                if cfg.smtp_use_tls:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                smtp.login(cfg.smtp_username, cfg.smtp_password)
                smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailerError(f"Failed to send email: {exc}") from exc

    logger.info("Email sent successfully to %d recipient(s).", len(to_list))
