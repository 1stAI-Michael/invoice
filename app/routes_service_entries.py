import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.customer_provisioning import get_or_create_customer_for_openemr_pid
from app.db_utils import execute, execute_returning_id, execute_returning_row, fetch_all, fetch_one
from app.models import (
    ServiceEntryBatchCreateRequest,
    ServiceEntryCreateRequest,
    ServiceEntryResponse,
    ServiceEntryUpdateRequest,
)

router = APIRouter(prefix="/service-entries", tags=["service-entries"])
logger = logging.getLogger(__name__)


def _money(val: Optional[Decimal]) -> Decimal:
    if val is None:
        return Decimal("0.00")
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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


async def _ensure_customer(session: AsyncSession, customer_id: Optional[int], openemr_pid: Optional[int]) -> int:
    if customer_id is not None:
        row = await fetch_one(session, "SELECT id FROM invoice.customers WHERE id = :cid", {"cid": customer_id})
        if not row:
            raise HTTPException(status_code=404, detail="customer not found")
        return int(row["id"])
    if openemr_pid is not None:
        return await get_or_create_customer_for_openemr_pid(session, openemr_pid)
    raise HTTPException(status_code=422, detail="customer_id or openemr_pid required")


@router.post("", response_model=ServiceEntryResponse)
async def create_entry(payload: ServiceEntryCreateRequest, session: AsyncSession = Depends(get_session)):
    provider_id = await _provider_id(session, payload.provider_code)
    customer_id = await _ensure_customer(session, payload.customer_id, payload.openemr_pid)

    data = {
        "customer_id": customer_id,
        "provider_id": provider_id,
        "service_id": payload.service_id,
        "external_encounter_id": payload.external_encounter_id,
        "leistungsdatum": payload.leistungsdatum,
        "menge": _money(payload.menge),
        "faktor": _money(payload.faktor),
        "kommentar": payload.kommentar,
    }
    async with session.begin():
        row = await execute_returning_row(
            session,
            """
            INSERT INTO invoice.service_entries (
                customer_id, provider_id, service_id, external_encounter_id,
                leistungsdatum, menge, faktor, kommentar, status
            ) VALUES (
                :customer_id, :provider_id, :service_id, :external_encounter_id,
                :leistungsdatum, :menge, :faktor, :kommentar, 'open'
            )
            RETURNING *
            """,
            data,
        )
    return ServiceEntryResponse.model_validate(row)


@router.post("/batch", response_model=list[ServiceEntryResponse])
async def create_entries_batch(payload: ServiceEntryBatchCreateRequest, session: AsyncSession = Depends(get_session)):
    if not payload.entries:
        raise HTTPException(status_code=422, detail="entries required")
    created: list[ServiceEntryResponse] = []
    async with session.begin():
        for entry in payload.entries:
            provider_id = await _provider_id(session, entry.provider_code)
            customer_id = await _ensure_customer(session, entry.customer_id, entry.openemr_pid)
            data = {
                "customer_id": customer_id,
                "provider_id": provider_id,
                "service_id": entry.service_id,
                "external_encounter_id": entry.external_encounter_id,
                "leistungsdatum": entry.leistungsdatum,
                "menge": _money(entry.menge),
                "faktor": _money(entry.faktor),
                "kommentar": entry.kommentar,
            }
            row = await execute_returning_row(
                session,
                """
                INSERT INTO invoice.service_entries (
                    customer_id, provider_id, service_id, external_encounter_id,
                    leistungsdatum, menge, faktor, kommentar, status
                ) VALUES (
                    :customer_id, :provider_id, :service_id, :external_encounter_id,
                    :leistungsdatum, :menge, :faktor, :kommentar, 'open'
                )
                RETURNING *
                """,
                data,
            )
            created.append(ServiceEntryResponse.model_validate(row))
    return created


@router.get("", response_model=list[ServiceEntryResponse])
async def list_entries(
    provider_code: Optional[str] = Query(None, pattern="^(TI|PP|MS)$"),
    customer_id: Optional[int] = None,
    openemr_pid: Optional[int] = None,
    status: str = Query("open", pattern="^(open|invoiced)$"),
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    cid = await _ensure_customer(session, customer_id, openemr_pid) if (customer_id is not None or openemr_pid is not None) else None
    pid_filter = ""
    params: dict[str, Any] = {"status": status}
    if provider_code:
        pid_filter += " AND se.provider_id = :provider_id"
        params["provider_id"] = await _provider_id(session, provider_code)
    if cid is not None:
        pid_filter += " AND se.customer_id = :cid"
        params["cid"] = cid
    if from_date:
        pid_filter += " AND se.leistungsdatum >= :from_date"
        params["from_date"] = from_date
    if to_date:
        pid_filter += " AND se.leistungsdatum <= :to_date"
        params["to_date"] = to_date

    rows = await fetch_all(
        session,
        f"""
        SELECT * FROM invoice.service_entries se
        WHERE se.status = :status {pid_filter}
        ORDER BY se.leistungsdatum DESC, se.created_at DESC
        """,
        params,
    )
    return [ServiceEntryResponse.model_validate(r) for r in rows]


@router.patch("/{entry_id}", response_model=ServiceEntryResponse)
async def update_entry(entry_id: int, payload: ServiceEntryUpdateRequest, session: AsyncSession = Depends(get_session)):
    existing = await fetch_one(session, "SELECT * FROM invoice.service_entries WHERE id = :id", {"id": entry_id})
    if not existing:
        raise HTTPException(status_code=404, detail="service_entry not found")
    if existing["status"] != "open":
        raise HTTPException(status_code=409, detail="service_entry not editable (not open)")

    fields = []
    params: dict[str, Any] = {"id": entry_id}
    if payload.service_id is not None:
        fields.append("service_id = :service_id")
        params["service_id"] = payload.service_id
    if payload.external_encounter_id is not None:
        fields.append("external_encounter_id = :external_encounter_id")
        params["external_encounter_id"] = payload.external_encounter_id
    if payload.leistungsdatum is not None:
        fields.append("leistungsdatum = :leistungsdatum")
        params["leistungsdatum"] = payload.leistungsdatum
    if payload.menge is not None:
        fields.append("menge = :menge")
        params["menge"] = _money(Decimal(payload.menge))
    if payload.faktor is not None:
        fields.append("faktor = :faktor")
        params["faktor"] = _money(Decimal(payload.faktor))
    if payload.kommentar is not None:
        fields.append("kommentar = :kommentar")
        params["kommentar"] = payload.kommentar

    if not fields:
        return ServiceEntryResponse.model_validate(existing)

    async with session.begin():
        await execute(
            session,
            f"UPDATE invoice.service_entries SET {', '.join(fields)} WHERE id = :id",
            params,
        )
        updated = await fetch_one(session, "SELECT * FROM invoice.service_entries WHERE id = :id", {"id": entry_id})
    return ServiceEntryResponse.model_validate(updated)


@router.delete("/{entry_id}")
async def delete_entry(entry_id: int, session: AsyncSession = Depends(get_session)):
    existing = await fetch_one(session, "SELECT status FROM invoice.service_entries WHERE id = :id", {"id": entry_id})
    if not existing:
        raise HTTPException(status_code=404, detail="service_entry not found")
    if existing["status"] != "open":
        raise HTTPException(status_code=409, detail="service_entry not deletable (not open)")
    async with session.begin():
        await execute(session, "DELETE FROM invoice.service_entries WHERE id = :id", {"id": entry_id})
    return {"deleted": True, "id": entry_id}
