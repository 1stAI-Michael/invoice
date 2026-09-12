import io
import re
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict
from xml.etree import ElementTree as ET

from pypdf import PdfReader, PdfWriter



ZERO_TAX_CATEGORIES = {"E", "AE", "K"}
EXEMPTION_REASON_CODES = {"AE": "VATEX-EU-AE"}


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


def _tax_rate(inv: Dict[str, Any], line: Dict[str, Any]) -> Decimal:
    category = _line_tax_category(inv, line)
    if category in ZERO_TAX_CATEGORIES:
        return Decimal("0.00")
    return Decimal(str(line.get("mwst_satz", 0)))


def _payment_days(inv: Dict[str, Any]) -> int | None:
    raw = " ".join(filter(None, [str(inv.get("payment_terms_key") or ""), str(inv.get("payment_terms_text") or "")]))
    match = re.search(r"(?:NET|TNET)\s*(\d+)|(\d+)\s*(?:TNET|Tage|Tag|days|day)", raw, re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return date.today()

def _fmt_decimal(val: Decimal | float | int) -> str:
    d = val if isinstance(val, Decimal) else Decimal(str(val))
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _fmt_date_102(value: Any) -> str:
    if isinstance(value, datetime):
        d = value.date()
    elif isinstance(value, date):
        d = value
    elif isinstance(value, str):
        try:
            d = date.fromisoformat(value)
        except ValueError:
            return value.replace("-", "")
    else:
        d = date.today()
    return d.strftime("%Y%m%d")


def build_zugferd_xml(payload: Dict[str, Any]) -> bytes:
    inv = payload["invoice"]
    lines = payload["lines"]
    customer = payload["customer"]
    provider = payload["provider"]
    totals = payload["totals"]

    ns = {
        "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
        "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
        "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
    }
    ET.register_namespace("rsm", ns["rsm"])
    ET.register_namespace("ram", ns["ram"])
    ET.register_namespace("udt", ns["udt"])
    q_rsm = lambda tag: ET.QName(ns["rsm"], tag)
    q_ram = lambda tag: ET.QName(ns["ram"], tag)
    q_udt = lambda tag: ET.QName(ns["udt"], tag)
    root = ET.Element(q_rsm("CrossIndustryInvoice"))

    # Context (must be before ExchangedDocument)
    context = ET.SubElement(root, q_rsm("ExchangedDocumentContext"))
    guideline = ET.SubElement(context, q_ram("GuidelineSpecifiedDocumentContextParameter"))
    guideline_id = payload.get("zugferd_guideline_id") or "urn:cen.eu:en16931:2017#compliant#urn:zugferd.de:2p1:basic"
    ET.SubElement(guideline, q_ram("ID")).text = guideline_id

    # Header
    header = ET.SubElement(root, q_rsm("ExchangedDocument"))
    ET.SubElement(header, q_ram("ID")).text = inv.get("invoice_number") or f"DRAFT-{inv['id']}"
    ET.SubElement(header, q_ram("TypeCode")).text = inv.get("invoice_type_code") or "380"
    issue = ET.SubElement(header, q_ram("IssueDateTime"))
    issue_dt = ET.SubElement(issue, q_udt("DateTimeString"), {"format": "102"})
    issue_dt.text = _fmt_date_102(inv.get("invoice_date") or date.today())

    # Lines (must come before header blocks in transaction)
    trade = ET.SubElement(root, q_rsm("SupplyChainTradeTransaction"))
    for idx, line in enumerate(lines, start=1):
        item = ET.SubElement(trade, q_ram("IncludedSupplyChainTradeLineItem"))
        line_doc = ET.SubElement(item, q_ram("AssociatedDocumentLineDocument"))
        line_id = line.get("position_no") or idx
        ET.SubElement(line_doc, q_ram("LineID")).text = str(line_id)
        snap = line.get("service_snapshot") or {}
        description = snap.get("beschreibung") or snap.get("kommentar") or ""
        product = ET.SubElement(item, q_ram("SpecifiedTradeProduct"))
        ET.SubElement(product, q_ram("Name")).text = description
        basis = ET.SubElement(item, q_ram("SpecifiedLineTradeAgreement"))
        gross_price = ET.SubElement(basis, q_ram("GrossPriceProductTradePrice"))
        ET.SubElement(gross_price, q_ram("ChargeAmount")).text = _fmt_decimal(line.get("einzelpreis", 0))
        net_price = ET.SubElement(basis, q_ram("NetPriceProductTradePrice"))
        ET.SubElement(net_price, q_ram("ChargeAmount")).text = _fmt_decimal(line.get("einzelpreis", 0))
        delivery = ET.SubElement(item, q_ram("SpecifiedLineTradeDelivery"))
        unit_code = (line.get("unit_code") or "C62").strip()
        billed = ET.SubElement(delivery, q_ram("BilledQuantity"), {"unitCode": unit_code})
        billed.text = _fmt_decimal(line.get("menge", 0))
        settlement = ET.SubElement(item, q_ram("SpecifiedLineTradeSettlement"))
        tax = ET.SubElement(settlement, q_ram("ApplicableTradeTax"))
        tax_category = _line_tax_category(inv, line)
        rate = _tax_rate(inv, line)
        ET.SubElement(tax, q_ram("TypeCode")).text = "VAT"
        ET.SubElement(tax, q_ram("CategoryCode")).text = tax_category
        ET.SubElement(tax, q_ram("RateApplicablePercent")).text = _fmt_decimal(rate)
        if tax_category in EXEMPTION_REASON_CODES:
            ET.SubElement(tax, q_ram("ExemptionReasonCode")).text = EXEMPTION_REASON_CODES[tax_category]
        summation = ET.SubElement(settlement, q_ram("SpecifiedTradeSettlementLineMonetarySummation"))
        ET.SubElement(summation, q_ram("LineTotalAmount")).text = _fmt_decimal(line.get("gesamtpreis", 0))

    # Parties
    agreement = ET.SubElement(trade, q_ram("ApplicableHeaderTradeAgreement"))
    seller = ET.SubElement(agreement, q_ram("SellerTradeParty"))
    ET.SubElement(seller, q_ram("Name")).text = provider.get("firma") or provider.get("nachname") or ""
    seller_vat = (provider.get("vat_id") or "").strip()
    seller_tax = (provider.get("tax_id") or "").strip()
    if seller_vat:
        seller_reg = ET.SubElement(seller, q_ram("SpecifiedTaxRegistration"))
        ET.SubElement(seller_reg, q_ram("ID"), {"schemeID": "VA"}).text = seller_vat
    elif seller_tax:
        seller_reg = ET.SubElement(seller, q_ram("SpecifiedTaxRegistration"))
        ET.SubElement(seller_reg, q_ram("ID"), {"schemeID": "FC"}).text = seller_tax

    buyer = ET.SubElement(agreement, q_ram("BuyerTradeParty"))
    ET.SubElement(buyer, q_ram("Name")).text = customer.get("firma") or " ".join(filter(None, [customer.get("vorname"), customer.get("nachname")])) or ""
    buyer_vat = (customer.get("vat_id") or "").strip()
    buyer_tax = (customer.get("tax_id") or "").strip()
    if buyer_vat:
        buyer_reg = ET.SubElement(buyer, q_ram("SpecifiedTaxRegistration"))
        ET.SubElement(buyer_reg, q_ram("ID"), {"schemeID": "VA"}).text = buyer_vat
    elif buyer_tax:
        buyer_reg = ET.SubElement(buyer, q_ram("SpecifiedTaxRegistration"))
        ET.SubElement(buyer_reg, q_ram("ID"), {"schemeID": "FC"}).text = buyer_tax

    ET.SubElement(trade, q_ram("ApplicableHeaderTradeDelivery"))

    # Totals (payment means -> tax breakdown -> summation)
    summary = ET.SubElement(trade, q_ram("ApplicableHeaderTradeSettlement"))
    currency = (inv.get("invoice_currency_code") or "EUR").strip() or "EUR"
    ET.SubElement(summary, q_ram("InvoiceCurrencyCode")).text = currency

    payment_means = ET.SubElement(summary, q_ram("SpecifiedTradeSettlementPaymentMeans"))
    ET.SubElement(payment_means, q_ram("TypeCode")).text = inv.get("payment_means_code") or "58"
    iban = (provider.get("iban") or "").strip()
    bic = (provider.get("bic") or "").strip()
    if iban or bic:
        acct = ET.SubElement(payment_means, q_ram("PayeePartyCreditorFinancialAccount"))
        if iban:
            ET.SubElement(acct, q_ram("IBANID")).text = iban
        if bic:
            inst = ET.SubElement(payment_means, q_ram("PayeeSpecifiedCreditorFinancialInstitution"))
            ET.SubElement(inst, q_ram("BICID")).text = bic

    tax_groups: dict[tuple[str, str], Decimal] = {}
    for line in lines:
        tax_category = _line_tax_category(inv, line)
        rate = _tax_rate(inv, line)
        basis = Decimal(str(line.get("gesamtpreis", 0)))
        key = (tax_category, _fmt_decimal(rate))
        tax_groups[key] = tax_groups.get(key, Decimal("0.00")) + basis

    tax_total = Decimal("0.00")
    for (tax_category, rate_str), basis in sorted(tax_groups.items()):
        rate = Decimal(rate_str)
        calculated = Decimal("0.00") if tax_category in {"E", "AE", "K"} else (basis * rate / Decimal("100"))
        tax_total += calculated
        tax = ET.SubElement(summary, q_ram("ApplicableTradeTax"))
        ET.SubElement(tax, q_ram("CalculatedAmount")).text = _fmt_decimal(calculated)
        ET.SubElement(tax, q_ram("TypeCode")).text = "VAT"
        ET.SubElement(tax, q_ram("BasisAmount")).text = _fmt_decimal(basis)
        ET.SubElement(tax, q_ram("CategoryCode")).text = tax_category
        ET.SubElement(tax, q_ram("RateApplicablePercent")).text = _fmt_decimal(rate)
        if tax_category in EXEMPTION_REASON_CODES:
            ET.SubElement(tax, q_ram("ExemptionReasonCode")).text = EXEMPTION_REASON_CODES[tax_category]

    if inv.get("payment_terms_text"):
        terms = ET.SubElement(summary, q_ram("SpecifiedTradePaymentTerms"))
        ET.SubElement(terms, q_ram("Description")).text = inv.get("payment_terms_text")
        days = _payment_days(inv)
        if days is not None:
            due = ET.SubElement(terms, q_ram("DueDateDateTime"))
            due_date = _as_date(inv.get("invoice_date")) + timedelta(days=days)
            ET.SubElement(due, q_udt("DateTimeString"), {"format": "102"}).text = _fmt_date_102(due_date)

    line_total = sum(Decimal(str(line.get("gesamtpreis", 0))) for line in lines)
    grand_total = line_total + tax_total
    totals_el = ET.SubElement(summary, q_ram("SpecifiedTradeSettlementHeaderMonetarySummation"))
    ET.SubElement(totals_el, q_ram("LineTotalAmount")).text = _fmt_decimal(line_total)
    ET.SubElement(totals_el, q_ram("ChargeTotalAmount")).text = "0.00"
    ET.SubElement(totals_el, q_ram("AllowanceTotalAmount")).text = "0.00"
    ET.SubElement(totals_el, q_ram("TaxBasisTotalAmount")).text = _fmt_decimal(line_total)
    ET.SubElement(
        totals_el,
        q_ram("TaxTotalAmount"),
        {"currencyID": currency},
    ).text = _fmt_decimal(tax_total)
    ET.SubElement(totals_el, q_ram("GrandTotalAmount")).text = _fmt_decimal(grand_total)
    ET.SubElement(totals_el, q_ram("DuePayableAmount")).text = _fmt_decimal(grand_total)

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return xml_bytes


def embed_zugferd_in_pdf(pdf_bytes: bytes, xml_bytes: bytes, filename: str = "zugferd.xml") -> bytes:
    """
    Embed the XML into PDF as a file attachment (ZUGFeRD-like). Simple embedding via pypdf.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    writer.add_attachment(filename, xml_bytes)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out.read()
