import json
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.customer_provisioning import get_or_create_customer_for_openemr_pid
from app.db import get_session
from app.db_utils import execute, execute_returning_id, fetch_all, fetch_one
from app.models import DraftFromServiceEntriesRequest

router = APIRouter(prefix="/workflow", tags=["workflow"])
logger = logging.getLogger(__name__)


def _money(val: Optional[Decimal]) -> Decimal:
    if val is None:
        return Decimal("0.00")
    return Decimal(val).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def _provider_id(session: AsyncSession, code: str) -> int:
    row = await fetch_one(session, "SELECT id FROM invoice.providers WHERE code = :c", {"c": code})
    if not row:
        inserted = await execute_returning_id(
            session,
            "INSERT INTO invoice.providers (code) VALUES (:c) ON CONFLICT (code) DO NOTHING RETURNING id",
            {"c": code},
        )
        if inserted:
            return int(inserted)
        row = await fetch_one(session, "SELECT id FROM invoice.providers WHERE code = :c", {"c": code})
        if not row:
            raise HTTPException(status_code=404, detail="provider not found")
    return int(row["id"])


async def _resolve_customer_id(session: AsyncSession, customer_id: Optional[int], openemr_pid: Optional[int]) -> int:
    if customer_id is not None:
        row = await fetch_one(session, "SELECT id FROM invoice.customers WHERE id = :cid", {"cid": customer_id})
        if not row:
            raise HTTPException(status_code=404, detail="customer not found")
        return int(row["id"])
    if openemr_pid is not None:
        return await get_or_create_customer_for_openemr_pid(session, openemr_pid)
    raise HTTPException(status_code=422, detail="customer_id or openemr_pid required")


@router.post("/invoices/draft-from-service-entries")
async def draft_from_entries(payload: DraftFromServiceEntriesRequest, session: AsyncSession = Depends(get_session)):
    provider_id = await _provider_id(session, payload.provider_code)
    customer_id = await _resolve_customer_id(session, payload.customer_id, payload.openemr_pid)

    params: dict[str, Any] = {"provider_id": provider_id, "customer_id": customer_id}
    ids_filter = ""
    if payload.service_entry_ids:
        ids_filter = "AND se.id = ANY(:ids)"
        params["ids"] = payload.service_entry_ids

    entries = await fetch_all(
        session,
        f"""
        SELECT se.* FROM invoice.service_entries se
        WHERE se.provider_id = :provider_id AND se.customer_id = :customer_id AND se.status = 'open' {ids_filter}
        ORDER BY se.leistungsdatum ASC, se.id ASC
        """,
        params,
    )
    if not entries:
        raise HTTPException(status_code=404, detail="no open service entries for provider/customer")
    if payload.service_entry_ids and len(entries) != len(payload.service_entry_ids):
        raise HTTPException(status_code=409, detail="one or more service_entry_ids not open or mismatch provider/customer")

    async with session.begin():
        invoice_id = await execute_returning_id(
            session,
            """
            INSERT INTO invoice.invoices (provider_id, customer_id, status, reverse_charge)
            VALUES (:provider_id, :customer_id, 'draft', :reverse_charge)
            RETURNING id
            """,
            {
                "provider_id": provider_id,
                "customer_id": customer_id,
                "reverse_charge": payload.reverse_charge if payload.reverse_charge is not None else False,
            },
        )

        position_no = 1
        for entry in entries:
            svc = await fetch_one(
                session,
                """
                SELECT id, nummer, beschreibung, kommentar_template, mwst_satz,
                       requires_diagnosis, COALESCE(standard_einzelpreis, 0) AS standard_einzelpreis
                FROM invoice.services_master
                WHERE id = :sid
                """,
                {"sid": entry["service_id"]},
            )
            if not svc:
                raise HTTPException(status_code=404, detail=f"service_master {entry['service_id']} not found")

            einzelpreis = _money(svc.get("standard_einzelpreis"))
            menge = _money(entry["menge"])
            faktor = _money(entry["faktor"])
            gesamtpreis = _money(menge * faktor * einzelpreis)

            snapshot = {
                "service_id": svc["id"],
                "nummer": svc["nummer"],
                "beschreibung": svc["beschreibung"],
                "kommentar_template": svc["kommentar_template"],
                "kommentar": entry.get("kommentar"),
                "mwst_satz": str(svc.get("mwst_satz")) if svc.get("mwst_satz") is not None else None,
                "einzelpreis": str(einzelpreis),
                "requires_diagnosis": svc.get("requires_diagnosis"),
            }

            await execute(
                session,
                """
                INSERT INTO invoice.invoice_lines (
                    invoice_id, position_no, service_snapshot, leistungsdatum,
                    menge, faktor, mwst_satz, einzelpreis, gesamtpreis
                ) VALUES (
                    :invoice_id, :position_no, :service_snapshot, :leistungsdatum,
                    :menge, :faktor, :mwst_satz, :einzelpreis, :gesamtpreis
                )
                """,
                {
                    "invoice_id": invoice_id,
                    "position_no": position_no,
                    "service_snapshot": json.dumps(snapshot),
                    "leistungsdatum": entry["leistungsdatum"],
                    "menge": menge,
                    "faktor": faktor,
                    "mwst_satz": _money(Decimal(svc.get("mwst_satz") or 0)),
                    "einzelpreis": einzelpreis,
                    "gesamtpreis": gesamtpreis,
                },
            )
            position_no += 1

        entry_ids = [e["id"] for e in entries]
        await execute(
            session,
            "UPDATE invoice.service_entries SET status = 'invoiced' WHERE id = ANY(:ids)",
            {"ids": entry_ids},
        )

    return {
        "invoice_id": invoice_id,
        "status": "draft",
        "lines_added": len(entries),
        "service_entries_marked_invoiced": len(entries),
    }
