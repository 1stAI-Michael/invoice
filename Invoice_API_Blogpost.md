# Invoice API — Ein vollständiges medizinisches Abrechnungssystem mit Python und FastAPI

## Zusammenfassung

Die **Invoice API** ist ein eigenentwickeltes, containerisiertes Abrechnungssystem für medizinische Leistungserbringer. Es wurde als Teil eines größeren Self-Hosted-Stacks konzipiert und deckt den gesamten Rechnungslebenszyklus ab: von der Erfassung erbrachter Leistungen über die Rechnungserstellung bis zum ZUGFeRD-konformen PDF-Export mit eingebettetem XML nach EN16931.

Das System ist bewusst schlank gehalten — kein SPA-Framework, kein ORM-Magic, keine externen SaaS-Abhängigkeiten. Stattdessen: FastAPI mit async SQLAlchemy, serverseitiges Rendering mit HTMX, und eine PostgreSQL-Datenbank, die Geschäftslogik dort durchsetzt, wo sie hingehört — in Triggern und Funktionen.

---

## Motivation und Kontext

In einer ärztlichen Praxis mit angeschlossener IT-Dienstleistung und Apotheke fehlte ein einheitliches Abrechnungstool, das:

- **Mehrere Leistungserbringer** (Arzt, IT, Apotheke) in einem System abbildet
- **Steuerlich korrekt** arbeitet — inkl. §4 UStG-Befreiung für ärztliche Leistungen, Reverse Charge und innergemeinschaftliche Lieferungen
- **Revisionssicher** ist — finalisierte Rechnungen dürfen nachträglich nicht verändert werden
- **OpenEMR-Patientendaten** integriert, ohne das bestehende Praxisverwaltungssystem zu ersetzen
- **ZUGFeRD-konforme E-Rechnungen** erzeugt, wie sie in Deutschland zunehmend Pflicht werden

Kommerzielle Lösungen waren entweder zu generisch, zu teuer oder nicht self-hosted-fähig. Also wurde das System von Grund auf selbst entwickelt.

---

## Architektur im Überblick

```
┌─────────────────────────────────────────────────────┐
│                    Caddy (Reverse Proxy)             │
│                  TLS, IP-Whitelist, Basic Auth       │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│              Invoice API (FastAPI + Uvicorn)         │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ REST API │  │  HTMX UI │  │  PDF/ZUGFeRD      │  │
│  │ 30+ Endp.│  │ 25+ Tmpl.│  │  Export Engine     │  │
│  └────┬─────┘  └────┬─────┘  └────────┬──────────┘  │
│       │              │                 │              │
│  ┌────▼──────────────▼─────────────────▼──────────┐  │
│  │       Async SQLAlchemy + asyncpg               │  │
│  └────────────────────┬───────────────────────────┘  │
└───────────────────────┼──────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────┐
│              PostgreSQL (Schema: invoice)             │
│                                                       │
│  Trigger: Immutabilität finalisierter Rechnungen     │
│  Funktionen: invoice_finalize(), invoice_cancel()    │
│  10+ Tabellen, 13 Migrationen                        │
│                                                       │
│  ┌─────────────────────────────────────────────┐     │
│  │  Schema: openemr (Read-Only Mirror)         │     │
│  │  Patienten, Behandlungen, Diagnosen         │     │
│  └─────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────┘
```

### Tech Stack

| Schicht | Technologie |
|---------|-------------|
| **Runtime** | Python 3.10, Uvicorn |
| **Framework** | FastAPI (vollständig async) |
| **ORM** | SQLAlchemy 2.0 (async) + asyncpg |
| **Validierung** | Pydantic v2 |
| **Frontend** | Jinja2 Templates + HTMX (Server-Side Rendering) |
| **PDF-Erzeugung** | ReportLab (programmatisches Layout) |
| **E-Rechnung** | PyPDF (ZUGFeRD XML-Einbettung in PDF) |
| **Datenbank** | PostgreSQL mit PL/pgSQL-Geschäftslogik |
| **Infrastruktur** | Docker, Live-Code-Mount, automatische Migrationen |

---

## Das Datenmodell

### Drei Leistungserbringer, fünf Steuerkategorien

Das System unterstützt drei Provider-Codes mit jeweils eigenen Nummernkreisen und Steuer-Defaults:

| Code | Bereich | Standard-Steuerkategorie |
|------|---------|--------------------------|
| **PP** | Arztpraxis (Physician) | E — Steuerbefreit nach §4 UStG |
| **TI** | IT-Dienstleistungen | S — Standard 19% |
| **MS** | Apotheke (Pharmacy) | E — Steuerbefreit |

Die fünf Steuerkategorien decken alle praxisrelevanten Szenarien ab:

- **S** (Standard): 19% MwSt — der Normalfall für IT-Leistungen
- **E** (Exempt): 0%, §4 UStG — ärztliche Heilbehandlungen
- **Z** (Zero-rated): 0%, aber steuerpflichtig — Sonderfälle
- **AE** (Reverse Charge): Steuerschuldnerschaft des Empfängers — Bauleistungen, Auslandsgeschäfte
- **K** (Innergemeinschaftlich): Umsatzsteuerfreie Lieferung innerhalb der EU

### Kernentitäten

```
Providers (TI, PP, MS)
    │
    ├── Services Master (Leistungskatalog pro Provider)
    │       │
    │       ▼
    ├── Service Entries (Leistungsjournal: offen → abgerechnet)
    │       │
    │       ▼
    └── Invoices (Entwurf → Finalisiert → Storniert)
            │
            ├── Invoice Lines (Positionen mit Snapshot)
            ├── Invoice Diagnoses (ICD-Codes, nur PP)
            └── Number Sequences (TI2600001, PP2600002...)
```

---

## Der Rechnungs-Workflow im Detail

### Phase 1: Leistungserfassung

Jede erbrachte Leistung wird als **Service Entry** erfasst — entweder manuell über die Web-UI oder automatisch über die OpenEMR-Integration. Ein Service Entry enthält:

- Leistungserbringer (Provider)
- Kunde/Patient (mit optionaler OpenEMR-PID)
- Leistung aus dem Katalog (oder Freitext)
- Datum, Menge, Faktor, Kommentar
- Status: **open** (noch nicht abgerechnet)

Bei der ersten Leistungserfassung für einen OpenEMR-Patienten wird automatisch ein Kundendatensatz angelegt (**Auto-Provisioning**).

### Phase 2: Rechnungsentwurf

Über den Workflow-Endpoint werden offene Service Entries zu einem Rechnungsentwurf zusammengefasst:

```
POST /workflow/invoices/draft-from-service-entries
```

Dabei geschieht atomar:
1. Neue Rechnung im Status **draft** wird erstellt
2. Rechnungspositionen werden aus den Service Entries generiert
3. Für jede Position wird ein **Snapshot** der Leistungsdaten gespeichert
4. Die Service Entries werden auf **invoiced** gesetzt

### Phase 3: Finalisierung

Die Finalisierung ist als **PL/pgSQL-Funktion** (`invoice_finalize()`) implementiert — nicht als Applikationslogik. Das ist eine bewusste Designentscheidung: Egal welcher Client die Datenbank anspricht, die Geschäftsregeln werden durchgesetzt.

Was bei der Finalisierung passiert:
- **Rechnungsnummer** wird aus dem Nummernkreis vergeben (z.B. `TI2600042`)
- **JSON-Snapshots** von Kunde und Leistungserbringer werden gespeichert (Name, Adresse, Steuer-IDs, IBAN/BIC)
- **Hash** wird berechnet (Integritätsprüfung)
- Status wechselt auf **final**
- **Trigger** blockieren ab sofort jede Änderung an Rechnung und Positionen

#### Warum Snapshots?

Eine finalisierte Rechnung muss auch in 10 Jahren exakt reproduzierbar sein. Wenn sich die Adresse des Leistungserbringers ändert, darf das die historische Rechnung nicht beeinflussen. Die JSON-Snapshots frieren den Stand zum Finalisierungszeitpunkt ein — inklusive Länder-ISO-Codes für die ZUGFeRD-Compliance.

### Phase 4: Stornierung (optional)

Stornierung erzeugt automatisch eine **Gutschrift** (Rechnungstyp 381) mit negativen Beträgen. Die zugehörigen Service Entries werden wieder auf **open** gesetzt und können einer neuen Rechnung zugeordnet werden.

```sql
-- Vereinfacht: Was invoice_cancel() in der Datenbank tut
INSERT INTO invoices (...) -- Gutschrift (status=final, type_code=381)
INSERT INTO invoice_lines (...) -- Negative Beträge
UPDATE service_entries SET status='open' WHERE invoice_id = :old_id
UPDATE invoices SET status='cancelled' WHERE id = :old_id
```

---

## Datenbank-Design: Geschäftslogik in PostgreSQL

Ein zentrales Architekturprinzip: **Kritische Geschäftsregeln gehören in die Datenbank, nicht in die Applikation.**

### Trigger-basierte Unveränderlichkeit

```sql
CREATE FUNCTION block_final_invoice_mutation() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status IN ('final', 'cancelled') THEN
        RAISE EXCEPTION 'Finalisierte Rechnungen können nicht geändert werden';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

Dieser Trigger liegt auf `invoices` und `invoice_lines`. Ein `UPDATE` oder `DELETE` auf einer finalisierten Rechnung scheitert — egal ob es vom API-Server, einem SQL-Client oder einem versehentlichen Script kommt.

### Automatische Defaults per Trigger

```sql
-- Provider-basierte Steuer-Defaults
IF NEW.provider_code = 'PP' THEN
    NEW.rechnungs_code := 'E';  -- Ärztliche Leistungen: steuerbefreit
ELSIF NEW.provider_code = 'TI' THEN
    NEW.rechnungs_code := 'S';  -- IT: 19% Standard
END IF;
```

### 13 sequenzielle Migrationen

Die Migrationen dokumentieren die Evolution des Schemas — von den Grundtabellen (001) über die Stornierungslogik (004–008) bis zur ZUGFeRD-Vorbereitung (009–013). Sie laufen automatisch beim Container-Start und sind idempotent.

---

## ZUGFeRD: E-Rechnungen nach deutschem Standard

### Was ist ZUGFeRD?

ZUGFeRD (**Z**entraler **U**ser **G**uide des **F**orums **e**lektronische **R**echnung **D**eutschland) kombiniert ein menschenlesbares PDF mit einer maschinenlesbaren XML-Datei. Der XML-Teil folgt dem europäischen Standard **EN16931** (Cross-Industry Invoice / CII) und ermöglicht die automatische Verarbeitung durch Buchhaltungssoftware.

### Implementierung

Die ZUGFeRD-Erzeugung ist zweistufig:

**1. CII-XML-Generierung:**

```
CrossIndustryInvoice
├── ExchangedDocumentContext
│   └── Guideline: urn:zugferd.de:2p1:basic
├── ExchangedDocument
│   ├── ID (Rechnungsnummer)
│   ├── TypeCode (380=Rechnung, 381=Gutschrift)
│   └── IssueDateTime
└── SupplyChainTradeTransaction
    ├── IncludedSupplyChainTradeLineItem (pro Position)
    │   ├── Produkt, Menge, Einzelpreis, Nettopreis
    │   └── Steuerkategorie + Steuersatz
    ├── ApplicableHeaderTradeAgreement
    │   ├── Seller (Name, Adresse, Steuer-IDs)
    │   └── Buyer (Name, Adresse, Steuer-IDs)
    └── ApplicableHeaderTradeSettlement
        ├── Währung (EUR)
        ├── Zahlungsmittel (IBAN/BIC, TypeCode=58)
        ├── Steueraufschlüsselung (pro Kategorie/Satz)
        └── Summen (Netto, Steuer, Brutto)
```

**2. PDF-Einbettung:**

Das generierte XML wird als Attachment in das PDF eingebettet (PyPDF). Das Ergebnis ist eine einzelne Datei, die sowohl ausgedruckt als auch maschinell verarbeitet werden kann.

**3. Validierung:**

Ein Lite-Validator (`/exports/invoices/{id}/zugferd/validate-lite`) prüft ohne externe Tools:
- Korrekte CII-Namespace-Deklarationen
- Vorhandensein aller Pflichtfelder (Dokumenten-ID, Währung, Positionen, Summen)
- Strukturelle Konsistenz

### Steuerkategorien in der Praxis

Die Komplexität steckt im Detail. Ein Arzt, der neben der Praxis auch IT-Beratung anbietet, braucht auf derselben Infrastruktur:

- **Rechnung an Patient** (PP): §4 UStG, 0% MwSt, keine Steuer-IDs nötig
- **Rechnung an Firma** (TI): 19% MwSt, eigene USt-ID
- **Rechnung an EU-Firma** (TI): Reverse Charge, beide USt-IDs, 0% MwSt
- **Gutschrift nach Storno**: Negativbeträge, gleiche Steuerkategorie wie Original

Das System bildet alle diese Fälle korrekt ab — sowohl im PDF als auch im ZUGFeRD-XML.

---

## OpenEMR-Integration

### Bidirektionale Anbindung (Read)

Ein separater Service (**openemr-sync**) spiegelt Patienten, Behandlungen, klinische Notizen und Diagnosen aus der OpenEMR-MariaDB-Datenbank in ein PostgreSQL-Schema (`openemr`). Die Invoice API liest aus diesem Mirror:

- **Patientensuche**: ILIKE-Suche über Vor-/Nachname
- **Behandlungstimeline**: Alle Encounters eines Patienten mit klinischen Notizen
- **Diagnosenliste**: Aktive ICD-Codes für die Rechnungsstellung

### Auto-Provisioning

Beim ersten Service Entry für einen OpenEMR-Patienten wird automatisch ein Kundendatensatz in der Invoice-Datenbank angelegt — mit Referenz zur OpenEMR-PID (`external_system='openemr'`, `external_id=PID`). Kein manuelles Anlegen nötig.

### Diagnosen auf der Rechnung

Für ärztliche Rechnungen (Provider PP) können ICD-Diagnosen aus OpenEMR direkt auf die Rechnung übernommen werden. Sie erscheinen im PDF oberhalb der Leistungspositionen und im ZUGFeRD-XML als Dokumentenreferenz.

---

## Frontend: HTMX statt SPA

### Die Entscheidung gegen React & Co.

Für ein internes Abrechnungstool mit überschaubarer Nutzerzahl ist ein vollwertiges SPA-Framework Overkill. Stattdessen nutzt das System **HTMX** — eine JavaScript-Bibliothek (14 KB), die HTML-Attribute für AJAX-Requests bereitstellt.

### Wie das in der Praxis aussieht

**Patientensuche mit Autocomplete:**
```html
<input type="text" name="q"
       hx-get="/openemr/patients/search"
       hx-trigger="keyup changed delay:300ms"
       hx-target="#search-results">
```

**Inline-Editing von Leistungseinträgen:**
```html
<button hx-get="/ui/service-entry/42/edit"
        hx-target="closest tr"
        hx-swap="outerHTML">Bearbeiten</button>
```

**Formular-Submission mit Redirect:**
```python
@router.post("/ui/service-entries")
async def create_service_entry(request: Request, ...):
    # ... Validierung und Speicherung ...
    return RedirectResponse(f"/ui/patient/{pid}", status_code=303)
```

Das Ergebnis: Eine reaktive Web-Oberfläche mit 25+ Templates, die sich anfühlt wie eine SPA — aber ohne Build-Step, ohne Node.js, ohne npm-Dependencies. Der gesamte Frontend-Code ist serverseitig, testbar und debuggbar.

### UI-Funktionen

- **Dashboard**: Patienten-/Kundensuche mit Live-Ergebnissen
- **Patientendetail**: Timeline, Behandlungen, Diagnosen, Leistungserfassung (Inline-Formular)
- **Rechnungsansicht**: Positionen, Steuern, Diagnosen, Finalisieren/Stornieren-Buttons, PDF/ZUGFeRD-Download
- **Rechnungsliste**: Filter nach Suche, Provider, Status, Zeitraum
- **Leistungskatalog**: CRUD für Services pro Provider
- **Stammdaten**: Kunden- und Provider-Verwaltung (Adressen, Steuer-IDs, IBAN)

---

## PDF-Erzeugung mit ReportLab

Die PDF-Engine erzeugt Rechnungen mit professionellem Layout:

- **Briefumschlag-kompatibles Fenster** (9 cm vom oberen Rand)
- **Absender/Empfänger-Block** mit korrekter Formatierung
- **Positionstabelle** mit Nummer, Beschreibung, Menge, Faktor, Einzelpreis, Gesamtpreis
- **Steueraufschlüsselung** (Netto, MwSt-Betrag pro Satz, Brutto)
- **Steuerkategorie-Erläuterung** (z.B. "Steuerbefreit nach §4 Nr. 14 UStG")
- **Zahlungsinformationen** (Zahlungsbedingung, IBAN/BIC)
- **Diagnosen** (nur PP, ICD-Codes mit Zeitraum, oberhalb der Positionen)
- **Seitennummerierung** und Rechnungsnummer im Kopf

Entwürfe werden mit "DRAFT-{id}" statt Rechnungsnummer gekennzeichnet.

---

## Deployment und Betrieb

### Docker-Container mit Live-Mount

```yaml
invoice-api:
  build: ./invoice-api
  volumes:
    - ./invoice-api/app:/app/app  # Live-Code-Mount
  environment:
    - DATABASE_URL=postgresql+asyncpg://...
```

Code-Änderungen werden sofort wirksam (Uvicorn Reload) — kein Rebuild nötig für die Entwicklung. Für Produktionsänderungen: `docker compose up -d --build invoice-api`.

### Automatische Migrationen

Beim Container-Start laufen alle 13 SQL-Migrationen sequenziell. Jede Migration ist idempotent (`IF NOT EXISTS`, Exception-Handler) — ein Neustart ist immer sicher.

### Health Check

```
GET /health → {"status": "ok"}
```

Überwacht durch Uptime Kuma im selben Stack.

---

## Technische Kennzahlen

| Metrik | Wert |
|--------|------|
| Python-Code | ~4.200 Zeilen |
| SQL-Migrationen | ~1.700 Zeilen (13 Dateien) |
| API-Endpoints | 30+ |
| Jinja2-Templates | 25+ |
| PL/pgSQL-Funktionen | 5 (finalize, cancel, block_mutation, touch_updated, defaults) |
| Datenbanktabellen | 10+ (+ Lookup-Tabellen) |
| Python-Dependencies | 11 |
| Erstellt | Q1 2025 |
| Aktuelle Version | v1.7 (Januar 2026) |

---

## Fazit

Die Invoice API zeigt, dass ein vollwertiges Abrechnungssystem keine Enterprise-Plattform braucht. Mit einem fokussierten Tech-Stack (FastAPI + PostgreSQL + HTMX) und durchdachtem Datenbankdesign lässt sich ein revisionssicheres, ZUGFeRD-konformes System bauen, das:

- **Domänenspezifisch** ist — statt generischer Rechnungssoftware eine Lösung, die ärztliche Steuerbefreiungen, OpenEMR-Integration und ICD-Diagnosen nativ versteht
- **Robust** ist — Geschäftsregeln in der Datenbank, nicht nur in der Applikation
- **Wartbar** ist — ~6.000 Zeilen Gesamtcode, kein Build-System, keine Frontend-Dependencies
- **Standardkonform** ist — ZUGFeRD/EN16931, korrekte Steuerkategorien, revisionssichere Snapshots

Das Projekt demonstriert, dass Self-Hosting und Eigenentwicklung für spezialisierte Geschäftsanwendungen nicht nur machbar, sondern oft die bessere Wahl sind — wenn man die Domäne versteht und die richtigen Architekturentscheidungen trifft.

---

*Technologie: Python 3.10 · FastAPI · SQLAlchemy 2.0 (async) · PostgreSQL · PL/pgSQL · ReportLab · ZUGFeRD/EN16931 · HTMX · Jinja2 · Docker*
