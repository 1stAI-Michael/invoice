# Dokument: API
# Version: v0.9
# Letzte Änderung: 2025-02-06

## Überblick
FastAPI-Endpoints für OpenEMR-Read-Modell, Service-Journal, Invoice-Workflow, Exports und UI-Hilfen.

## Wichtige Endpoints (Auswahl)

### Health
- `GET /health` – DB-Check.

### OpenEMR
- `GET /openemr/patients/search?q=...`
- `GET /openemr/patients/{pid}/timeline`
- `GET /openemr/patients/{pid}/diagnoses?active_only=true`

### Service Entries
- `POST /service-entries` – Leistung anlegen (provider_code TI/PP/MS; customer_id oder openemr_pid).
- `POST /service-entries/batch` – Mehrere Leistungen anlegen (Array von `ServiceEntryCreateRequest`).
- `GET /service-entries` – Filtern nach provider_code, customer_id/openemr_pid, status.
- `PATCH /service-entries/{id}` – Nur wenn status=open.
- `DELETE /service-entries/{id}` – Nur wenn status=open.

### Services
- `GET /services?provider_code=MS` – Leistungskatalog je Provider als JSON-Liste.

### Workflow
- `POST /workflow/invoices/draft-from-service-entries` – Draft aus offenen Leistungen (provider+customer).

### Customers
- `GET /customers` – Kundenliste (optional `q`, `limit`), für Dropdowns/Lookup.

### Invoices
- `POST /invoices/draft` – Draft anlegen (provider_code, customer/external).
- `GET /invoices/{id}` – Header + Lines + Diagnoses.
- `POST /invoices/{id}/lines` – Position hinzufügen (nur draft).
- `POST /invoices/{id}/diagnoses` – Diagnosen ersetzen (draft).
- `POST /invoices/{id}/add-lines-from-service-entries` – Offene Leistungen übernehmen.
- `POST /invoices/{id}/finalize` – DB-Funktion invoice_finalize aufrufen.
- `POST /invoices/{id}/cancel` – Storno/Gutschrift erzeugen, Original auf cancelled setzen, Leistungen wieder öffnen.
Hinweis: Invoices führen `rechnungs_code` (VAT-Kategorie S/Z/E/AE/K) und `invoice_type_code` (380/381/384/389); Provider/Kunde haben tax_id/vat_id Felder.
Hinweis: `invoice_lines.rechnungs_code` ist optional; wenn nicht gesetzt, gilt die Vererbung von `invoices.rechnungs_code`.

### Exports
- `GET /exports/invoices/{id}/pdf` – PDF-Download.
- `GET /exports/invoices/{id}/zugferd` – ZUGFeRD-PDF (nur final; Fallback XML).
- `GET /exports/invoices/{id}/zugferd/validate-lite` – ZUGFeRD Lite Check (embedded XML gefunden? CII-Element/Namespace?).

## Neue Stammdaten/Felder
- Kunden/Provider: `tax_id`, `vat_id`; Provider zusätzlich `iban`, `bic`.
- Services: `currency` (Default EUR).
- Invoices: `rechnungs_code` (VAT-Category S/Z/E/AE/K, Provider-Default), `invoice_type_code` (380/381/384/389).

### UI (Server-Rendered, HTMX)
- `GET /ui` – Suche.
- `GET /ui/patient/{pid}` – Timeline, Leistungen erfassen, Draft erstellen.
- `GET /ui/invoice/{id}` – Invoice-Ansicht, Finalize, Downloads.

## Beispiel (kurz)
```bash
# Health
curl -s http://localhost:8000/health

# Draft aus offenen Leistungen
curl -X POST http://localhost:8000/workflow/invoices/draft-from-service-entries \
  -H 'Content-Type: application/json' \
  -d '{"provider_code":"PP","openemr_pid":123}'
```
