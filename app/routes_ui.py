import json
import logging
from typing import Optional, Any

from datetime import date as dtdate
from decimal import Decimal
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import ProgrammingError, IntegrityError
from decimal import InvalidOperation

from app.db_utils import transaction_session
from app.customer_provisioning import get_or_create_customer_for_openemr_pid
from app.db import get_session
from app.db_utils import execute, execute_returning_id, fetch_all, fetch_one

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(prefix="/ui", tags=["ui"])
# Log to uvicorn.error so messages appear in container logs
logger = logging.getLogger("uvicorn.error")

@router.get("/provider/{provider_id}/edit", response_class=HTMLResponse)
async def ui_provider_edit(request: Request, provider_id: int, session: AsyncSession = Depends(get_session)):
    provider = await fetch_one(session, "SELECT * FROM invoice.providers WHERE id = :id", {"id": provider_id})
    if not provider:
        raise HTTPException(status_code=404, detail="provider not found")
    return templates.TemplateResponse("provider_edit.html", {"request": request, "provider": provider})


@router.post("/provider/{provider_id}/edit", response_class=HTMLResponse)
async def ui_provider_edit_save(
    request: Request,
    provider_id: int,
    firma: Optional[str] = Form(None),
    vorname: Optional[str] = Form(None),
    nachname: Optional[str] = Form(None),
    iban: Optional[str] = Form(None),
    bic: Optional[str] = Form(None),
    tax_id: Optional[str] = Form(None),
    vat_id: Optional[str] = Form(None),
    freitext1: Optional[str] = Form(None),
    freitext2: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_session),
):
    provider = await fetch_one(session, "SELECT id FROM invoice.providers WHERE id = :id", {"id": provider_id})
    if not provider:
        raise HTTPException(status_code=404, detail="provider not found")
    await execute(
        session,
        """
        UPDATE invoice.providers
        SET firma=:firma, vorname=:vorname, nachname=:nachname,
            iban=:iban, bic=:bic, tax_id=:tax_id, vat_id=:vat_id,
            freitext1=:freitext1, freitext2=:freitext2
        WHERE id=:id
        """,
        {
            "id": provider_id,
            "firma": firma,
            "vorname": vorname,
            "nachname": nachname,
            "iban": iban,
            "bic": bic,
            "tax_id": tax_id,
            "vat_id": vat_id,
            "freitext1": freitext1,
            "freitext2": freitext2,
        },
    )
    await session.commit()
    return RedirectResponse(url=f"/ui/provider/{provider_id}/edit", status_code=303)


@router.get("/customer/{customer_id}/edit", response_class=HTMLResponse)
async def ui_customer_edit(request: Request, customer_id: int, session: AsyncSession = Depends(get_session)):
    customer = await fetch_one(session, "SELECT * FROM invoice.customers WHERE id = :id", {"id": customer_id})
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")
    payment_terms = await _payment_terms(session)
    return templates.TemplateResponse(
        "customer_edit.html",
        {"request": request, "customer": customer, "payment_terms": payment_terms},
    )


@router.post("/customer/{customer_id}/edit", response_class=HTMLResponse)
async def ui_customer_edit_save(
    request: Request,
    customer_id: int,
    firma: Optional[str] = Form(None),
    vorname: Optional[str] = Form(None),
    nachname: Optional[str] = Form(None),
    tax_id: Optional[str] = Form(None),
    vat_id: Optional[str] = Form(None),
    zahlungsbedingung_key: Optional[str] = Form(None),
    strasse: Optional[str] = Form(None),
    plz: Optional[str] = Form(None),
    ort: Optional[str] = Form(None),
    land: Optional[str] = Form(None),
    external_system: Optional[str] = Form(None),
    external_id: Optional[str] = Form(None),
    reverse_charge: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_session),
):
    customer = await fetch_one(session, "SELECT id FROM invoice.customers WHERE id = :id", {"id": customer_id})
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")
    ext_id_val = None
    if external_id not in (None, ""):
        try:
            ext_id_val = int(external_id)
        except (TypeError, ValueError):
            ext_id_val = external_id  # fallback to raw text
    await execute(
        session,
        """
        UPDATE invoice.customers
        SET firma=:firma, vorname=:vorname, nachname=:nachname,
            tax_id=:tax_id, vat_id=:vat_id,
            strasse=:strasse, plz=:plz, ort=:ort, land=:land,
            external_system=:external_system, external_id=:external_id,
            reverse_charge=:reverse_charge,
            zahlungsbedingung_key = COALESCE(NULLIF(btrim(:zbk),''), 'SOFORT')
        WHERE id=:id
        """,
        {
            "id": customer_id,
            "firma": firma,
            "vorname": vorname,
            "nachname": nachname,
            "tax_id": tax_id,
            "vat_id": vat_id,
            "strasse": strasse,
            "plz": plz,
            "ort": ort,
            "land": land,
            "external_system": external_system,
            "external_id": ext_id_val,
            "reverse_charge": True if reverse_charge else False,
            "zbk": zahlungsbedingung_key,
        },
    )
    await session.commit()
    return RedirectResponse(url=f"/ui/customer/{customer_id}/edit", status_code=303)


@router.get("/customers/new", response_class=HTMLResponse)
async def ui_customer_new(request: Request, session: AsyncSession = Depends(get_session)):
    payment_terms = await _payment_terms(session)
    return templates.TemplateResponse(
        "customer_new.html",
        {"request": request, "payment_terms": payment_terms},
    )


@router.post("/customers/new", response_class=HTMLResponse)
async def ui_customer_new_save(
    request: Request,
    firma: Optional[str] = Form(None),
    vorname: Optional[str] = Form(None),
    nachname: Optional[str] = Form(None),
    strasse: Optional[str] = Form(None),
    plz: Optional[str] = Form(None),
    ort: Optional[str] = Form(None),
    land: Optional[str] = Form(None),
    external_system: Optional[str] = Form(None),
    external_id: Optional[str] = Form(None),
    tax_id: Optional[str] = Form(None),
    vat_id: Optional[str] = Form(None),
    reverse_charge: Optional[str] = Form(None),
    zahlungsbedingung_key: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_session),
):
    ext_id_val = None
    if external_id not in (None, ""):
        try:
            ext_id_val = int(external_id)
        except (TypeError, ValueError):
            ext_id_val = external_id
    try:
        new_id = await execute_returning_id(
            session,
            """
            INSERT INTO invoice.customers (
                firma, vorname, nachname, strasse, plz, ort, land,
                external_system, external_id,
                tax_id, vat_id, reverse_charge, zahlungsbedingung_key
            ) VALUES (
                :firma, :vorname, :nachname, :strasse, :plz, :ort, :land,
                :external_system, :external_id,
                :tax_id, :vat_id, :reverse_charge,
                COALESCE(NULLIF(btrim(:zbk),''), 'SOFORT')
            )
            RETURNING id
            """,
            {
                "firma": firma,
                "vorname": vorname,
                "nachname": nachname,
                "strasse": strasse,
                "plz": plz,
                "ort": ort,
                "land": land,
                "external_system": external_system,
                "external_id": ext_id_val,
                "tax_id": tax_id,
                "vat_id": vat_id,
                "reverse_charge": True if reverse_charge else False,
                "zbk": zahlungsbedingung_key,
            },
        )
        await session.commit()
        return RedirectResponse(url=f"/ui/customer/{new_id}", status_code=303)
    except Exception:
        await session.rollback()
        raise

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


async def _services_for_provider(session: AsyncSession, provider_code: str):
    return await fetch_all(
        session,
        """
        SELECT id, nummer, beschreibung, standard_einzelpreis, mwst_satz, standard_faktor, kommentar_template
        FROM invoice.services_master
        WHERE rechnungskuertzel = :pc
        ORDER BY nummer
        """,
        {"pc": provider_code},
    )


def _decimal_or_zero(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _calculate_entry_totals(entries: list):
    day_totals = {}
    encounter_total = Decimal("0")
    for entry in entries:
        line_total = _decimal_or_zero(entry.get("line_total"))
        encounter_total += line_total
        day = entry.get("leistungsdatum")
        if day:
            day_totals[day] = day_totals.get(day, Decimal("0")) + line_total
    day_totals_list = [{"date": day, "total": total} for day, total in sorted(day_totals.items())]
    return day_totals_list, encounter_total


async def _load_open_entries(session: AsyncSession, customer_id: int, provider_id: int):
    return await fetch_all(
        session,
        """
        SELECT se.*, sm.nummer, sm.beschreibung, sm.standard_einzelpreis,
               (se.menge * se.faktor * sm.standard_einzelpreis) AS line_total
        FROM invoice.service_entries se
        JOIN invoice.services_master sm ON sm.id = se.service_id
        WHERE se.customer_id = :cid AND se.provider_id = :pid AND se.status = 'open'
        ORDER BY se.leistungsdatum DESC NULLS LAST, sm.nummer ASC, se.id ASC
        """,
        {"cid": customer_id, "pid": provider_id},
    )


async def _load_service_entries(session: AsyncSession, customer_id: int, provider_id: int):
    return await fetch_all(
        session,
        """
        SELECT se.*, sm.nummer, sm.beschreibung, sm.standard_einzelpreis,
               (se.menge * se.faktor * sm.standard_einzelpreis) AS line_total
        FROM invoice.service_entries se
        JOIN invoice.services_master sm ON sm.id = se.service_id
        WHERE se.customer_id = :cid AND se.provider_id = :pid
        ORDER BY se.leistungsdatum DESC NULLS LAST, sm.nummer ASC, se.id ASC
        """,
        {"cid": customer_id, "pid": provider_id},
    )


async def _load_ziffernkette_options(session: AsyncSession, provider_code: str):
    return await fetch_all(
        session,
        """
        SELECT DISTINCT kuerzel
        FROM invoice.ziffernkette
        WHERE rechnungskuertzel = :provider_code
          AND kuerzel IS NOT NULL
          AND BTRIM(kuerzel) <> ''
        ORDER BY kuerzel ASC
        """,
        {"provider_code": provider_code},
    )


async def _load_encounter_context_entries(
    session: AsyncSession, customer_id: int, provider_id: int, encounter_id: Optional[int]
):
    encounter_entries_rows = await _load_service_entries(session, customer_id, provider_id)
    encounter_entries = {}
    for row in encounter_entries_rows:
        encounter_entries.setdefault(row.get("external_encounter_id"), []).append(row)
    entries = (encounter_entries.get(encounter_id, []) + encounter_entries.get(None, [])) if encounter_entries else []
    day_totals, encounter_total = _calculate_entry_totals(entries)
    return entries, day_totals, encounter_total


async def _search_openemr_list(session: AsyncSession, q: str):
    term = f"%{q}%"
    exact_pid_text = q if q.isdigit() else ""
    return await fetch_all(
        session,
        """
        WITH matches AS (
            SELECT p.pid, p.fname, p.lname, p.dob, p.city
            FROM openemr.patients p
            WHERE p.fname ILIKE :term OR p.lname ILIKE :term OR CAST(p.pid AS TEXT) ILIKE :term
               OR (:exact_pid_text <> '' AND p.pid = CAST(:exact_pid_text AS bigint))
            UNION
            SELECT c.external_id AS pid, c.vorname AS fname, c.nachname AS lname, NULL::date AS dob, c.ort AS city
            FROM invoice.customers c
            WHERE c.external_system = 'openemr'
              AND (c.vorname ILIKE :term OR c.nachname ILIKE :term OR CAST(c.external_id AS TEXT) ILIKE :term)
        )
        SELECT DISTINCT pid, fname, lname, dob, city
        FROM matches
        ORDER BY lname, fname
        LIMIT 50
        """,
        {"term": term, "exact_pid_text": exact_pid_text},
    )


async def _search_customers_list(session: AsyncSession, q: str):
    term = f"%{q}%"
    return await fetch_all(
        session,
        """
        SELECT id, firma, vorname, nachname, vat_id, tax_id, external_id
        FROM invoice.customers
        WHERE (firma ILIKE :term OR vorname ILIKE :term OR nachname ILIKE :term
               OR vat_id ILIKE :term OR tax_id ILIKE :term OR CAST(external_id AS TEXT) ILIKE :term)
        ORDER BY COALESCE(firma, nachname), vorname
        LIMIT 50
        """,
        {"term": term},
    )


@router.get("", response_class=HTMLResponse)
async def ui_home(request: Request, q: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    patients = customers = None
    if q:
        patients = await _search_openemr_list(session, q)
        customers = await _search_customers_list(session, q)
    return templates.TemplateResponse("ui_home.html", {"request": request, "patients": patients, "customers": customers, "q": q or ""})


@router.get("/search-openemr", response_class=HTMLResponse)
async def ui_search_openemr(request: Request, q: str, session: AsyncSession = Depends(get_session)):
    rows = await _search_openemr_list(session, q)
    return templates.TemplateResponse("_openemr_results.html", {"request": request, "patients": rows})


@router.get("/search-customers", response_class=HTMLResponse)
async def ui_search_customers(request: Request, q: str, session: AsyncSession = Depends(get_session)):
    rows = await _search_customers_list(session, q)
    return templates.TemplateResponse("_customer_results.html", {"request": request, "customers": rows})


@router.get("/patient/{pid}", response_class=HTMLResponse)
async def ui_patient_detail(request: Request, pid: int, provider: str = "PP", session: AsyncSession = Depends(get_session)):
    if provider not in {"PP", "TI", "MS"}:
        provider = "PP"
    provider_id = await _provider_id(session, provider)
    try:
        async with transaction_session() as tx:
            patient = await fetch_one(tx, "SELECT * FROM openemr.patients WHERE pid = :pid", {"pid": pid})
            if not patient:
                raise HTTPException(status_code=404, detail="patient not found")
            customer_id = await get_or_create_customer_for_openemr_pid(tx, pid)
            tx_customer_id = customer_id
    except HTTPException:
        raise
    except Exception:
        logger.exception("failed to load patient %s", pid)
        raise HTTPException(status_code=500, detail="failed to load patient")
    diagnoses = await fetch_all(
        session,
        """
        SELECT * FROM openemr.problems
        WHERE pid = :pid AND enddate IS NULL
        ORDER BY begdate DESC NULLS LAST, id DESC
        """,
        {"pid": pid},
    )
    encounters = await fetch_all(
        session,
        """
        SELECT id, date AS encounter_datetime, date::date AS encounter_date, reason
        FROM openemr.encounters
        WHERE pid = :pid
          AND (date::date) >= current_date - interval '400 days'
        ORDER BY encounter_datetime DESC NULLS LAST, id DESC
        """,
        {"pid": pid},
    )
    notes = await fetch_all(
        session,
        """
        SELECT encounter_id, date AS note_datetime, date::date AS note_date, codetext, description
        FROM openemr.clinical_notes
        WHERE pid = :pid
        ORDER BY note_datetime DESC NULLS LAST, encounter_id DESC
        """,
        {"pid": pid},
    )
    notes_by_enc = {}
    for n in notes:
        notes_by_enc.setdefault(n["encounter_id"], []).append(n)
    note_text_by_enc = {}
    note_text_by_date = {}
    for enc_id, enc_notes in notes_by_enc.items():
        lines = []
        for note in enc_notes:
            desc = (note.get("description") or "").strip()
            if not desc:
                continue
            note_date = note.get("note_date")
            prefix = f"{note_date} " if note_date else ""
            lines.append(f"{prefix}{desc}")
            if note_date:
                note_text_by_date.setdefault(note_date, []).append(f"{note_date} {desc}")
        if lines:
            note_text_by_enc[enc_id] = "\n".join(lines)
    for note_date, lines in note_text_by_date.items():
        note_text_by_date[note_date] = "\n".join(lines)
    logger.info("patient notes pid=%s notes=%s encounters=%s", pid, len(notes), len(encounters))

    services = await _services_for_provider(session, provider)
    ziffernkette_options = await _load_ziffernkette_options(session, provider)
    open_entries = await _load_open_entries(session, tx_customer_id, provider_id)
    draft_invoices = await fetch_all(
        session,
        """
        SELECT inv.id, inv.created_at, inv.invoice_number, inv.status,
               COALESCE(SUM(il.gesamtpreis), 0) AS total
        FROM invoice.invoices inv
        LEFT JOIN invoice.invoice_lines il ON il.invoice_id = inv.id
        WHERE inv.customer_id = :cid
          AND inv.provider_id = :pid
          AND inv.status = 'draft'
        GROUP BY inv.id
        ORDER BY inv.created_at DESC
        """,
        {"cid": tx_customer_id, "pid": provider_id},
    )
    encounter_entries_rows = await _load_service_entries(session, tx_customer_id, provider_id)
    encounter_entries = {}
    for row in encounter_entries_rows:
        encounter_entries.setdefault(row.get("external_encounter_id"), []).append(row)
    encounter_totals = {}
    encounter_day_totals = {}
    none_entries = encounter_entries.get(None, [])
    for enc in encounters:
        entries = encounter_entries.get(enc["id"], []) + none_entries
        day_totals, encounter_total = _calculate_entry_totals(entries)
        encounter_totals[enc["id"]] = encounter_total
        encounter_day_totals[enc["id"]] = day_totals

    return templates.TemplateResponse(
        "patient_detail.html",
        {
            "request": request,
            "patient": patient,
            "diagnoses": diagnoses,
            "encounters": encounters,
            "notes_by_enc": notes_by_enc,
            "services": services,
            "ziffernkette_options": ziffernkette_options,
            "open_entries": open_entries,
            "draft_invoices": draft_invoices,
            "provider_code": provider,
            "customer_id": tx_customer_id,
            "provider_id": provider_id,
            "encounter_entries": encounter_entries,
            "encounter_totals": encounter_totals,
            "encounter_day_totals": encounter_day_totals,
            "note_text_by_date": note_text_by_date,
        },
    )


@router.post("/patient/{pid}/service-entry", response_class=HTMLResponse)
async def ui_add_service_entry(
    request: Request,
    pid: int,
    provider_code: str = Form(...),
    service_id: int = Form(...),
    leistungsdatum: str = Form(...),
    menge: str = Form("1.00"),
    faktor: str = Form("1.00"),
    kommentar: str = Form(None),
    external_encounter_id: Optional[int] = Form(None),
    session: AsyncSession = Depends(get_session),
):
    customer_id = None
    provider_id = None
    try:
        customer_id = await get_or_create_customer_for_openemr_pid(session, pid)
        provider_id = await _provider_id(session, provider_code)
        # normalize types
        try:
            leistungsdatum_date = dtdate.fromisoformat(leistungsdatum)
            menge_dec = Decimal(str(menge))
            faktor_dec = Decimal(str(faktor))
        except (ValueError, InvalidOperation):
            return HTMLResponse(status_code=400, content="invalid input")
        await execute(
            session,
            """
            INSERT INTO invoice.service_entries (
                customer_id, provider_id, service_id, external_encounter_id,
                leistungsdatum, menge, faktor, kommentar, status
            ) VALUES (
                :customer_id, :provider_id, :service_id, :external_encounter_id,
                :leistungsdatum, :menge, :faktor, :kommentar, 'open'
            )
            """,
            {
                "customer_id": customer_id,
                "provider_id": provider_id,
                "service_id": service_id,
                "external_encounter_id": external_encounter_id,
                "leistungsdatum": leistungsdatum_date,
                "menge": menge_dec,
                "faktor": faktor_dec,
                "kommentar": kommentar,
            },
        )
        await session.commit()
        logger.info(
            "service entry added pid=%s customer_id=%s provider=%s service=%s date=%s menge=%s faktor=%s",
            pid,
            customer_id,
            provider_id,
            service_id,
            leistungsdatum_date,
            menge_dec,
            faktor_dec,
        )
        open_entries = await _load_open_entries(session, customer_id, provider_id)
        entries, day_totals, encounter_total = await _load_encounter_context_entries(
            session, customer_id, provider_id, external_encounter_id
        )
        return templates.TemplateResponse(
            "_encounter_entries_response.html",
            {
                "request": request,
                "entries": entries,
                "encounter_id": external_encounter_id,
                "day_totals": day_totals,
                "encounter_total": encounter_total,
                "open_entries": open_entries,
                "pid": pid,
                "provider_code": provider_code,
            },
        )
    except HTTPException as exc:
        logger.warning("add service entry failed pid=%s code=%s: %s", pid, provider_code, exc.detail)
        await session.rollback()
        if customer_id and provider_id:
            open_entries = await _load_open_entries(session, customer_id, provider_id)
            entries, day_totals, encounter_total = await _load_encounter_context_entries(
                session, customer_id, provider_id, external_encounter_id
            )
            return templates.TemplateResponse(
                "_encounter_entries_response.html",
                {
                    "request": request,
                    "entries": entries,
                    "encounter_id": external_encounter_id,
                    "day_totals": day_totals,
                    "encounter_total": encounter_total,
                    "open_entries": open_entries,
                    "pid": pid,
                    "provider_code": provider_code,
                    "error": exc.detail,
                },
                status_code=200,
            )
        return HTMLResponse(status_code=exc.status_code, content=exc.detail)
    except Exception as exc:
        logger.exception("add service entry unexpected error pid=%s code=%s", pid, provider_code)
        await session.rollback()
        if customer_id and provider_id:
            open_entries = await _load_open_entries(session, customer_id, provider_id)
            entries, day_totals, encounter_total = await _load_encounter_context_entries(
                session, customer_id, provider_id, external_encounter_id
            )
            return templates.TemplateResponse(
                "_encounter_entries_response.html",
                {
                    "request": request,
                    "entries": entries,
                    "encounter_id": external_encounter_id,
                    "day_totals": day_totals,
                    "encounter_total": encounter_total,
                    "open_entries": open_entries,
                    "pid": pid,
                    "provider_code": provider_code,
                    "error": "unexpected error",
                },
                status_code=200,
            )
        return HTMLResponse(status_code=500, content="error")


@router.post("/patient/{pid}/ziffernkette", response_class=HTMLResponse)
async def ui_add_ziffernkette_entries(
    request: Request,
    pid: int,
    provider_code: str = Form(...),
    ziffernkette_kuerzel: str = Form(...),
    leistungsdatum: str = Form(...),
    external_encounter_id: Optional[int] = Form(None),
    session: AsyncSession = Depends(get_session),
):
    if provider_code not in {"PP", "TI", "MS"}:
        return HTMLResponse(status_code=400, content="invalid provider")
    if not ziffernkette_kuerzel:
        return HTMLResponse(status_code=400, content="missing ziffernkette")
    try:
        leistungsdatum_date = dtdate.fromisoformat(leistungsdatum)
    except ValueError:
        return HTMLResponse(status_code=400, content="invalid date")

    customer_id = None
    provider_id = None
    try:
        customer_id = await get_or_create_customer_for_openemr_pid(session, pid)
        provider_id = await _provider_id(session, provider_code)
        ziffern_rows = await fetch_all(
            session,
            """
            SELECT uid, ziffer, faktor, begruendung, beschreibung, anzahl, reihenfolge
            FROM invoice.ziffernkette
            WHERE kuerzel = :kuerzel
              AND rechnungskuertzel = :provider_code
            ORDER BY uid ASC
            """,
            {"kuerzel": ziffernkette_kuerzel, "provider_code": provider_code},
        )
        if not ziffern_rows:
            entries, day_totals, encounter_total = await _load_encounter_context_entries(
                session, customer_id, provider_id, external_encounter_id
            )
            open_entries = await _load_open_entries(session, customer_id, provider_id)
            return templates.TemplateResponse(
                "_encounter_entries_response.html",
                {
                    "request": request,
                    "entries": entries,
                    "encounter_id": external_encounter_id,
                    "pid": pid,
                    "day_totals": day_totals,
                    "encounter_total": encounter_total,
                    "open_entries": open_entries,
                },
            )

        for row in ziffern_rows:
            nummer = (row.get("ziffer") or "").strip()
            if not nummer:
                continue
            service = await fetch_one(
                session,
                """
                SELECT id, standard_faktor, standard_menge, kommentar_template, beschreibung
                FROM invoice.services_master
                WHERE nummer = :nummer AND rechnungskuertzel = :provider_code
                ORDER BY id ASC
                LIMIT 1
                """,
                {"nummer": nummer, "provider_code": provider_code},
            )
            if not service:
                logger.warning("ziffernkette service not found kuerzel=%s nummer=%s", ziffernkette_kuerzel, nummer)
                continue
            kommentar = row.get("begruendung")
            if kommentar is not None:
                kommentar = kommentar.strip()
            if kommentar == "":
                kommentar = None
            if kommentar is None:
                kommentar = service.get("kommentar_template")

            faktor_override = row.get("faktor")
            menge_override = row.get("anzahl")
            if faktor_override is None or _decimal_or_zero(faktor_override) == Decimal("0"):
                faktor_override = service.get("standard_faktor")
            if menge_override is None or _decimal_or_zero(menge_override) == Decimal("0"):
                menge_override = service.get("standard_menge")
            faktor_val = _decimal_or_zero(faktor_override)
            menge_val = _decimal_or_zero(menge_override)

            await execute(
                session,
                """
                INSERT INTO invoice.service_entries (
                    customer_id, provider_id, service_id, external_encounter_id,
                    leistungsdatum, menge, faktor, kommentar, status
                ) VALUES (
                    :customer_id, :provider_id, :service_id, :external_encounter_id,
                    :leistungsdatum, :menge, :faktor, :kommentar, 'open'
                )
                """,
                {
                    "customer_id": customer_id,
                    "provider_id": provider_id,
                    "service_id": service["id"],
                    "external_encounter_id": external_encounter_id,
                    "leistungsdatum": leistungsdatum_date,
                    "menge": menge_val,
                    "faktor": faktor_val,
                    "kommentar": kommentar,
                },
            )
        await session.commit()
    except Exception:
        logger.exception("failed to add ziffernkette entries pid=%s", pid)
        await session.rollback()
        return HTMLResponse(status_code=500, content="failed to add ziffernkette entries")

    entries, day_totals, encounter_total = await _load_encounter_context_entries(
        session, customer_id, provider_id, external_encounter_id
    )
    open_entries = await _load_open_entries(session, customer_id, provider_id)
    return templates.TemplateResponse(
        "_encounter_entries_response.html",
        {
            "request": request,
            "entries": entries,
            "encounter_id": external_encounter_id,
            "pid": pid,
            "day_totals": day_totals,
            "encounter_total": encounter_total,
            "open_entries": open_entries,
        },
    )


@router.get("/service-entry/{entry_id}/edit", response_class=HTMLResponse)
async def ui_edit_service_entry(
    entry_id: int,
    request: Request,
    encounter_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
):
    entry = await fetch_one(
        session,
        """
        SELECT se.*, sm.nummer, sm.beschreibung, sm.standard_einzelpreis,
               (se.menge * se.faktor * sm.standard_einzelpreis) AS line_total,
               p.code AS provider_code
        FROM invoice.service_entries se
        JOIN invoice.services_master sm ON sm.id = se.service_id
        JOIN invoice.providers p ON p.id = se.provider_id
        WHERE se.id = :id
        """,
        {"id": entry_id},
    )
    if not entry:
        return HTMLResponse(status_code=404, content="Not found")
    if entry["status"] != "open":
        return HTMLResponse(status_code=409, content="not editable")
    encounter_context_id = encounter_id if encounter_id is not None else entry.get("external_encounter_id")
    return templates.TemplateResponse(
        "_encounter_entry_edit_row.html",
        {
            "request": request,
            "e": entry,
            "encounter_id": encounter_context_id,
            "provider_code": entry.get("provider_code"),
        },
    )


@router.get("/service-entry/{entry_id}/row", response_class=HTMLResponse)
async def ui_service_entry_row(
    entry_id: int,
    request: Request,
    encounter_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
):
    entry = await fetch_one(
        session,
        """
        SELECT se.*, sm.nummer, sm.beschreibung, sm.standard_einzelpreis,
               (se.menge * se.faktor * sm.standard_einzelpreis) AS line_total,
               p.code AS provider_code
        FROM invoice.service_entries se
        JOIN invoice.services_master sm ON sm.id = se.service_id
        JOIN invoice.providers p ON p.id = se.provider_id
        WHERE se.id = :id
        """,
        {"id": entry_id},
    )
    if not entry:
        return HTMLResponse(status_code=404, content="Not found")
    encounter_context_id = encounter_id if encounter_id is not None else entry.get("external_encounter_id")
    return templates.TemplateResponse(
        "_encounter_entry_row.html",
        {
            "request": request,
            "e": entry,
            "encounter_id": encounter_context_id,
            "provider_code": entry.get("provider_code"),
            "pid": entry.get("customer_id"),
        },
    )


@router.post("/service-entry/{entry_id}/edit", response_class=HTMLResponse)
async def ui_edit_service_entry_save(
    entry_id: int,
    request: Request,
    leistungsdatum: str = Form(...),
    menge: str = Form("1.00"),
    faktor: str = Form("1.00"),
    kommentar: str = Form(None),
    encounter_id: Optional[int] = Form(None),
    session: AsyncSession = Depends(get_session),
):
    try:
        entry = await fetch_one(
            session,
            """
            SELECT id, status, customer_id, provider_id, external_encounter_id
            FROM invoice.service_entries
            WHERE id = :id
            """,
            {"id": entry_id},
        )
        if not entry:
            return HTMLResponse(status_code=404, content="Not found")
        if entry["status"] != "open":
            return HTMLResponse(status_code=409, content="not editable")
        leistungsdatum_date = dtdate.fromisoformat(leistungsdatum)
        menge_dec = Decimal(str(menge))
        faktor_dec = Decimal(str(faktor))
        await execute(
            session,
            """
            UPDATE invoice.service_entries
            SET leistungsdatum = :leistungsdatum,
                menge = :menge,
                faktor = :faktor,
                kommentar = :kommentar
            WHERE id = :id
            """,
            {
                "id": entry_id,
                "leistungsdatum": leistungsdatum_date,
                "menge": menge_dec,
                "faktor": faktor_dec,
                "kommentar": kommentar,
            },
        )
        await session.commit()
        encounter_context_id = encounter_id if encounter_id is not None else entry.get("external_encounter_id")
        entries, day_totals, encounter_total = await _load_encounter_context_entries(
            session, entry["customer_id"], entry["provider_id"], encounter_context_id
        )
        open_entries = await _load_open_entries(session, entry["customer_id"], entry["provider_id"])
        prov = await fetch_one(
            session, "SELECT code FROM invoice.providers WHERE id = :pid", {"pid": entry["provider_id"]}
        ) or {}
        provider_code = prov.get("code")
        return templates.TemplateResponse(
            "_encounter_entries_response.html",
            {
                "request": request,
                "entries": entries,
                "encounter_id": encounter_context_id,
                "day_totals": day_totals,
                "encounter_total": encounter_total,
                "open_entries": open_entries,
                "pid": entry.get("customer_id"),
                "provider_code": provider_code,
            },
        )
    except HTTPException as exc:
        await session.rollback()
        return HTMLResponse(status_code=exc.status_code, content=exc.detail)
    except Exception:
        logger.exception("edit service entry failed id=%s", entry_id)
        await session.rollback()
        return HTMLResponse(status_code=500, content="error")


@router.post("/service-entry/{entry_id}/delete", response_class=HTMLResponse)
async def ui_delete_service_entry(
    entry_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    try:
        form = await request.form()
        customer_view = form.get("customer_view") == "1"
        form_encounter_id = form.get("encounter_id")
        encounter_context_id = int(form_encounter_id) if form_encounter_id not in (None, "") else None
        # load entry
        entry = await fetch_one(
            session,
            """
            SELECT id, status, customer_id, provider_id, external_encounter_id
            FROM invoice.service_entries
            WHERE id = :id
            """,
            {"id": entry_id},
        )
        if not entry:
            logger.warning("delete service entry not found id=%s", entry_id)
            return HTMLResponse(status_code=404, content="Not found")
        if entry["status"] != "open":
            logger.warning("delete service entry not editable id=%s status=%s", entry_id, entry["status"])
            return HTMLResponse(status_code=409, content="not editable")

        await execute(session, "DELETE FROM invoice.service_entries WHERE id = :id", {"id": entry_id})
        await session.commit()
        logger.info("delete service entry deleted id=%s", entry_id)

        # reload list (match same encounter_id if provided, else all open for customer/provider)
        enc_id = entry.get("external_encounter_id")
        if customer_view:
            entries = await fetch_all(
                session,
                """
                SELECT se.*, sm.nummer, sm.beschreibung
                FROM invoice.service_entries se
                JOIN invoice.services_master sm ON sm.id = se.service_id
                WHERE se.customer_id = :cid AND se.provider_id = :pid
                  AND se.status = 'open'
                ORDER BY se.created_at DESC
                """,
                {"cid": entry["customer_id"], "pid": entry["provider_id"]},
            )
        else:
            encounter_context_id = encounter_context_id if encounter_context_id is not None else enc_id
            entries, day_totals, encounter_total = await _load_encounter_context_entries(
                session, entry["customer_id"], entry["provider_id"], encounter_context_id
            )

        prov = await fetch_one(session, "SELECT code FROM invoice.providers WHERE id = :pid", {"pid": entry["provider_id"]}) or {}
        provider_code = prov.get("code")

        context = {
            "request": request,
            "entries": entries,
            "pid": entry.get("customer_id"),
            "provider_code": provider_code,
            "encounter_id": encounter_context_id,
            "day_totals": day_totals if not customer_view else [],
            "encounter_total": encounter_total if not customer_view else Decimal("0"),
        }
        logger.info(
            "delete service entry render entries_count=%s cid=%s pid=%s enc=%s",
            len(entries),
            entry.get("customer_id"),
            entry.get("provider_id"),
            encounter_context_id,
        )
        if customer_view:
            return templates.TemplateResponse(
                "_open_entries_customer.html",
                {"request": request, "entries": entries, "customer_id": entry["customer_id"], "provider_code": provider_code},
            )
        open_entries = await _load_open_entries(session, entry["customer_id"], entry["provider_id"])
        context["open_entries"] = open_entries
        return templates.TemplateResponse("_encounter_entries_response.html", context)
    except HTTPException as exc:
        await session.rollback()
        logger.warning("delete service entry http error id=%s status=%s detail=%s", entry_id, exc.status_code, exc.detail)
        return HTMLResponse(status_code=exc.status_code, content=exc.detail)
    except Exception:
        logger.exception("delete service entry failed id=%s", entry_id)
        await session.rollback()
        return HTMLResponse(status_code=500, content="error")


@router.post("/patient/{pid}/create-invoice")
async def ui_create_invoice(pid: int, provider_code: str = Form(...), session: AsyncSession = Depends(get_session)):
    customer_id = await get_or_create_customer_for_openemr_pid(session, pid)
    params = {"provider_id": await _provider_id(session, provider_code), "customer_id": customer_id}
    def _default_rc(code: str) -> str:
        if code == "PP":
            return "E"
        if code == "TI":
            return "S"
        if code == "MS":
            return "E"
        return "S"
    entries = await fetch_all(
        session,
        """
        SELECT * FROM invoice.service_entries
        WHERE provider_id = :provider_id AND customer_id = :customer_id AND status = 'open'
        ORDER BY leistungsdatum ASC, id ASC
        """,
        params,
    )
    if not entries:
        logger.warning("no open entries for pid=%s provider=%s", pid, provider_code)
        return HTMLResponse(status_code=400, content="Keine offenen Leistungen für diesen Provider/Patienten.")

    # re-use API workflow logic via direct DB (no nested begin)
    try:
        invoice_id = await execute_returning_id(
            session,
            """
            INSERT INTO invoice.invoices (provider_id, customer_id, status, rechnungs_code)
            VALUES (:provider_id, :customer_id, 'draft', :rc)
            RETURNING id
            """,
            {**params, "rc": _default_rc(provider_code)},
        )
        pos = 1
        for e in entries:
            svc = await fetch_one(
                session,
                """
                SELECT id, nummer, beschreibung, kommentar_template, mwst_satz,
                       requires_diagnosis, COALESCE(standard_einzelpreis, 0) AS standard_einzelpreis
                FROM invoice.services_master WHERE id = :sid
                """,
                {"sid": e["service_id"]},
            )
            if not svc:
                raise HTTPException(status_code=404, detail=f"service {e['service_id']} not found")
            einzelpreis = Decimal(svc["standard_einzelpreis"])
            menge = Decimal(e["menge"])
            faktor = Decimal(e["faktor"])
            gesamtpreis = (menge * faktor * einzelpreis).quantize(Decimal("0.01"))
            snapshot = {
                "service_entry_id": e.get("id"),
                "service_id": svc["id"],
                "nummer": svc["nummer"],
                "beschreibung": svc["beschreibung"],
                "kommentar_template": svc["kommentar_template"],
                "kommentar": e.get("kommentar"),
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
                    "position_no": pos,
                    "service_snapshot": json.dumps(snapshot),
                    "leistungsdatum": e["leistungsdatum"],
                    "menge": menge,
                    "faktor": faktor,
                    "mwst_satz": Decimal(svc.get("mwst_satz") or 0).quantize(Decimal("0.01")),
                    "einzelpreis": einzelpreis,
                    "gesamtpreis": gesamtpreis,
                },
            )
            pos += 1
        ids = [e["id"] for e in entries]
        await execute(
            session,
            "UPDATE invoice.service_entries SET status = 'invoiced' WHERE id = ANY(:ids)",
            {"ids": ids},
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return RedirectResponse(url=f"/ui/invoice/{invoice_id}", status_code=303)


@router.get("/customer/{customer_id}", response_class=HTMLResponse)
async def ui_customer_detail(request: Request, customer_id: int, provider_code: str = "TI", session: AsyncSession = Depends(get_session)):
    if provider_code not in {"PP", "TI", "MS"}:
        provider_code = "TI"
    customer = await fetch_one(session, "SELECT * FROM invoice.customers WHERE id = :id", {"id": customer_id})
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")
    provider_id = await _provider_id(session, provider_code)
    services = await _services_for_provider(session, provider_code)
    open_entries = await fetch_all(
        session,
        """
        SELECT se.*, sm.nummer, sm.beschreibung, p.code AS provider_code
        FROM invoice.service_entries se
        LEFT JOIN invoice.services_master sm ON sm.id = se.service_id
        LEFT JOIN invoice.providers p ON p.id = se.provider_id
        WHERE se.customer_id = :cid
          AND se.provider_id = :pid
          AND btrim(se.status) = 'open'
        ORDER BY se.leistungsdatum DESC, se.created_at DESC
        """,
        {"cid": customer_id, "pid": provider_id},
    )
    all_open_entries = await fetch_all(
        session,
        """
        SELECT se.*, sm.nummer, sm.beschreibung, p.code AS provider_code
        FROM invoice.service_entries se
        LEFT JOIN invoice.services_master sm ON sm.id = se.service_id
        LEFT JOIN invoice.providers p ON p.id = se.provider_id
        WHERE se.customer_id = :cid
          AND btrim(se.status) = 'open'
        ORDER BY se.leistungsdatum DESC, se.created_at DESC
        """,
        {"cid": customer_id},
    )
    logger.info(
        "customer %s provider %s open_entries=%s all_open=%s ids_filtered=%s ids_all=%s",
        customer_id,
        provider_code,
        len(open_entries),
        len(all_open_entries),
        [e.get("id") for e in open_entries],
        [e.get("id") for e in all_open_entries],
    )
    return templates.TemplateResponse(
        "customer_detail.html",
        {
            "request": request,
            "customer": customer,
            "services": services,
            "provider_code": provider_code,
            "provider_id": provider_id,
            "open_entries": open_entries,
            "all_open_entries": all_open_entries,
            "today": dtdate.today().isoformat(),
        },
    )


@router.get("/customer/{customer_id}/open-entries", response_class=HTMLResponse)
async def ui_customer_open_entries(request: Request, customer_id: int, provider_code: str, session: AsyncSession = Depends(get_session)):
    provider_id = await _provider_id(session, provider_code)
    entries = await fetch_all(
        session,
        """
        SELECT se.*, sm.nummer, sm.beschreibung, p.code AS provider_code
        FROM invoice.service_entries se
        LEFT JOIN invoice.services_master sm ON sm.id = se.service_id
        LEFT JOIN invoice.providers p ON p.id = se.provider_id
        WHERE se.customer_id = :cid
          AND se.provider_id = :pid
          AND btrim(se.status) = 'open'
        ORDER BY se.leistungsdatum DESC, se.created_at DESC
        """,
        {"cid": customer_id, "pid": provider_id},
    )
    return templates.TemplateResponse(
        "_open_entries_customer.html",
        {"request": request, "entries": entries, "customer_id": customer_id, "provider_code": provider_code},
    )


@router.post("/customer/{customer_id}/service-entry", response_class=HTMLResponse)
async def ui_customer_service_entry(
    request: Request,
    customer_id: int,
    provider_code: str = Form(...),
    service_id: int = Form(...),
    leistungsdatum: str = Form(...),
    menge: str = Form("1.00"),
    faktor: str = Form("1.00"),
    kommentar: str = Form(None),
    session: AsyncSession = Depends(get_session),
):
    try:
        provider_id = await _provider_id(session, provider_code)
        leistungsdatum_date = dtdate.fromisoformat(leistungsdatum)
        try:
            menge_dec = Decimal(str(menge or "1"))
            faktor_dec = Decimal(str(faktor or "1"))
        except InvalidOperation:
            logger.warning("invalid menge/faktor input customer_id=%s provider=%s menge=%s faktor=%s", customer_id, provider_code, menge, faktor)
            return templates.TemplateResponse(
                "_open_entries_customer.html",
                {
                    "request": request,
                    "entries": [],
                    "customer_id": customer_id,
                    "provider_code": provider_code,
                    "error": "Ungültige Menge/Faktor",
                },
                status_code=200,
            )
        await execute(
            session,
            """
            INSERT INTO invoice.service_entries (
                customer_id, provider_id, service_id, external_encounter_id,
                leistungsdatum, menge, faktor, kommentar, status
            ) VALUES (
                :customer_id, :provider_id, :service_id, NULL,
                :leistungsdatum, :menge, :faktor, :kommentar, 'open'
            )
            """,
            {
                "customer_id": customer_id,
                "provider_id": provider_id,
                "service_id": service_id,
                "leistungsdatum": leistungsdatum_date,
                "menge": menge_dec,
                "faktor": faktor_dec,
                "kommentar": kommentar,
            },
        )
        await session.commit()
        entries = await fetch_all(
            session,
            """
            SELECT se.*, sm.nummer, sm.beschreibung
            FROM invoice.service_entries se
            JOIN invoice.services_master sm ON sm.id = se.service_id
            WHERE se.customer_id = :cid
              AND se.provider_id = :pid
              AND se.status = 'open'
            ORDER BY se.leistungsdatum DESC, se.created_at DESC
            """,
            {"cid": customer_id, "pid": provider_id},
        )
        return templates.TemplateResponse(
            "_open_entries_customer.html",
            {"request": request, "entries": entries, "customer_id": customer_id, "provider_code": provider_code},
        )
    except Exception:
        logger.exception("failed to add service entry for customer_id=%s provider=%s", customer_id, provider_code)
        await session.rollback()
        return templates.TemplateResponse(
            "_open_entries_customer.html",
            {
                "request": request,
                "entries": [],
                "customer_id": customer_id,
                "provider_code": provider_code,
                "error": "Fehler beim Speichern",
            },
            status_code=200,
        )


@router.post("/customer/{customer_id}/create-invoice")
async def ui_customer_create_invoice(customer_id: int, provider_code: str = Form(...), session: AsyncSession = Depends(get_session)):
    provider_id = await _provider_id(session, provider_code)
    def _default_rc(code: str) -> str:
        if code == "PP":
            return "E"
        if code == "TI":
            return "S"
        if code == "MS":
            return "E"
        return "S"
    entries = await fetch_all(
        session,
        """
        SELECT * FROM invoice.service_entries
        WHERE provider_id = :provider_id AND customer_id = :customer_id AND status = 'open'
        ORDER BY leistungsdatum ASC, id ASC
        """,
        {"provider_id": provider_id, "customer_id": customer_id},
    )
    if not entries:
        return HTMLResponse(status_code=400, content="Keine offenen Leistungen.")
    try:
        invoice_id = await execute_returning_id(
            session,
            """
            INSERT INTO invoice.invoices (provider_id, customer_id, status, rechnungs_code)
            VALUES (:provider_id, :customer_id, 'draft', :rc)
            RETURNING id
            """,
            {"provider_id": provider_id, "customer_id": customer_id, "rc": _default_rc(provider_code)},
        )
        pos = 1
        for e in entries:
            svc = await fetch_one(
                session,
                """
                SELECT id, nummer, beschreibung, kommentar_template, mwst_satz,
                       requires_diagnosis, COALESCE(standard_einzelpreis, 0) AS standard_einzelpreis
                FROM invoice.services_master WHERE id = :sid
                """,
                {"sid": e["service_id"]},
            )
            if not svc:
                raise HTTPException(status_code=404, detail=f"service {e['service_id']} not found")
            einzelpreis = Decimal(svc["standard_einzelpreis"])
            menge_val = Decimal(e["menge"])
            faktor_val = Decimal(e["faktor"])
            gesamtpreis = (menge_val * faktor_val * einzelpreis).quantize(Decimal("0.01"))
            snapshot = {
                "service_id": svc["id"],
                "nummer": svc["nummer"],
                "beschreibung": svc["beschreibung"],
                "kommentar_template": svc["kommentar_template"],
                "kommentar": e.get("kommentar"),
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
                    "position_no": pos,
                    "service_snapshot": json.dumps(snapshot),
                    "leistungsdatum": e["leistungsdatum"],
                    "menge": menge_val,
                    "faktor": faktor_val,
                    "mwst_satz": Decimal(svc.get("mwst_satz") or 0).quantize(Decimal("0.01")),
                    "einzelpreis": einzelpreis,
                    "gesamtpreis": gesamtpreis,
                },
            )
            pos += 1
        ids = [e["id"] for e in entries]
        await execute(
            session,
            "UPDATE invoice.service_entries SET status = 'invoiced' WHERE id = ANY(:ids)",
            {"ids": ids},
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return RedirectResponse(url=f"/ui/invoice/{invoice_id}", status_code=303)


@router.get("/invoice/{invoice_id}", response_class=HTMLResponse)
async def ui_invoice(request: Request, invoice_id: int, session: AsyncSession = Depends(get_session)):
    inv = await fetch_one(
        session,
        """
        SELECT i.*,
               vc.meaning AS rechnungs_code_meaning,
               vc.tax_logic AS rechnungs_code_tax_logic,
               vc.comment AS rechnungs_code_comment,
               itc.meaning AS invoice_type_meaning
        FROM invoice.invoices i
        LEFT JOIN invoice.vat_category vc ON vc.code = i.rechnungs_code
        LEFT JOIN invoice.invoice_type_code itc ON itc.code = i.invoice_type_code
        WHERE i.id = :id
        """,
        {"id": invoice_id},
    )
    if not inv:
        raise HTTPException(status_code=404, detail="invoice not found")
    lines = await fetch_all(
        session,
        "SELECT * FROM invoice.invoice_lines WHERE invoice_id = :id ORDER BY position_no",
        {"id": invoice_id},
    )
    provider = await fetch_one(
        session,
        """
        SELECT id, code, firma, vorname, nachname, vat_id, tax_id, iban, bic
        FROM invoice.providers WHERE id = :pid
        """,
        {"pid": inv["provider_id"]},
    )
    customer = await fetch_one(
        session,
        """
        SELECT id, firma, vorname, nachname, vat_id, tax_id
        FROM invoice.customers WHERE id = :cid
        """,
        {"cid": inv["customer_id"]},
    )
    diagnoses = await fetch_all(
        session,
        """
        SELECT title, icd_code, begdate, enddate
        FROM invoice.invoice_diagnoses
        WHERE invoice_id = :id
        ORDER BY id
        """,
        {"id": invoice_id},
    )
    # Fallback: nur PP, nur wenn keine Rechnungsdiagnosen vorhanden, und Customer ist OpenEMR
    if (not diagnoses) and provider and provider.get("code") == "PP":
        cust_ext_id = customer.get("external_id")
        cust_ext_sys = customer.get("external_system")
        if cust_ext_sys == "openemr" and cust_ext_id:
            diagnoses = await fetch_all(
                session,
                """
                SELECT title, diagnosis AS icd_code, begdate, enddate
                FROM openemr.problems
                WHERE pid = :pid AND (enddate IS NULL OR enddate >= current_date)
                ORDER BY begdate DESC NULLS LAST, id DESC
                """,
                {"pid": cust_ext_id},
            )
    vat_categories = await fetch_all(session, "SELECT code, meaning FROM invoice.vat_category ORDER BY code")
    open_reopened = await fetch_all(
        session,
        """
        SELECT se.*, sm.nummer, sm.beschreibung
        FROM invoice.service_entries se
        JOIN invoice.services_master sm ON sm.id = se.service_id
        WHERE se.customer_id = :cid AND se.provider_id = :pid AND se.status = 'open'
        ORDER BY se.leistungsdatum DESC, se.created_at DESC
        """,
        {"cid": inv["customer_id"], "pid": inv["provider_id"]},
    )
    credit_ref = None
    credit_child = None
    try:
        credit_child = await fetch_one(
            session,
            "SELECT id, invoice_number FROM invoice.invoices WHERE cancellation_of_invoice_id = :id ORDER BY id DESC LIMIT 1",
            {"id": invoice_id},
        )
    except ProgrammingError:
        credit_child = None
    if inv.get("cancellation_of_invoice_id"):
        ref = await fetch_one(
            session,
            "SELECT id, invoice_number FROM invoice.invoices WHERE id = :rid",
            {"rid": inv["cancellation_of_invoice_id"]},
        )
        if ref:
            credit_ref = ref
    total_net = sum([line.get("gesamtpreis", 0) or 0 for line in lines]) if lines else 0
    inv["draft_totals"] = total_net
    return templates.TemplateResponse(
        "invoice_detail.html",
        {
            "request": request,
            "invoice": inv,
            "lines": lines,
            "credit_child": credit_child,
            "credit_ref": credit_ref,
            "open_reopened": open_reopened,
            "provider_code": provider.get("code") if provider else None,
            "provider": provider or {},
            "customer": customer or {},
            "vat_categories": vat_categories,
            "diagnoses": diagnoses,
        },
    )


@router.get("/customers", response_class=HTMLResponse)
async def ui_customers(
    request: Request,
    firma: Optional[str] = None,
    nachname: Optional[str] = None,
    ort: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    filters = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if firma:
        filters.append("c.firma ILIKE :firma")
        params["firma"] = f"%{firma}%"
    if nachname:
        filters.append("c.nachname ILIKE :nachname")
        params["nachname"] = f"%{nachname}%"
    if ort:
        filters.append("c.ort ILIKE :ort")
        params["ort"] = f"%{ort}%"
    where_clause = " AND ".join(filters)
    if where_clause:
        where_clause = "WHERE " + where_clause

    customers = await fetch_all(
        session,
        f"""
        SELECT c.id, c.firma, c.vorname, c.nachname, c.strasse, c.plz, c.ort, c.land,
               c.vat_id, c.tax_id, c.external_id
        FROM invoice.customers c
        {where_clause}
        ORDER BY c.firma NULLS LAST, c.nachname NULLS LAST, c.id DESC
        LIMIT :limit OFFSET :offset
        """,
        params,
    )

    return templates.TemplateResponse(
        "customers_overview.html",
        {
            "request": request,
            "customers": customers,
            "firma": firma or "",
            "nachname": nachname or "",
            "ort": ort or "",
        },
    )


async def _payment_terms(session: AsyncSession):
    return await fetch_all(
        session,
        """
        SELECT key, text
        FROM invoice.payment_terms
        ORDER BY key
        """,
    )


@router.get("/payment-terms", response_class=HTMLResponse)
async def ui_payment_terms(request: Request, session: AsyncSession = Depends(get_session)):
    payment_terms = await _payment_terms(session)
    return templates.TemplateResponse(
        "payment_terms_overview.html",
        {"request": request, "payment_terms": payment_terms},
    )


@router.get("/payment-terms/new", response_class=HTMLResponse)
async def ui_payment_term_new(request: Request):
    term = {"key": "", "text": ""}
    return templates.TemplateResponse(
        "payment_term_edit.html",
        {"request": request, "term": term, "is_new": True},
    )


@router.post("/payment-terms/new", response_class=HTMLResponse)
async def ui_payment_term_create(
    request: Request,
    key: Optional[str] = Form(None),
    text: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_session),
):
    key_clean = (key or "").strip().upper()
    text_clean = (text or "").strip()
    term = {"key": key_clean, "text": text_clean}
    if not key_clean or not text_clean:
        return templates.TemplateResponse(
            "payment_term_edit.html",
            {"request": request, "term": term, "is_new": True, "error": "Key und Text sind Pflichtfelder."},
            status_code=400,
        )
    try:
        await execute(
            session,
            """
            INSERT INTO invoice.payment_terms (key, text)
            VALUES (:key, :text)
            """,
            {"key": key_clean, "text": text_clean},
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return templates.TemplateResponse(
            "payment_term_edit.html",
            {
                "request": request,
                "term": term,
                "is_new": True,
                "error": "Diese Zahlungsbedingung existiert bereits.",
            },
            status_code=409,
        )
    return RedirectResponse(url="/ui/payment-terms", status_code=303)


@router.get("/payment-terms/{term_key}/edit", response_class=HTMLResponse)
async def ui_payment_term_edit(request: Request, term_key: str, session: AsyncSession = Depends(get_session)):
    term = await fetch_one(session, "SELECT key, text FROM invoice.payment_terms WHERE key = :key", {"key": term_key})
    if not term:
        raise HTTPException(status_code=404, detail="payment term not found")
    return templates.TemplateResponse(
        "payment_term_edit.html",
        {"request": request, "term": term, "is_new": False},
    )


@router.post("/payment-terms/{term_key}/edit", response_class=HTMLResponse)
async def ui_payment_term_edit_save(
    request: Request,
    term_key: str,
    text: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_session),
):
    text_clean = (text or "").strip()
    term = {"key": term_key, "text": text_clean}
    if not text_clean:
        return templates.TemplateResponse(
            "payment_term_edit.html",
            {"request": request, "term": term, "is_new": False, "error": "Text ist ein Pflichtfeld."},
            status_code=400,
        )
    existing = await fetch_one(session, "SELECT key FROM invoice.payment_terms WHERE key = :key", {"key": term_key})
    if not existing:
        raise HTTPException(status_code=404, detail="payment term not found")
    await execute(
        session,
        """
        UPDATE invoice.payment_terms
        SET text = :text
        WHERE key = :key
        """,
        {"key": term_key, "text": text_clean},
    )
    await session.commit()
    return RedirectResponse(url="/ui/payment-terms", status_code=303)


@router.get("/services-master", response_class=HTMLResponse)
async def ui_services_master(
    request: Request,
    provider_code: Optional[str] = None,
    nummer: Optional[str] = None,
    beschreibung: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    filters = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if provider_code:
        filters.append("sm.rechnungskuertzel = :pc")
        params["pc"] = provider_code
    if nummer:
        filters.append("sm.nummer ILIKE :nummer")
        params["nummer"] = f"%{nummer}%"
    if beschreibung:
        filters.append("sm.beschreibung ILIKE :beschreibung")
        params["beschreibung"] = f"%{beschreibung}%"
    where_clause = " AND ".join(filters)
    if where_clause:
        where_clause = "WHERE " + where_clause

    services = await fetch_all(
        session,
        f"""
        SELECT sm.id, sm.nummer, sm.beschreibung, sm.standard_faktor, sm.standard_menge,
               sm.standard_einzelpreis, sm.kommentar_template, sm.mwst_satz, sm.rechnungskuertzel,
               sm.requires_diagnosis, sm.currency
        FROM invoice.services_master sm
        {where_clause}
        ORDER BY sm.rechnungskuertzel, sm.nummer, sm.id
        LIMIT :limit OFFSET :offset
        """,
        params,
    )
    return templates.TemplateResponse(
        "services_master_overview.html",
        {
            "request": request,
            "services": services,
            "provider_code": provider_code or "",
            "nummer": nummer or "",
            "beschreibung": beschreibung or "",
        },
    )


@router.get("/services-master/{service_id}/edit", response_class=HTMLResponse)
async def ui_service_master_edit(request: Request, service_id: int, session: AsyncSession = Depends(get_session)):
    svc = await fetch_one(session, "SELECT * FROM invoice.services_master WHERE id = :id", {"id": service_id})
    if not svc:
        raise HTTPException(status_code=404, detail="service not found")
    return templates.TemplateResponse("service_master_edit.html", {"request": request, "svc": svc, "is_new": False})


@router.get("/services-master/new", response_class=HTMLResponse)
async def ui_service_master_new(request: Request):
    svc = {
        "id": None,
        "rechnungskuertzel": "",
        "nummer": "",
        "beschreibung": "",
        "standard_menge": "1",
        "standard_faktor": "1",
        "standard_einzelpreis": "0",
        "mwst_satz": "0",
        "currency": "EUR",
        "requires_diagnosis": False,
        "kommentar_template": "",
    }
    return templates.TemplateResponse("service_master_edit.html", {"request": request, "svc": svc, "is_new": True})


@router.post("/services-master/{service_id}/edit", response_class=HTMLResponse)
async def ui_service_master_edit_save(
    request: Request,
    service_id: int,
    nummer: Optional[str] = Form(None),
    beschreibung: Optional[str] = Form(None),
    standard_faktor: Optional[str] = Form(None),
    standard_menge: Optional[str] = Form(None),
    standard_einzelpreis: Optional[str] = Form(None),
    kommentar_template: Optional[str] = Form(None),
    mwst_satz: Optional[str] = Form(None),
    rechnungskuertzel: Optional[str] = Form(None),
    requires_diagnosis: Optional[str] = Form(None),
    currency: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_session),
):
    svc = await fetch_one(session, "SELECT id FROM invoice.services_master WHERE id = :id", {"id": service_id})
    if not svc:
        raise HTTPException(status_code=404, detail="service not found")

    def dec(val, default="0"):
        if val in (None, ""):
            val = default
        try:
            return Decimal(str(val))
        except Exception:
            return Decimal(str(default))

    await execute(
        session,
        """
        UPDATE invoice.services_master
        SET nummer=:nummer,
            beschreibung=:beschreibung,
            standard_faktor=:standard_faktor,
            standard_menge=:standard_menge,
            standard_einzelpreis=:standard_einzelpreis,
            kommentar_template=:kommentar_template,
            mwst_satz=:mwst_satz,
            rechnungskuertzel=:rechnungskuertzel,
            requires_diagnosis=:requires_diagnosis,
            currency=:currency
        WHERE id=:id
        """,
        {
            "id": service_id,
            "nummer": nummer,
            "beschreibung": beschreibung,
            "standard_faktor": dec(standard_faktor, "1"),
            "standard_menge": dec(standard_menge, "1"),
            "standard_einzelpreis": dec(standard_einzelpreis, "0"),
            "kommentar_template": kommentar_template,
            "mwst_satz": dec(mwst_satz, "0"),
            "rechnungskuertzel": rechnungskuertzel,
            "requires_diagnosis": True if requires_diagnosis else False,
            "currency": currency or "EUR",
        },
    )
    await session.commit()
    return RedirectResponse(url=f"/ui/services-master", status_code=303)


@router.post("/services-master/new", response_class=HTMLResponse)
async def ui_service_master_create(
    request: Request,
    nummer: Optional[str] = Form(None),
    beschreibung: Optional[str] = Form(None),
    standard_faktor: Optional[str] = Form(None),
    standard_menge: Optional[str] = Form(None),
    standard_einzelpreis: Optional[str] = Form(None),
    kommentar_template: Optional[str] = Form(None),
    mwst_satz: Optional[str] = Form(None),
    rechnungskuertzel: Optional[str] = Form(None),
    requires_diagnosis: Optional[str] = Form(None),
    currency: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_session),
):
    def dec(val, default="0"):
        if val in (None, ""):
            val = default
        try:
            return Decimal(str(val))
        except Exception:
            return Decimal(str(default))

    nummer_clean = (nummer or "").strip()
    beschreibung_clean = (beschreibung or "").strip()
    rechnungskuertzel_clean = (rechnungskuertzel or "").strip().upper() or None
    currency_clean = (currency or "EUR").strip().upper() or "EUR"

    svc = {
        "id": None,
        "rechnungskuertzel": rechnungskuertzel_clean or "",
        "nummer": nummer_clean,
        "beschreibung": beschreibung_clean,
        "standard_menge": str(dec(standard_menge, "1")),
        "standard_faktor": str(dec(standard_faktor, "1")),
        "standard_einzelpreis": str(dec(standard_einzelpreis, "0")),
        "mwst_satz": str(dec(mwst_satz, "0")),
        "currency": currency_clean,
        "requires_diagnosis": True if requires_diagnosis else False,
        "kommentar_template": kommentar_template or "",
    }

    if not nummer_clean or not beschreibung_clean:
        return templates.TemplateResponse(
            "service_master_edit.html",
            {"request": request, "svc": svc, "is_new": True, "error": "Nummer und Beschreibung sind Pflichtfelder."},
            status_code=400,
        )

    try:
        new_id = await execute_returning_id(
            session,
            """
            INSERT INTO invoice.services_master
                (nummer, beschreibung, standard_faktor, standard_menge, standard_einzelpreis,
                 kommentar_template, mwst_satz, rechnungskuertzel, requires_diagnosis, currency)
            VALUES
                (:nummer, :beschreibung, :standard_faktor, :standard_menge, :standard_einzelpreis,
                 :kommentar_template, :mwst_satz, :rechnungskuertzel, :requires_diagnosis, :currency)
            RETURNING id
            """,
            {
                "nummer": nummer_clean,
                "beschreibung": beschreibung_clean,
                "standard_faktor": dec(standard_faktor, "1"),
                "standard_menge": dec(standard_menge, "1"),
                "standard_einzelpreis": dec(standard_einzelpreis, "0"),
                "kommentar_template": kommentar_template,
                "mwst_satz": dec(mwst_satz, "0"),
                "rechnungskuertzel": rechnungskuertzel_clean,
                "requires_diagnosis": True if requires_diagnosis else False,
                "currency": currency_clean,
            },
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return templates.TemplateResponse(
            "service_master_edit.html",
            {
                "request": request,
                "svc": svc,
                "is_new": True,
                "error": "Diese Leistung existiert bereits (Nummer + Provider).",
            },
            status_code=409,
        )

    return RedirectResponse(url=f"/ui/services-master/{new_id}/edit", status_code=303)


@router.get("/invoices", response_class=HTMLResponse)
async def ui_invoices(
    request: Request,
    q: Optional[str] = None,
    provider_code: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    filters = []
    params = {"limit": limit, "offset": offset}
    if provider_code:
        filters.append("p.code = :provider_code")
        params["provider_code"] = provider_code
    if status:
        filters.append("i.status = :status")
        params["status"] = status
    if date_from:
        filters.append("i.invoice_date >= :date_from")
        params["date_from"] = date_from
    if date_to:
        filters.append("i.invoice_date <= :date_to")
        params["date_to"] = date_to
    if q:
        filters.append("""(
            i.invoice_number ILIKE :q
            OR c.firma ILIKE :q
            OR c.vorname ILIKE :q
            OR c.nachname ILIKE :q
            OR CAST(c.external_id AS TEXT) ILIKE :q
        )""")
        params["q"] = f"%{q}%"
    where_clause = " AND ".join(filters)
    if where_clause:
        where_clause = "WHERE " + where_clause

    rows = await fetch_all(
        session,
        f"""
        SELECT i.id,
               i.invoice_number,
               i.invoice_date,
               i.status,
               i.rechnungs_code,
               i.invoice_type_code,
               p.code AS provider_code,
               c.firma, c.vorname, c.nachname,
               COALESCE(
                 (i.totals->>'gross')::numeric,
                 (i.totals->>'brutto')::numeric,
                 (i.totals->>'grand_total')::numeric,
                 0
               ) AS gross
        FROM invoice.invoices i
        JOIN invoice.providers p ON p.id = i.provider_id
        JOIN invoice.customers c ON c.id = i.customer_id
        {where_clause}
        ORDER BY i.invoice_date DESC NULLS LAST, i.id DESC
        LIMIT :limit OFFSET :offset
        """,
        params,
    )
    return templates.TemplateResponse(
        "invoices_overview.html",
        {
            "request": request,
            "invoices": rows,
            "q": q or "",
            "provider_code": provider_code or "",
            "status": status or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
        },
    )


@router.post("/invoice/{invoice_id}/set-tax", response_class=HTMLResponse)
async def ui_invoice_set_tax(
    request: Request,
    invoice_id: int,
    rechnungs_code: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    try:
        # call API logic via DB to keep one source of truth
        vc = await fetch_one(session, "SELECT code, meaning FROM invoice.vat_category WHERE code = :c", {"c": rechnungs_code})
        if not vc:
            return HTMLResponse(status_code=422, content="Ungültiger Code")
        inv = await fetch_one(session, "SELECT status FROM invoice.invoices WHERE id = :id", {"id": invoice_id})
        if not inv:
            return HTMLResponse(status_code=404, content="Not found")
        if inv["status"] != "draft":
            return HTMLResponse(status_code=409, content="Nur im Draft änderbar")
        await execute(
            session,
            "UPDATE invoice.invoices SET rechnungs_code = :c, reverse_charge = :reverse_charge WHERE id = :id",
            {"c": rechnungs_code, "reverse_charge": rechnungs_code == "AE", "id": invoice_id},
        )
        await session.commit()
        inv["rechnungs_code"] = rechnungs_code
        inv["rechnungs_code_meaning"] = vc.get("meaning")
        return templates.TemplateResponse(
            "_invoice_tax_block.html",
            {"request": request, "invoice": inv, "vat_categories": await fetch_all(session, "SELECT code, meaning FROM invoice.vat_category ORDER BY code")},
        )
    except Exception:
        await session.rollback()
        logger.exception("set-tax failed invoice_id=%s", invoice_id)
        return HTMLResponse(status_code=500, content="Fehler")


@router.post("/invoice/{invoice_id}/cancel")
async def ui_invoice_cancel(invoice_id: int, reason: str = Form(...), session: AsyncSession = Depends(get_session)):
    inv = await fetch_one(session, "SELECT status FROM invoice.invoices WHERE id = :id", {"id": invoice_id})
    if not inv:
        raise HTTPException(status_code=404, detail="invoice not found")
    if inv["status"] != "final":
        return RedirectResponse(url=f"/ui/invoice/{invoice_id}", status_code=303)
    try:
        credit_id = await execute_returning_id(
            session,
            "SELECT invoice.invoice_cancel(:id, :reason)",
            {"id": invoice_id, "reason": reason},
        )
        await session.commit()
        return RedirectResponse(url=f"/ui/invoice/{credit_id}", status_code=303)
    except Exception:
        await session.rollback()
        raise


@router.post("/invoice/{invoice_id}/finalize")
async def ui_invoice_finalize(invoice_id: int, session: AsyncSession = Depends(get_session)):
    inv = await fetch_one(session, "SELECT status FROM invoice.invoices WHERE id = :id", {"id": invoice_id})
    if not inv:
        raise HTTPException(status_code=404, detail="invoice not found")
    if inv["status"] != "draft":
        return RedirectResponse(url=f"/ui/invoice/{invoice_id}", status_code=303)
    try:
        await execute(session, "SELECT invoice.invoice_finalize(:id)", {"id": invoice_id})
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return RedirectResponse(url=f"/ui/invoice/{invoice_id}", status_code=303)


@router.post("/invoice/{invoice_id}/undo")
async def ui_invoice_undo(invoice_id: int, session: AsyncSession = Depends(get_session)):
    inv = await fetch_one(
        session,
        "SELECT id, status, provider_id, customer_id FROM invoice.invoices WHERE id = :id",
        {"id": invoice_id},
    )
    if not inv:
        raise HTTPException(status_code=404, detail="invoice not found")
    if inv["status"] != "draft":
        return RedirectResponse(url=f"/ui/invoice/{invoice_id}", status_code=303)

    customer = await fetch_one(
        session,
        "SELECT id, external_system, external_id FROM invoice.customers WHERE id = :cid",
        {"cid": inv["customer_id"]},
    )
    try:
        entry_rows = await fetch_all(
            session,
            """
            SELECT (service_snapshot->>'service_entry_id')::bigint AS entry_id
            FROM invoice.invoice_lines
            WHERE invoice_id = :id
              AND service_snapshot ? 'service_entry_id'
            """,
            {"id": invoice_id},
        )
        entry_ids = [r["entry_id"] for r in entry_rows if r.get("entry_id")]
        if entry_ids:
            # Re-open only entries that belong to this draft.
            await execute(
                session,
                """
                UPDATE invoice.service_entries
                SET status = 'open'
                WHERE id = ANY(:ids) AND status = 'invoiced'
                """,
                {"ids": entry_ids},
            )
        else:
            logger.warning("invoice undo: no service_entry_id snapshot for invoice_id=%s", invoice_id)
        # Remove draft data
        await execute(session, "DELETE FROM invoice.invoice_lines WHERE invoice_id = :id", {"id": invoice_id})
        await execute(session, "DELETE FROM invoice.invoices WHERE id = :id", {"id": invoice_id})
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    # Redirect back to entry UI for editing
    if customer and customer.get("external_system") == "openemr" and customer.get("external_id"):
        return RedirectResponse(url=f"/ui/patient/{customer.get('external_id')}", status_code=303)
    return RedirectResponse(url=f"/ui/customer/{inv['customer_id']}", status_code=303)


@router.get("/services", response_model=list[dict])
async def ui_services(provider_code: str, session: AsyncSession = Depends(get_session)):
    return await _services_for_provider(session, provider_code)
