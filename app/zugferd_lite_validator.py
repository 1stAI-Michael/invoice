import io
from typing import Any, Dict, List, Tuple

from pypdf import PdfReader
from xml.etree import ElementTree as ET


def _extract_attachments(reader: PdfReader) -> List[Tuple[str, bytes]]:
    files: List[Tuple[str, bytes]] = []
    # pypdf >=3 convenience
    if hasattr(reader, "attachments"):
        try:
            for name, data in reader.attachments.items():  # type: ignore[attr-defined]
                if hasattr(data, "get_data"):
                    files.append((name, data.get_data()))
                elif isinstance(data, list):
                    try:
                        files.append((name, bytes(data)))
                    except Exception:
                        continue
                elif isinstance(data, (bytes, bytearray)):
                    files.append((name, data))
        except Exception:
            pass
    if files:
        return files
    # fallback: Names tree
    try:
        root = reader.trailer["/Root"]
        names = root.get("/Names")
        if not names:
            return files
        embedded = names.get("/EmbeddedFiles")
        if not embedded:
            return files
        name_array = embedded.get("/Names")
        if not name_array:
            return files
        for i in range(0, len(name_array), 2):
            name_obj = name_array[i]
            spec = name_array[i + 1]
            ef_dict = spec.get("/EF")
            if ef_dict and ef_dict.get("/F"):
                stream = ef_dict.get("/F")
                data = stream.get_data()
                files.append((str(name_obj), data))
    except Exception:
        return files
    return files


def validate_zugferd_lite(pdf_bytes: bytes) -> Dict[str, Any]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    attachments = _extract_attachments(reader)
    xml_files: List[Dict[str, Any]] = []
    all_attachments: List[Dict[str, Any]] = []
    has_cii_xml = False
    cii_namespace_found = False
    first_preview = ""
    minimal_ok = False
    issues: List[str] = []

    for name, data in attachments:
        all_attachments.append({"name": name, "size": len(data) if hasattr(data, "__len__") else None})
        # normalize data to bytes if possible
        if isinstance(data, list):
            try:
                data = bytes(data)
            except Exception:
                continue
        if not isinstance(data, (bytes, bytearray)):
            continue
        if name.lower().endswith(".xml") or b"<?xml" in data[:20]:
            text = data.decode("utf-8", errors="replace")
            xml_files.append({"name": name, "size": len(data)})
            if not first_preview:
                first_preview = text[:200]
            if "CrossIndustryInvoice" in text:
                has_cii_xml = True
            if "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice" in text:
                cii_namespace_found = True
            if not minimal_ok:
                try:
                    root = ET.fromstring(text)
                    ns = {'rsm': root.tag.split('}')[0].strip('{')} if '}' in root.tag else {}
                    # basic required fields
                    doc_id = root.find('.//rsm:ExchangedDocument/rsm:ID', ns)
                    issue_date = root.find('.//rsm:ExchangedDocument/rsm:IssueDateTime', ns)
                    curr = root.find('.//rsm:ApplicableHeaderTradeSettlement/rsm:InvoiceCurrencyCode', ns)
                    lines = root.findall('.//rsm:IncludedSupplyChainTradeLineItem', ns)
                    total_base = root.find('.//rsm:SpecifiedTradeSettlementHeaderMonetarySummation/rsm:TaxBasisTotalAmount', ns)
                    total_gross = root.find('.//rsm:SpecifiedTradeSettlementHeaderMonetarySummation/rsm:GrandTotalAmount', ns)
                    if doc_id is None or (doc_id.text or '').strip() == '':
                        issues.append("Missing ExchangedDocument/ID")
                    if issue_date is None or (issue_date.text or '').strip() == '':
                        issues.append("Missing ExchangedDocument/IssueDateTime")
                    if curr is None or (curr.text or '').strip() == '':
                        issues.append("Missing InvoiceCurrencyCode")
                    if not lines:
                        issues.append("No line items")
                    if total_base is None or (total_base.text or '').strip() == '':
                        issues.append("Missing TaxBasisTotalAmount")
                    if total_gross is None or (total_gross.text or '').strip() == '':
                        issues.append("Missing GrandTotalAmount")
                    minimal_ok = len(issues) == 0
                except Exception as parse_exc:
                    issues.append(f"XML parse failed: {parse_exc}")

    return {
        "has_embedded_files": len(attachments) > 0,
        "all_attachments": all_attachments,
        "xml_files": xml_files,
        "has_cii_xml": has_cii_xml,
        "cii_namespace_found": cii_namespace_found,
        "first_200_chars_of_xml": first_preview,
        "minimal_ok": minimal_ok,
        "issues": issues,
    }
