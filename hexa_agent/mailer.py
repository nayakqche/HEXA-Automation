"""Simple SMTP mailer that sends a multipart text+html message."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Sequence

from .config import Config

logger = logging.getLogger(__name__)


class MailerError(RuntimeError):
    pass


def send_email(
    cfg: Config,
    *,
    subject: str,
    text_body: str,
    html_body: str,
    recipients: Sequence[str] | None = None,
) -> None:
    """Send a multipart email using the SMTP settings in ``cfg``.

    Raises MailerError if validation or sending fails.
    """
    errors = cfg.validate_for_email()
    if errors:
        raise MailerError("Email config invalid: " + "; ".join(errors))

    to_list = list(recipients) if recipients else cfg.mail_to
    if not to_list:
        raise MailerError("No recipients configured (MAIL_TO is empty).")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.mail_from
    msg["To"] = ", ".join(to_list)
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
