from datetime import date
from decimal import Decimal
from typing import Any, List, Optional

from pydantic import BaseModel, Field, condecimal


class InvoiceCreateRequest(BaseModel):
    provider_code: str = Field(..., pattern="^(TI|PP|MS)$")
    customer_id: Optional[int] = None
    external_system: Optional[str] = None
    external_id: Optional[int] = None
    reverse_charge: Optional[bool] = None


class InvoiceLineResponse(BaseModel):
    id: int
    position_no: int
    service_snapshot: Any
    leistungsdatum: date
    menge: Decimal
    faktor: Decimal
    mwst_satz: Decimal
    einzelpreis: Decimal
    gesamtpreis: Decimal


class InvoiceDiagnosisResponse(BaseModel):
    id: int
    title: str
    icd_code: Optional[str] = None
    begdate: Optional[date] = None
    enddate: Optional[date] = None


class InvoiceResponse(BaseModel):
    id: int
    provider_id: int
    customer_id: int
    status: str
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    leistungsdatum_max: Optional[date] = None
    reverse_charge: bool
    customer_snapshot: Optional[Any] = None
    provider_snapshot: Optional[Any] = None
    payment_terms_text: Optional[str] = None
    totals: Optional[Any] = None
    hash: Optional[str] = None
    created_at: Any
    finalized_at: Optional[Any] = None
    lines: List[InvoiceLineResponse] = []
    diagnoses: List[InvoiceDiagnosisResponse] = []


class InvoiceLineCreateRequest(BaseModel):
    service_id: Optional[int] = None
    nummer: Optional[str] = None
    beschreibung: Optional[str] = None
    kommentar: Optional[str] = None
    mwst_satz: Optional[condecimal(max_digits=5, decimal_places=2)] = None
    leistungsdatum: date
    menge: condecimal(max_digits=10, decimal_places=2) = Decimal("1.00")
    faktor: condecimal(max_digits=6, decimal_places=2) = Decimal("1.00")
    einzelpreis: Optional[condecimal(max_digits=12, decimal_places=2)] = None
    gesamtpreis: Optional[condecimal(max_digits=12, decimal_places=2)] = None


class AddLinesFromServiceEntriesRequest(BaseModel):
    service_entry_ids: List[int]


class FinalizeResponse(BaseModel):
    invoice_id: int
    invoice_number: Optional[str] = None
    status: str
    invoice_date: Optional[date] = None
    totals: Optional[Any] = None
    hash: Optional[str] = None


class CancelRequest(BaseModel):
    reason: str


class CancelResponse(BaseModel):
    cancelled_invoice_id: int
    credit_invoice_id: int
    reopened_service_entries: int


class DiagnosisUpsert(BaseModel):
    title: str
    icd_code: Optional[str] = None
    begdate: Optional[date] = None
    enddate: Optional[date] = None


class ServiceEntryResponse(BaseModel):
    id: int
    customer_id: int
    provider_id: int
    service_id: int
    external_encounter_id: Optional[int] = None
    leistungsdatum: date
    menge: Decimal
    faktor: Decimal
    kommentar: Optional[str] = None
    status: str
    created_at: Any


class ServiceEntryCreateRequest(BaseModel):
    provider_code: str = Field(..., pattern="^(TI|PP|MS)$")
    customer_id: Optional[int] = None
    openemr_pid: Optional[int] = None
    service_id: int
    external_encounter_id: Optional[int] = None
    leistungsdatum: date
    menge: condecimal(max_digits=10, decimal_places=2) = Decimal("1.00")
    faktor: condecimal(max_digits=6, decimal_places=2) = Decimal("1.00")
    kommentar: Optional[str] = None


class ServiceEntryUpdateRequest(BaseModel):
    service_id: Optional[int] = None
    external_encounter_id: Optional[int] = None
    leistungsdatum: Optional[date] = None
    menge: Optional[condecimal(max_digits=10, decimal_places=2)] = None
    faktor: Optional[condecimal(max_digits=6, decimal_places=2)] = None
    kommentar: Optional[str] = None


class ServiceEntryBatchCreateRequest(BaseModel):
    entries: List[ServiceEntryCreateRequest]


class CustomerListItem(BaseModel):
    id: int
    firma: Optional[str] = None
    vorname: Optional[str] = None
    nachname: Optional[str] = None
    ort: Optional[str] = None
    external_system: Optional[str] = None
    external_id: Optional[int] = None


class ServiceListItem(BaseModel):
    id: int
    nummer: str
    beschreibung: str
    standard_einzelpreis: Decimal
    mwst_satz: Decimal
    standard_faktor: Decimal
    kommentar_template: Optional[str] = None


class DraftFromServiceEntriesRequest(BaseModel):
    provider_code: str = Field(..., pattern="^(TI|PP|MS)$")
    customer_id: Optional[int] = None
    openemr_pid: Optional[int] = None
    service_entry_ids: Optional[list[int]] = None
    reverse_charge: Optional[bool] = None
