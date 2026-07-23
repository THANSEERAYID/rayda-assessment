"""Email delivery, ported from the ERP project's smtplib service.

Two things differ from that project, both deliberate:

*Simulated by default.* The dataset's employees have no real addresses, and
sending real mail is a side effect that must be opt-in. So unless SMTP is
explicitly configured (``MAIL_ENABLED=true`` plus credentials), this records the
message as ``simulated`` and never opens a connection. This is the same stance
the action service takes on execution — the effect is recorded, the outbound
integration is a seam you turn on.

*Every send is returned as a result, never raised.* The caller records the
outcome (sent / simulated / failed) in the emails table regardless, so the
Emails page is a faithful log of what the system tried to do.

Configuration (all from the environment, never hardcoded):
  MAIL_ENABLED, MAIL_USERNAME, MAIL_PASSWORD, MAIL_FROM, MAIL_FROM_NAME,
  MAIL_SERVER, MAIL_PORT, MAIL_STARTTLS
"""
from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


@dataclass
class EmailResult:
    status: str  # "sent" | "simulated" | "failed"
    error: str | None = None


def email_enabled() -> bool:
    """Whether a real send should be attempted.

    Requires an explicit opt-in *and* the credentials to do it, so a half-set
    environment falls back to simulation rather than erroring on every send.
    """
    if os.getenv("MAIL_ENABLED", "false").lower() != "true":
        return False
    return bool(os.getenv("MAIL_USERNAME") and os.getenv("MAIL_PASSWORD"))


def employee_address(employee_id: str, company_id: str) -> str:
    """A best-effort recipient for an employee with no address on record.

    The dataset has no employee emails, so this derives a stable placeholder in
    a reserved domain (`.example`, RFC 2606 — guaranteed never to route). It is
    what the simulated send is addressed to; a real deployment would look up the
    actual mailbox here.
    """
    company = company_id.split("-")[0] if company_id else "fleet"
    return f"{employee_id}@{company}.example"


def send_email(
    *,
    to: str,
    subject: str,
    text_content: str,
    html_content: str | None = None,
) -> EmailResult:
    """Send one email, or simulate it when SMTP is not configured.

    Returns an :class:`EmailResult`; never raises, so recording the outcome is
    always possible.
    """
    if not email_enabled():
        return EmailResult(status="simulated")

    try:
        mail_from = os.getenv("MAIL_FROM", os.getenv("MAIL_USERNAME", ""))
        from_name = os.getenv("MAIL_FROM_NAME", "Fleet Copilot")
        server = os.getenv("MAIL_SERVER", "smtp.gmail.com")
        port = int(os.getenv("MAIL_PORT", "587"))
        use_tls = os.getenv("MAIL_STARTTLS", "true").lower() == "true"
        timeout = int(os.getenv("MAIL_SMTP_TIMEOUT", "15"))

        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = f"{from_name} <{mail_from}>"
        message["To"] = to
        message.attach(MIMEText(text_content, "plain"))
        if html_content:
            message.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(server, port, timeout=timeout) as smtp:
            if use_tls:
                smtp.starttls()
            smtp.login(os.getenv("MAIL_USERNAME"), os.getenv("MAIL_PASSWORD"))
            smtp.sendmail(mail_from, [to], message.as_string())
        return EmailResult(status="sent")
    except Exception as exc:  # network, auth, bad address — recorded, not raised
        return EmailResult(status="failed", error=f"{type(exc).__name__}: {exc}")
