import io
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle



ZERO_TAX_CATEGORIES = {"E", "AE", "K"}


def _invoice_tax_category(inv: Dict[str, Any]) -> str:
    code = (inv.get("rechnungs_code") or "").strip().upper()
    if inv.get("reverse_charge"):
        return "AE"
    return code or "S"


def _line_tax_category(inv: Dict[str, Any], line: Dict[str, Any]) -> str:
    code = (line.get("rechnungs_code") or "").strip().upper()
    if code:
        return code
    return _invoice_tax_category(inv)


def _display_tax_rate(inv: Dict[str, Any], line: Dict[str, Any]) -> str:
    category = _line_tax_category(inv, line)
    if category in ZERO_TAX_CATEGORIES:
        return "0.00"
    return str(line.get("mwst_satz") or "0.00")

def _fmt_money(val: Decimal | float | int | None) -> str:
    if val is None:
        val = Decimal("0.00")
    if not isinstance(val, Decimal):
        val = Decimal(str(val or 0))
    return f"{val.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


PAGE_WIDTH, PAGE_HEIGHT = A4
CM = 28.3464567
FIX_HEADER_HEIGHT = 9 * CM  # 9 cm = 255 pt
LEFT_MARGIN = 18 * mm
RIGHT_MARGIN = 18 * mm
TOP_MARGIN = 10 * mm
BOTTOM_MARGIN = 12 * mm


def _truncate(text: str | None, max_chars: int) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _draw_boxed_lines(
    canvas,
    lines: List[str],
    x: float,
    y: float,
    max_width: float,
    leading: float = 12,
    right_align: bool = False,
    font_size: int = 10,
    bold: bool = False,
):
    """Draw lines starting at (x,y) downward; right_align draws using right-aligned text; bold allows 16pt header etc."""
    canvas.saveState()
    font_name = "Helvetica-Bold" if bold else "Helvetica"
    canvas.setFont(font_name, font_size)
    cur_y = y
    for line in lines:
        if not line:
            cur_y -= leading
            continue
        text = _truncate(line, 120)
        if right_align:
            canvas.drawRightString(x + max_width, cur_y, text)
        else:
            canvas.drawString(x, cur_y, text)
        cur_y -= leading
    canvas.restoreState()


class _NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        page_count = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_page_number(page_count)
            super().showPage()
        super().save()

    def _draw_page_number(self, page_count: int):
        page_num = self.getPageNumber()
        footer_text = f"Seite {page_num} von {page_count}"
        self.saveState()
        self.setFont("Helvetica", 8)
        self.drawCentredString(A4[0] / 2, 10 * mm, footer_text)
        self.restoreState()


def render_invoice_pdf(payload: Dict[str, Any]) -> bytes:
    """
    Render a simple invoice PDF from a prepared payload:
    {
      "invoice": {...},
      "lines": [...],
      "customer": {...},
      "provider": {...},
      "totals": {...},
    }
    """
    inv = payload["invoice"]
    lines: List[Dict[str, Any]] = payload["lines"]
    customer = payload["customer"]
    provider = payload["provider"]
    totals = payload["totals"]
    diagnoses = payload.get("diagnoses") or []
    provider_code = inv.get("provider_code") or provider.get("code")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=LEFT_MARGIN,
        rightMargin=RIGHT_MARGIN,
        topMargin=TOP_MARGIN,
        bottomMargin=BOTTOM_MARGIN,
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Right", alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name="Center", alignment=TA_CENTER))
    styles.add(ParagraphStyle(
        name="Small",
        parent=styles["Normal"],
        fontSize=8,
        leading=10
    ))

    story: list = []
    is_credit = bool(inv.get("cancellation_of_invoice_id"))

    patient_id = customer.get("external_id") or inv.get("customer_id")
    provider_name = provider.get("firma") or " ".join(filter(None, [provider.get("vorname"), provider.get("nachname")])) or "Rechnung"
    full_name = " ".join(filter(None, [provider.get("vorname"), provider.get("nachname")]))

    # Reserve fixed header zone to keep window-stable
    story.append(Spacer(1, FIX_HEADER_HEIGHT))
    story.append(Spacer(1, 0))
    if is_credit:
        ref_txt = inv.get("cancellation_reference") or inv.get("cancellation_of_invoice_id")
        story.append(Paragraph("<b>Storno-Rechnung / Gutschrift</b>", styles["Center"]))
        story.append(Paragraph(f"Bezug: Rechnung {ref_txt}", styles["Center"]))
        story.append(Spacer(1, 6))

    story.append(Paragraph("<b>Rechnung</b>", styles["Normal"]))
    story.append(Spacer(1, 4))
    if provider.get("freitext1"):
        story.append(Paragraph(provider["freitext1"], styles["Center"]))
        story.append(Spacer(1, 6))

    # Diagnosenblock (nur PP, vor Positionen, max wenige Zeilen)
    if provider_code == "PP" and diagnoses:
        story.append(Paragraph("<b>Diagnosen</b>", styles["Normal"]))
        diag_style = styles["Normal"]
        for diag in diagnoses:
            txt = f"{diag.get('icd_code') or ''} – {diag.get('title') or ''}"
            dates = []
            if diag.get("begdate"):
                dates.append(f"ab {diag.get('begdate')}")
            if diag.get("enddate"):
                dates.append(f"bis {diag.get('enddate')}")
            if dates:
                txt += f" ({', '.join(dates)})"
            story.append(Paragraph(f"• {txt}", diag_style))
        story.append(Spacer(1, 6))

    # Lines
    data = [["Pos", "Datum", "Nummer", "Beschreibung", "Menge", "Faktor", "EP", "GP", "MwSt%"]]
    for idx, line in enumerate(lines, start=1):
        snap = line.get("service_snapshot") or {}
        data.append([
            idx,
            str(line.get("leistungsdatum") or ""),
            Paragraph(snap.get("nummer") or "", styles["Normal"]),
            Paragraph((snap.get("beschreibung") or "") + ("<br/>" + snap.get("kommentar", "") if snap.get("kommentar") else ""), styles["Normal"]),
            str(line.get("menge")),
            str(line.get("faktor")),
            _fmt_money(line.get("einzelpreis")),
            _fmt_money(line.get("gesamtpreis")),
            _display_tax_rate(inv, line),
        ])

    tbl = Table(data, repeatRows=1, colWidths=[15*mm, 25*mm, 25*mm, 50*mm, 15*mm, 15*mm, 20*mm, 20*mm, 15*mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ALIGN", (-4, 1), (-1, -1), "RIGHT"),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 10))

    # Totals
    vat_rows = []
    for vat_entry in totals.get("vat_breakdown", []):
        vat_rows.append([f"MwSt {vat_entry.get('rate')}%", f"Netto {_fmt_money(vat_entry.get('net'))} / MwSt {_fmt_money(vat_entry.get('vat'))}"])
    total_rows = [
        ["Netto", _fmt_money(totals.get("net"))],
        *vat_rows,
        ["Brutto", _fmt_money(totals.get("gross"))],
    ]
    totals_tbl = Table(total_rows, colWidths=[45 * mm, 60 * mm])
    totals_tbl.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
    ]))
    story.append(totals_tbl)
    story.append(Spacer(1, 8))

    # Steuer / ID Block unter den Summen
    tax_lines: List[str] = []
    prov_vat = provider.get("vat_id")
    prov_tax = provider.get("tax_id")
    cust_vat = customer.get("vat_id")
    cust_tax = customer.get("tax_id")
    if prov_vat:
        tax_lines.append(f"Leistungserbringer USt-IdNr: {prov_vat}")
    elif prov_tax:
        tax_lines.append(f"Leistungserbringer Steuernr: {prov_tax}")
    if cust_vat:
        tax_lines.append(f"Leistungsempfänger USt-IdNr: {cust_vat}")
    elif cust_tax:
        tax_lines.append(f"Leistungsempfänger Steuernr: {cust_tax}")

    rc_code = _invoice_tax_category(inv)
    rc_meaning = inv.get("rechnungs_code_meaning")
    if rc_code:
        label = rc_meaning or ""
        tax_lines.append(f"Steuerkategorie: {rc_code}" + (f" – {label}" if label else ""))
        if rc_code == "AE":
            tax_lines.append("Hinweis: Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge).")
        elif rc_code == "E":
            tax_lines.append("Hinweis: Steuerbefreit (Exempt).")
        elif rc_code == "K":
            tax_lines.append("Hinweis: Innergemeinschaftliche Lieferung/Leistung (steuerfrei, nicht §4).")
        elif rc_code == "Z":
            tax_lines.append("Hinweis: Steuersatz 0 %, nicht steuerfrei.")
    if inv.get("reverse_charge") and rc_code != "AE":
        tax_lines.append("Hinweis: Reverse Charge, keine Umsatzsteuer ausgewiesen.")

    story.append(Paragraph("<b>Steuer / Identifikation</b>", styles["Normal"]))
    for line in tax_lines:
        story.append(Paragraph(line, styles["Small"]))

    if inv.get("payment_terms_text"):
        story.append(Spacer(1, 6))
        story.append(Paragraph("<b>Zahlungsbedingung</b>", styles["Normal"]))
        story.append(Paragraph(inv["payment_terms_text"], styles["Small"]))
    if provider.get("freitext2"):
        story.append(Spacer(1, 6))
        story.append(Paragraph(provider["freitext2"], styles["Center"]))

    # First-page fixed header drawer
    def _first_page(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Helvetica", 10)
        top_y = PAGE_HEIGHT - 12 * mm  # keep firm name as high as possible

        # Seller block (fixed area)
        # Diese Box: komplett 16pt fett (nur Name/Firma oben links)
        seller_lines = [_truncate(provider_name, 60)]
        _draw_boxed_lines(canvas, seller_lines, LEFT_MARGIN, top_y, max_width=110 * mm, leading=14, bold=True, font_size=16)
        # Sub-Zeilen unter dem Hauptnamen (kleiner, normal)
        #---- ANFANG diesen Block nicht Löschen posotion für Werbung spaeter ----- 
        # seller_sub = [
        #    _truncate(full_name, 60),
        #    _truncate(provider.get("strasse"), 60),
        #    " ".join(filter(None, [provider.get("plz"), provider.get("ort")])),
        #    provider.get("website") or "",
        # ]
        # _draw_boxed_lines(canvas, seller_sub, LEFT_MARGIN, top_y - 18, max_width=110 * mm, leading=12)
        #---- ENDE diesen Block nicht Löschen posotion für Werbung spaeter ----- 

        # Right contact block (all right-aligned)
        contact_lines = [
            _truncate(full_name, 50),
            _truncate(provider.get("strasse"), 60),
            _truncate(" ".join(filter(None, [provider.get("plz"), provider.get("ort")])), 60),
            provider.get("telefon") or provider.get("telefon1") or "",
            provider.get("email") or "",
            provider.get("website") or "",
        ]
        _draw_boxed_lines(canvas, contact_lines, PAGE_WIDTH - RIGHT_MARGIN - 70 * mm, top_y, max_width=70 * mm, leading=12, right_align=True)

        # Meta block aligned roughly auf Höhe des Addressfensters
        meta_start_y = PAGE_HEIGHT - 60 * mm  # same vertical band as recipient
        meta_lines = [
            f"Rechnung: {inv.get('invoice_number') or 'DRAFT'}",
            f"Datum: {inv.get('invoice_date') or ''}",
            f"Patient-ID: {patient_id}",
            f"Typ: {inv.get('invoice_type_code') or ''}"
            + (f" ({inv.get('invoice_type_meaning')})" if inv.get('invoice_type_meaning') else ""),
        ]
        _draw_boxed_lines(canvas, meta_lines, PAGE_WIDTH - RIGHT_MARGIN - 70 * mm, meta_start_y, max_width=70 * mm, leading=12, right_align=True)

        # Recipient block (window) lowered by ~1 cm
        rec_y = PAGE_HEIGHT - (55 * mm)
        recipient_lines = [
            _truncate(customer.get("firma"), 80),
            _truncate(" ".join(filter(None, [customer.get("vorname"), customer.get("nachname")])), 80),
            _truncate(customer.get("strasse"), 80),
            _truncate(" ".join(filter(None, [customer.get("plz"), customer.get("ort")])), 80),
            _truncate(customer.get("land"), 80),
        ]
        _draw_boxed_lines(canvas, recipient_lines, LEFT_MARGIN, rec_y, max_width=90 * mm, leading=12)

        # Sender small line above window (fine-tuning space for envelope)
        sender_y = rec_y + 32  # just above recipient block
        sender_lines = [
            _truncate(provider.get("firma") or provider_name, 70),
#            _truncate(provider.get("strasse"), 70),
            _truncate(" ".join(filter(None, [provider.get("strasse"), provider.get("plz"), provider.get("ort")])), 70),
        ]
        _draw_boxed_lines(canvas, sender_lines, LEFT_MARGIN, sender_y, max_width=90 * mm, leading=10, font_size=8)

        canvas.restoreState()

    # render
    doc.build(story, onFirstPage=_first_page, canvasmaker=_NumberedCanvas)
    buffer.seek(0)
    return buffer.read()
