import io
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db_utils import fetch_all, fetch_one
from app.pdf_renderer import render_invoice_pdf
from app.zugferd import build_zugferd_xml, embed_zugferd_in_pdf
from app.zugferd_lite_validator import validate_zugferd_lite

router = APIRouter(prefix="/exports", tags=["exports"])
logger = logging.getLogger(__name__)


def _to_decimal(val: Any) -> Decimal:
    if isinstance(val, Decimal):
        return val
    return Decimal(str(val or 0))


ZERO_TAX_CATEGORIES = {"E", "AE", "K"}


def _invoice_tax_category(inv: dict) -> str:
    code = (inv.get("rechnungs_code") or "").strip().upper()
    if inv.get("reverse_charge"):
        return "AE"
    return code or "S"


def _line_tax_category(inv: dict, line: dict) -> str:
    code = (line.get("rechnungs_code") or "").strip().upper()
    if code:
        return code
    return _invoice_tax_category(inv)


def _is_reverse_charge(inv: dict) -> bool:
    return bool(inv.get("reverse_charge")) or _invoice_tax_category(inv) == "AE"


def _line_tax_rate(inv: dict, line: dict) -> Decimal:
    category = _line_tax_category(inv, line)
    if category in ZERO_TAX_CATEGORIES:
        return Decimal("0.00")
    return _to_decimal(line.get("mwst_satz") or 0)


def _compute_totals(lines: list[dict], inv: dict) -> dict:
    net = Decimal("0.00")
    vat_map: dict[str, dict[str, Decimal]] = {}
    for line in lines:
        gp = _to_decimal(line.get("gesamtpreis"))
        rate = _line_tax_rate(inv, line)
        net += gp
        if rate == 0:
            continue
        rate_key = str(rate)
        vat_map.setdefault(rate_key, {"rate": rate, "net": Decimal("0.00"), "vat": Decimal("0.00")})
        vat_map[rate_key]["net"] += gp
        vat_map[rate_key]["vat"] += (gp * rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    vat_breakdown = list(vat_map.values())
    total_vat = sum((entry["vat"] for entry in vat_breakdown), Decimal("0.00"))
    gross = net + total_vat
    return {
        "net": net,
        "vat": total_vat,
        "vat_breakdown": vat_breakdown,
        "gross": gross,
    }


async def _load_invoice_payload(session: AsyncSession, invoice_id: int) -> Dict[str, Any]:
    inv = await fetch_one(
        session,
        """
        SELECT i.*,
               vc.meaning AS rechnungs_code_meaning,
               vc.tax_logic AS rechnungs_code_tax_logic,
               vc.comment AS rechnungs_code_comment,
               itc.meaning AS invoice_type_meaning,
               p.code AS provider_code
        FROM invoice.invoices i
        LEFT JOIN invoice.providers p ON p.id = i.provider_id
        LEFT JOIN invoice.vat_category vc ON vc.code = i.rechnungs_code
        LEFT JOIN invoice.invoice_type_code itc ON itc.code = i.invoice_type_code
        WHERE i.id = :id
        """,
        {"id": invoice_id},
    )
    if not inv:
        raise HTTPException(status_code=404, detail="invoice not found")
    lines = await fetch_all(session, "SELECT * FROM invoice.invoice_lines WHERE invoice_id = :id ORDER BY position_no", {"id": invoice_id})

    # load live and snapshot, merge to ensure new fields show even for finalized invoices
    customer_live = await fetch_one(session, "SELECT * FROM invoice.customers WHERE id = :cid", {"cid": inv["customer_id"]}) or {}
    provider_live = await fetch_one(session, "SELECT * FROM invoice.providers WHERE id = :pid", {"pid": inv["provider_id"]}) or {}
    customer = {**customer_live, **(inv.get("customer_snapshot") or {})}
    provider = {**provider_live, **(inv.get("provider_snapshot") or {})}

    if (inv.get("rechnungs_code") or "").strip().upper() == "AE":
        inv["reverse_charge"] = True
    elif inv.get("reverse_charge") and not inv.get("rechnungs_code"):
        inv["rechnungs_code"] = "AE"

    if not inv.get("payment_terms_text"):
        payment_key = customer.get("zahlungsbedingung_key")
        if payment_key:
            payment_term = await fetch_one(
                session,
                "SELECT text FROM invoice.payment_terms WHERE key = :key",
                {"key": payment_key},
            )
            if payment_term:
                inv["payment_terms_text"] = payment_term.get("text")
                inv["payment_terms_key"] = payment_key
    elif customer.get("zahlungsbedingung_key"):
        inv["payment_terms_key"] = customer.get("zahlungsbedingung_key")

    # normalize service_snapshot if stored as text
    import json
    for line in lines:
        snap = line.get("service_snapshot")
        if isinstance(snap, str):
            try:
                line["service_snapshot"] = json.loads(snap)
            except Exception:
                line["service_snapshot"] = {}

    if inv.get("cancellation_of_invoice_id"):
        ref = await fetch_one(
            session,
            "SELECT id, invoice_number FROM invoice.invoices WHERE id = :rid",
            {"rid": inv["cancellation_of_invoice_id"]},
        )
        if ref:
            inv["cancellation_reference"] = ref.get("invoice_number") or str(ref.get("id"))

    totals = _compute_totals(lines, inv)
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
    # Fallback: für PP und fehlende Diagnosen aktive openemr-Probleme des Kunden laden (nur Anzeige in Draft)
    if (not diagnoses) and inv.get("provider_code") == "PP":
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

    payload = {
        "invoice": inv,
        "lines": lines,
        "customer": customer,
        "provider": provider,
        "totals": totals,
        "diagnoses": diagnoses,
    }
    return payload


@router.get("/invoices/{invoice_id}/pdf")
async def export_invoice_pdf(invoice_id: int, session: AsyncSession = Depends(get_session)):
    payload = await _load_invoice_payload(session, invoice_id)
    pdf_bytes = render_invoice_pdf(payload)
    filename = payload["invoice"].get("invoice_number") or f"DRAFT_{invoice_id}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}.pdf"'}
    return StreamingResponse(io.BytesIO(pdf_bytes), media_type="application/pdf", headers=headers)


@router.get("/invoices/{invoice_id}/zugferd")
async def export_invoice_zugferd(invoice_id: int, session: AsyncSession = Depends(get_session)):
    payload = await _load_invoice_payload(session, invoice_id)
    inv = payload["invoice"]
    if inv.get("status") != "final":
        raise HTTPException(status_code=409, detail="ZUGFeRD nur für finalisierte Rechnungen")
    filename = inv.get("invoice_number") or f"DRAFT_{invoice_id}"
    xml_bytes = build_zugferd_xml(payload)
    try:
        pdf_bytes = render_invoice_pdf(payload)
        embedded = embed_zugferd_in_pdf(pdf_bytes, xml_bytes, filename=f"{filename}.xml")
        headers = {"Content-Disposition": f'attachment; filename="{filename}_zugferd.pdf"'}
        return StreamingResponse(io.BytesIO(embedded), media_type="application/pdf", headers=headers)
    except Exception:
        logger.exception("failed to embed ZUGFeRD, returning XML")
        headers = {"Content-Disposition": f'attachment; filename="{filename}.xml"'}
        return StreamingResponse(io.BytesIO(xml_bytes), media_type="application/xml", headers=headers)


@router.get("/invoices/{invoice_id}/zugferd/validate-lite")
async def export_invoice_zugferd_validate(invoice_id: int, session: AsyncSession = Depends(get_session)):
    payload = await _load_invoice_payload(session, invoice_id)
    inv = payload["invoice"]
    if inv.get("status") != "final":
        raise HTTPException(status_code=409, detail="Validation nur für finalisierte Rechnungen")
    filename = inv.get("invoice_number") or f"DRAFT_{invoice_id}"
    xml_bytes = build_zugferd_xml(payload)
    pdf_bytes = render_invoice_pdf(payload)
    try:
        pdf_bytes = embed_zugferd_in_pdf(pdf_bytes, xml_bytes, filename=f"{filename}.xml")
    except Exception:
        logger.exception("embedding failed during validate-lite; validating raw PDF")
    result = validate_zugferd_lite(pdf_bytes)
    return result
