import logging

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_utils import fetch_one

logger = logging.getLogger(__name__)


async def get_or_create_customer_for_openemr_pid(session: AsyncSession, pid: int) -> int:
    patient = await fetch_one(
        session,
        """
        SELECT pid, fname, lname, street, postal_code, city, country_code, title
        FROM openemr.patients
        WHERE pid = :pid
        """,
        {"pid": pid},
    )
    if not patient:
        raise HTTPException(status_code=404, detail="openemr patient not found")

    existing = await fetch_one(
        session,
        """
        SELECT id FROM invoice.customers
        WHERE external_system = 'openemr' AND external_id = :pid
        """,
        {"pid": pid},
    )
    if existing:
        return int(existing["id"])

    try:
        inserted = await fetch_one(
            session,
            """
            WITH ins AS (
                INSERT INTO invoice.customers (
                    external_system, external_id, vorname, nachname,
                    strasse, plz, ort, land, reverse_charge
                )
                SELECT
                    'openemr', :pid, :vorname, :nachname,
                    :strasse, :plz, :ort, :land, false
                WHERE NOT EXISTS (
                    SELECT 1 FROM invoice.customers
                    WHERE external_system = 'openemr' AND external_id = :pid
                )
                RETURNING id
            )
            SELECT id FROM ins
            """,
            {
                "pid": pid,
                "vorname": patient.get("fname"),
                "nachname": patient.get("lname"),
                "strasse": patient.get("street"),
                "plz": patient.get("postal_code"),
                "ort": patient.get("city"),
                "land": patient.get("country_code"),
            },
        )
        if inserted:
            return int(inserted["id"])

        # Race: someone else inserted
        existing_after = await fetch_one(
            session,
            """
            SELECT id FROM invoice.customers
            WHERE external_system = 'openemr' AND external_id = :pid
            """,
            {"pid": pid},
        )
        if existing_after:
            return int(existing_after["id"])
    except Exception as exc:
        logger.exception("customer provisioning failed for openemr pid %s", pid)
        raise HTTPException(status_code=500, detail="customer provisioning failed") from exc

    raise HTTPException(status_code=500, detail="customer provisioning failed")
