import json
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db_utils import execute, execute_returning_id, fetch_all, fetch_one
from app.models import (
    AddLinesFromServiceEntriesRequest,
    CancelRequest,
    CancelResponse,
    DiagnosisUpsert,
    FinalizeResponse,
    InvoiceCreateRequest,
    InvoiceLineCreateRequest,
    InvoiceResponse,
    ServiceEntryResponse,
)


router = APIRouter(prefix="/invoices", tags=["invoices"])
logger = logging.getLogger(__name__)


def _money(val: Optional[Decimal]) -> Decimal:
    if val is None:
        return Decimal("0.00")
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def _get_provider_id(session: AsyncSession, code: str) -> int:
    row = await fetch_one(
        session,
        "SELECT id FROM invoice.providers WHERE code = :code",
        {"code": code},
    )
    if not row:
        raise HTTPException(status_code=404, detail="provider not found")
    return int(row["id"])


async def _get_customer_id(session: AsyncSession, customer_id: Optional[int], external_system: Optional[str], external_id: Optional[int]) -> int:
    if customer_id is not None:
        row = await fetch_one(
            session,
            "SELECT id FROM invoice.customers WHERE id = :cid",
            {"cid": customer_id},
        )
        if not row:
            raise HTTPException(status_code=404, detail="customer not found")
        return int(row["id"])
    if external_system and external_id is not None:
        row = await fetch_one(
            session,
            "SELECT id FROM invoice.customers WHERE external_system = :sys AND external_id = :eid",
            {"sys": external_system, "eid": external_id},
        )
        if not row:
            raise HTTPException(status_code=404, detail="customer not found")
        return int(row["id"])
    raise HTTPException(status_code=422, detail="customer_id or external reference required")


async def _ensure_invoice_draft(session: AsyncSession, invoice_id: int) -> dict[str, Any]:
    inv = await fetch_one(
        session,
        "SELECT * FROM invoice.invoices WHERE id = :id",
        {"id": invoice_id},
    )
    if not inv:
        raise HTTPException(status_code=404, detail="invoice not found")
    if inv["status"] != "draft":
        raise HTTPException(status_code=409, detail="invoice is not draft")
    return inv


async def _load_invoice_full(session: AsyncSession, invoice_id: int) -> InvoiceResponse:
    invoice = await fetch_one(
        session,
        "SELECT * FROM invoice.invoices WHERE id = :id",
        {"id": invoice_id},
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="invoice not found")
    lines = await fetch_all(
        session,
        "SELECT * FROM invoice.invoice_lines WHERE invoice_id = :id ORDER BY position_no",
        {"id": invoice_id},
    )
    diagnoses = await fetch_all(
        session,
        "SELECT * FROM invoice.invoice_diagnoses WHERE invoice_id = :id ORDER BY id",
        {"id": invoice_id},
    )
    invoice["lines"] = lines
    invoice["diagnoses"] = diagnoses
    return InvoiceResponse.model_validate(invoice)


@router.post("/draft", response_model=InvoiceResponse)
async def create_draft(payload: InvoiceCreateRequest, session: AsyncSession = Depends(get_session)) -> InvoiceResponse:
    try:
        async with session.begin():
            provider_id = await _get_provider_id(session, payload.provider_code)
            customer_id = await _get_customer_id(session, payload.customer_id, payload.external_system, payload.external_id)
            def _default_rc(code: str) -> str:
                if code == "PP":
                    return "E"
                if code == "TI":
                    return "S"
                if code == "MS":
                    return "E"
                return "S"
            rc = _default_rc(payload.provider_code)

            new_id = await execute_returning_id(
                session,
                """
                INSERT INTO invoice.invoices(provider_id, customer_id, status, reverse_charge, rechnungs_code)
                VALUES (:provider_id, :customer_id, 'draft', :reverse_charge, :rc)
                RETURNING id
                """,
                {
                    "provider_id": provider_id,
                    "customer_id": customer_id,
                    "reverse_charge": payload.reverse_charge if payload.reverse_charge is not None else False,
                    "rc": rc,
                },
            )
        return await _load_invoice_full(session, new_id)
    except SQLAlchemyError as exc:
        logger.exception("create_draft failed")
        raise HTTPException(status_code=500, detail="database error") from exc


@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(invoice_id: int, session: AsyncSession = Depends(get_session)) -> InvoiceResponse:
    return await _load_invoice_full(session, invoice_id)


@router.patch("/{invoice_id}")
async def patch_invoice(invoice_id: int, payload: dict, session: AsyncSession = Depends(get_session)):
    inv = await fetch_one(
        session,
        "SELECT id, status FROM invoice.invoices WHERE id = :id",
        {"id": invoice_id},
    )
    if not inv:
        raise HTTPException(status_code=404, detail="invoice not found")
    if inv["status"] != "draft":
        raise HTTPException(status_code=409, detail="invoice is not draft")

    rechnungs_code = payload.get("rechnungs_code")
    if rechnungs_code is None:
        raise HTTPException(status_code=422, detail="rechnungs_code required")

    vc = await fetch_one(
        session,
        "SELECT code, meaning FROM invoice.vat_category WHERE code = :c",
        {"c": rechnungs_code},
    )
    if not vc:
        raise HTTPException(status_code=422, detail="invalid rechnungs_code")

    async with session.begin():
        await execute(
            session,
            "UPDATE invoice.invoices SET rechnungs_code = :c, reverse_charge = :reverse_charge WHERE id = :id",
            {"c": rechnungs_code, "reverse_charge": rechnungs_code == "AE", "id": invoice_id},
        )

    return {
        "id": invoice_id,
        "status": "draft",
        "rechnungs_code": rechnungs_code,
        "rechnungs_code_meaning": vc.get("meaning"),
    }


async def _next_position_no(session: AsyncSession, invoice_id: int) -> int:
    row = await fetch_one(
        session,
        "SELECT COALESCE(MAX(position_no), 0) AS max_pos FROM invoice.invoice_lines WHERE invoice_id = :id",
        {"id": invoice_id},
    )
    return int(row["max_pos"]) + 1


@router.post("/{invoice_id}/lines", response_model=InvoiceResponse)
async def add_invoice_line(invoice_id: int, payload: InvoiceLineCreateRequest, session: AsyncSession = Depends(get_session)) -> InvoiceResponse:
    async with session.begin():
        await _ensure_invoice_draft(session, invoice_id)

        service_snapshot: dict[str, Any] = {}
        if payload.service_id:
            svc = await fetch_one(
                session,
                "SELECT id, nummer, beschreibung, kommentar_template, mwst_satz, requires_diagnosis, rechnungskuertzel, standard_einzelpreis "
                "FROM invoice.services_master WHERE id = :sid",
                {"sid": payload.service_id},
            )
            if not svc:
                raise HTTPException(status_code=404, detail="service not found")
            service_snapshot = {
                "service_id": svc["id"],
                "nummer": svc["nummer"],
                "beschreibung": svc["beschreibung"],
                "kommentar_template": svc["kommentar_template"],
                "mwst_satz": str(svc["mwst_satz"]) if svc["mwst_satz"] is not None else None,
                "requires_diagnosis": svc["requires_diagnosis"],
                "rechnungskuertzel": svc["rechnungskuertzel"],
            }
            mwst_satz = Decimal(svc["mwst_satz"])
            einzelpreis_base = Decimal(svc["standard_einzelpreis"])
            einzelpreis = _money(payload.einzelpreis) if payload.einzelpreis is not None else _money(einzelpreis_base)
        else:
            if not payload.nummer and not payload.beschreibung:
                raise HTTPException(status_code=422, detail="nummer or beschreibung required when service_id missing")
            if payload.mwst_satz is None:
                raise HTTPException(status_code=422, detail="mwst_satz required when service_id missing")
            service_snapshot = {
                "nummer": payload.nummer,
                "beschreibung": payload.beschreibung,
                "kommentar": payload.kommentar,
            }
            mwst_satz = _money(payload.mwst_satz)
            if payload.einzelpreis is None:
                raise HTTPException(status_code=422, detail="einzelpreis required when service_id missing")
            einzelpreis = _money(payload.einzelpreis)

        leistungsdatum = payload.leistungsdatum
        menge = _money(payload.menge)
        faktor = _money(payload.faktor)

        gesamtpreis = _money(menge * faktor * einzelpreis)
        if einzelpreis <= Decimal("0") or gesamtpreis <= Decimal("0"):
            raise HTTPException(status_code=422, detail="einzelpreis and gesamtpreis must be greater than zero")

        service_snapshot["einzelpreis"] = str(einzelpreis)

        position_no = await _next_position_no(session, invoice_id)

        logger.debug(
            "add_invoice_line invoice_id=%s pos=%s menge=%s faktor=%s einzelpreis=%s gesamtpreis=%s",
            invoice_id,
            position_no,
            menge,
            faktor,
            einzelpreis,
            gesamtpreis,
        )

        await execute(
            session,
            """
            INSERT INTO invoice.invoice_lines(
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
                "service_snapshot": json.dumps(service_snapshot),
                "leistungsdatum": leistungsdatum,
                "menge": menge,
                "faktor": faktor,
                "mwst_satz": mwst_satz,
                "einzelpreis": einzelpreis,
                "gesamtpreis": gesamtpreis,
            },
        )
    return await _load_invoice_full(session, invoice_id)


@router.post("/{invoice_id}/diagnoses", response_model=InvoiceResponse)
async def upsert_diagnoses(invoice_id: int, payload: list[DiagnosisUpsert], session: AsyncSession = Depends(get_session)) -> InvoiceResponse:
    async with session.begin():
        await _ensure_invoice_draft(session, invoice_id)
        await execute(session, "DELETE FROM invoice.invoice_diagnoses WHERE invoice_id = :id", {"id": invoice_id})
        for idx, diag in enumerate(payload):
            await execute(
                session,
                """
                INSERT INTO invoice.invoice_diagnoses (invoice_id, title, icd_code, begdate, enddate)
                VALUES (:invoice_id, :title, :icd_code, :begdate, :enddate)
                """,
                {
                    "invoice_id": invoice_id,
                    "title": diag.title,
                    "icd_code": diag.icd_code,
                    "begdate": diag.begdate,
                    "enddate": diag.enddate,
                },
            )
    return await _load_invoice_full(session, invoice_id)


@router.post("/{invoice_id}/add-lines-from-service-entries", response_model=InvoiceResponse)
async def add_lines_from_service_entries(invoice_id: int, payload: AddLinesFromServiceEntriesRequest, session: AsyncSession = Depends(get_session)) -> InvoiceResponse:
    if not payload.service_entry_ids:
        raise HTTPException(status_code=422, detail="service_entry_ids required")

    async with session.begin():
        await _ensure_invoice_draft(session, invoice_id)
        position_no = await _next_position_no(session, invoice_id)
        for entry_id in payload.service_entry_ids:
            entry = await fetch_one(
                session,
                """
                SELECT se.*, sm.nummer, sm.beschreibung, sm.kommentar_template, sm.mwst_satz, sm.requires_diagnosis
                FROM invoice.service_entries se
                JOIN invoice.services_master sm ON sm.id = se.service_id
                WHERE se.id = :id
                """,
                {"id": entry_id},
            )
            if not entry:
                raise HTTPException(status_code=404, detail=f"service_entry {entry_id} not found")

            snapshot = {
                "nummer": entry["nummer"],
                "beschreibung": entry["beschreibung"],
                "kommentar_template": entry["kommentar_template"],
                "kommentar": entry["kommentar"],
                "requires_diagnosis": entry["requires_diagnosis"],
            }
            mwst_satz = _money(Decimal(entry["mwst_satz"]))
            menge = _money(Decimal(entry["menge"]))
            faktor = _money(Decimal(entry["faktor"]))

            await execute(
                session,
                """
                INSERT INTO invoice.invoice_lines(
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
                    "mwst_satz": mwst_satz,
                    "einzelpreis": Decimal("0.00"),
                    "gesamtpreis": Decimal("0.00"),
                },
            )
            position_no += 1
            await execute(
                session,
                "UPDATE invoice.service_entries SET status = 'invoiced' WHERE id = :id",
                {"id": entry_id},
            )
    return await _load_invoice_full(session, invoice_id)


@router.post("/{invoice_id}/finalize", response_model=FinalizeResponse)
async def finalize_invoice(invoice_id: int, session: AsyncSession = Depends(get_session)) -> FinalizeResponse:
    async with session.begin():
        await _ensure_invoice_draft(session, invoice_id)
        await execute(session, "SELECT invoice.invoice_finalize(:id)", {"id": invoice_id})
    refreshed = await fetch_one(
        session,
        "SELECT id AS invoice_id, invoice_number, status, invoice_date, totals, hash FROM invoice.invoices WHERE id = :id",
        {"id": invoice_id},
    )
    if not refreshed:
        raise HTTPException(status_code=404, detail="invoice not found after finalize")
    return FinalizeResponse.model_validate(refreshed)


@router.post("/{invoice_id}/cancel", response_model=CancelResponse)
async def cancel_invoice(invoice_id: int, payload: CancelRequest, session: AsyncSession = Depends(get_session)) -> CancelResponse:
    inv = await fetch_one(session, "SELECT id, status, cancelled_at FROM invoice.invoices WHERE id = :id", {"id": invoice_id})
    if not inv:
        raise HTTPException(status_code=404, detail="invoice not found")
    if inv["status"] != "final":
        raise HTTPException(status_code=409, detail="invoice is not final")
    if inv.get("cancelled_at"):
        raise HTTPException(status_code=409, detail="invoice already cancelled")

    line_count_row = await fetch_one(session, "SELECT COUNT(*) AS cnt FROM invoice.invoice_lines WHERE invoice_id = :id", {"id": invoice_id})
    line_count = int(line_count_row["cnt"]) if line_count_row else 0
    try:
        credit_id = await execute_returning_id(
            session,
            "SELECT invoice.invoice_cancel(:id, :reason)",
            {"id": invoice_id, "reason": payload.reason},
        )
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.exception("cancel failed")
        raise HTTPException(status_code=409, detail="cancel failed") from exc

    return CancelResponse(
        cancelled_invoice_id=invoice_id,
        credit_invoice_id=credit_id,
        reopened_service_entries=line_count,
    )


@router.get("/customers/{customer_id}/open-service-entries", response_model=list[ServiceEntryResponse])
async def list_open_service_entries(customer_id: int, provider_code: Optional[str] = Query(None, pattern="^(TI|PP|MS)$"), session: AsyncSession = Depends(get_session)) -> list[ServiceEntryResponse]:
    params: dict[str, Any] = {"cid": customer_id}
    provider_filter = ""
    if provider_code:
        provider_filter = "AND p.code = :pcode"
        params["pcode"] = provider_code

    rows = await fetch_all(
        session,
        f"""
        SELECT se.*
        FROM invoice.service_entries se
        JOIN invoice.providers p ON p.id = se.provider_id
        WHERE se.customer_id = :cid AND se.status = 'open' {provider_filter}
        ORDER BY se.leistungsdatum DESC, se.id
        """,
        params,
    )
    return [ServiceEntryResponse.model_validate(r) for r in rows]
