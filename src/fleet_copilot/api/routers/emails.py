"""Emails — the log of messages sent (or simulated), plus a manual compose send."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...services.email import send_email
from ...storage.db import connect
from ...storage.repositories.emails import EmailRepository
from ..deps import require_company

router = APIRouter(tags=["emails"])


class EmailIn(BaseModel):
    company_id: str
    to_address: str = Field(min_length=3)
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)


@router.get("/emails")
def list_emails(company_id: str) -> dict:
    """A tenant's email log, newest first."""
    require_company(company_id)
    with connect() as conn:
        rows = EmailRepository(conn).list_for_company(company_id)
    return {
        "company_id": company_id,
        "emails": [
            {
                "email_id": r.email_id,
                "action_id": r.action_id,
                "employee_id": r.employee_id,
                "to_address": r.to_address,
                "subject": r.subject,
                "body": r.body,
                "status": r.status,
                "error": r.error,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.post("/emails")
def send_manual_email(payload: EmailIn) -> dict:
    """Send (or simulate) a hand-composed email and record it.

    Simulated unless SMTP is configured — the same boundary the agent's
    notify_employee action goes through.
    """
    require_company(payload.company_id)
    result = send_email(
        to=payload.to_address, subject=payload.subject, text_content=payload.body
    )
    with connect() as conn:
        email_id = EmailRepository(conn).record(
            company_id=payload.company_id,
            to_address=payload.to_address,
            subject=payload.subject,
            body=payload.body,
            status=result.status,
            error=result.error,
        )
    if result.status == "failed":
        raise HTTPException(status_code=502, detail=result.error or "Email send failed.")
    return {"email_id": email_id, "status": result.status}
