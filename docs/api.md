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


## Schreibrechte (v1.10.0, fA-2302)

- **Lesen** (`GET`) ist auf allen Routen unverändert.
- **Schreiben** (`POST`/`PUT`/`PATCH`/`DELETE`) auf den alten Routen (`/service-entries`, `/invoices`, `/customers`, `/ui`, `/exports`, `/workflow`, `/openemr`, Services) geht nur noch
  - über die Web-UI hinter dem Anmelde-Proxy. Erkannt wird sie an der TCP-Quelle aus `INVOICE_UI_QUELLEN`. Ist die Variable leer, gibt es keine Ausnahme.
  - oder mit einem Geräte-Token mit `invoice:write` (`Authorization: Bearer …`).
- Ohne Ausweis: `401 schreiben_ohne_ausweis`. Mit Token ohne Scope: `403 scope_required`. Beides kommt **vor** der Feldprüfung, ein ungültiger Rumpf ergibt also kein 422.
- Einen Platzhalter-Scope `*` gibt es nicht mehr. Jeder Scope muss namentlich am Gerät stehen.
- **Zwischenspeicher:** Ein positives Prüfergebnis von flows-api wird 60 s gemerkt (`INVOICE_TOKEN_CACHE_SECONDS`). Ein entwerteter Ausweis kann deshalb bis zu 60 s weiter schreiben. „Entwertet“ heißt also nicht „sofort dicht“.

## Agenten-Schnittstelle (v1.8.0, fA-2245)

Damit legen Schienen offene Leistungen an, ohne Schreibzugang auf die Datenbank. Es geht nur das, nichts weiter.

    POST /api/invoice/service-entries/device      Scope invoice:write
    GET  /api/invoice/service-entries/device?customer_id=123&provider_code=MS   Scope invoice:read
    Authorization: Bearer <Geraete-Token der Schiene>

Rumpf beim Anlegen (ein Kunde und ein Provider je Stapel, höchstens 500 Positionen):

```json
{
  "customer_id": 123, "provider_code": "MS",
  "erwartet_anzahl": 2, "erwartet_summe": "150.00",
  "probe": false,
  "positionen": [
    {"service_nummer": "AUSL", "leistungsdatum": "2026-03-02", "menge": "48.90", "faktor": "1.00", "kommentar": "Beispiel"}
  ]
}
```

- **Einzelpreis und Status setzt der Server:** Einzelpreis aus `services_master.standard_einzelpreis`, Status `open`. Die Summe ist `menge × faktor × standard_einzelpreis`.
- **Idempotenz:** Geprüft wird der Schlüssel (Kunde, Provider, Leistung, Leistungsdatum, Menge, Faktor, Kommentar) gegen `open` und `invoiced`, als Multimenge gezählt. Derselbe Stapel ein zweites Mal ergibt 0 angelegt und alles übersprungen. Zwei gleiche Positionen in einem Stapel ergeben zwei Einträge.
- **Wächter:** Weichen `erwartet_*` vom offenen Stand ab, antwortet die Route mit 409 `ausgangsstand_weicht_ab` und dem Ist-Stand und legt nichts an. Gibt es nichts anzulegen, wird der Wächter nicht angewandt (`waechter: nicht_angewandt_nichts_anzulegen`). ⚠️ Die Antwort ist dann **200**, auch wenn `erwartet_*` nicht stimmt. Wer nur den Statuscode prüft, liest „Wächter bestanden“, wo „Wächter übersprungen“ steht. Maßgeblich ist das Feld `waechter` (Hinweis aus der Abnahme fA-2245).
- **Alles oder nichts:** Der Stapel läuft in einer Transaktion mit einer Sperre je (Kunde, Provider). Die Antwort trägt `vorher`, `nachher` und je Position `angelegt` bzw. `uebersprungen` mit `id`.
- **Probelauf:** Mit `"probe": true` wird alles geprüft und geplant, auch der Wächter, aber nichts geschrieben. Die Antwort nennt `wuerde_angelegt` und `nachher_erwartet`.
- **Fehler:** 401 ohne Token. 403 bei ungültigem Token oder `scope_required`. 404 bei unbekanntem Kunden oder Provider. 422 `leistung_unbekannt` mit Index. 503, wenn flows-api das Token nicht beurteilen konnte.
- **Nicht enthalten:** Rechnung erstellen, Festschreiben, Löschen, Ändern, Kunden- oder Provider-Anlage.
- **Herkunft:** Das Log nennt `device_name` und die angelegten ids (`docker logs invoice-api`, rotiert). Eine Spalte „angelegt von“ gibt es noch nicht, dafür bräuchte es eine Migration auf CT107.

### Ändern (v1.9.0)

    PATCH /api/invoice/service-entries/device/{id}      Scope invoice:write

```json
{"customer_id": 123, "provider_code": "MS",
 "vorher": {"leistungsdatum": "2021-08-05"},
 "leistungsdatum": "2026-08-05"}
```

- Änderbar sind nur `leistungsdatum` und `kommentar` (oder `kommentar_leeren: true`), und nur bei Posten mit Status `open`. Menge, Faktor, Leistung, Kunde und Status bleiben, wie sie sind.
- **`vorher` ist Pflicht** für jedes geänderte Feld. Weicht der gespeicherte Wert ab, kommt 409 `vorher_weicht_ab` mit `ist`, und es wird nichts geändert.
- Passen Kunde oder Provider nicht zur id, kommt 404, die Antwort sagt nichts über fremde Posten. Ein abgerechneter Posten ergibt 409 `nicht_offen`.
- Ist der Zielwert schon gesetzt, kommt 200 mit `geaendert: false`. Die Antwort trägt `vorher`, `nachher` und `geraet`, das Log den Gerätenamen.
