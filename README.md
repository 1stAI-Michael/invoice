# Invoice API

Eigenentwickeltes Abrechnungssystem für eine Arztpraxis mit angeschlossener IT-Dienstleistung
und Apotheke: Leistungserfassung, Rechnungsentwurf, Finalisierung mit fortlaufenden
Nummernkreisen je Leistungserbringer, Storno, PDF-Export mit eingebettetem ZUGFeRD/EN16931-XML.
FastAPI + async SQLAlchemy (asyncpg), serverseitig gerenderte HTMX-Oberfläche, Geschäftslogik
als Trigger und Funktionen in PostgreSQL. Hintergrund und Architektur: `Invoice_API_Blogpost.md`.

Dieses Repository enthält alles, was der Dienst zum Laufen braucht: REST-API, UI (Templates),
PDF/ZUGFeRD-Engine, SQL-Migrationen, Dockerfile und ein Compose-Beispiel.

## Inhalt

| Pfad | Was |
|---|---|
| `app/` | FastAPI-Anwendung: Routen (`routes_*.py`), UI (`routes_ui.py` + `templates/`), PDF (`pdf_renderer.py`), ZUGFeRD (`zugferd.py`, `zugferd_lite_validator.py`), DB (`db.py`, `settings.py`), Migrations-Runner (`sql_runner.py`) |
| `sql/` | 15 Migrationen `001`–`015`, idempotent (IF NOT EXISTS, DO-Blöcke, `ON CONFLICT DO NOTHING`) |
| `docs/` | API, Integration (Aufrufbeispiele), UI, Workflow, Entwicklernotizen, Changelog |
| `Dockerfile`, `docker-compose.yml`, `.env.example` | Build und Betrieb |

## Voraussetzungen

* Docker mit Compose-Plugin
* PostgreSQL 14+ (erreichbar aus dem Container). Die Anwendung nutzt das Schema `invoice`
  und liest optional das Schema `openemr` (Read-Only-Spiegel aus OpenEMR, siehe unten).

## Start

```bash
cp .env.example .env          # Werte eintragen, Datei bleibt lokal (.gitignore)
docker compose up -d --build
curl -s http://127.0.0.1:8010/health      # {"status":"ok"}
```

UI: `http://127.0.0.1:8010/ui` · API-Doku (OpenAPI): `http://127.0.0.1:8010/docs`

**Erster Start gegen eine leere Datenbank:** einmal mit `RUN_SQL_MIGRATIONS=true` starten. Der
Runner fährt `sql/*.sql` in Dateireihenfolge komplett durch, ohne Buchführung. Danach die
Variable wieder auf `false` setzen. Die Dateien sind wiederholbar geschrieben, aber ein Lauf
gegen eine Produktionsdatenbank sollte eine bewusste Entscheidung sein, kein Default.

### Umgebungsvariablen

| Variable | Bedeutung | Default (`app/settings.py`) |
|---|---|---|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | PostgreSQL-Zugang | `localhost`, `5432`, `n8n`, `postgres`, leer |
| `DB_SSLMODE` | `disable` · `require` · `verify-full` | `disable` |
| `DB_SCHEMA` | Schema für `search_path`; wird beim Start per `CREATE SCHEMA IF NOT EXISTS` angelegt | `invoice` |
| `RUN_SQL_MIGRATIONS` | `true` = `sql/*.sql` beim Start ausführen | `false` |
| `TZ` | Zeitzone | `UTC` |
| `INVOICE_BIND`, `INVOICE_API_PORT` | nur Compose: Host-Adresse und -Port der Veröffentlichung | `127.0.0.1`, `8010` |

Alle Tabellenreferenzen im Code sind schemaqualifiziert (`invoice.`, `openemr.`). `DB_SCHEMA`
steuert nur `search_path` und das `CREATE SCHEMA` beim Start. Ein falscher Wert legt ein
leeres Schema an und läuft ansonsten normal weiter. Nach dem ersten Start lohnt ein Blick in
`\dn`.

### Rechte der Datenbankrolle

Der Dienst braucht keinen Superuser. Die Funktionen sind nicht `SECURITY DEFINER`, die
Rolle braucht also auch Rechte auf die Tabellen, die die Funktionen intern anfassen:

```
invoice   USAGE                            Schema
invoice   SELECT, INSERT, UPDATE, DELETE   customers, providers, services_master, invoices,
                                           invoice_lines, invoice_diagnoses, service_entries,
                                           payment_terms, invoice_number_sequences
invoice   SELECT                           countries, vat_category, invoice_type_code, ziffernkette
invoice   USAGE                            alle Sequenzen
invoice   EXECUTE                          invoice_finalize(bigint), invoice_cancel(bigint, text),
                                           invoice_next_number(text, date)
openemr   USAGE                            Schema
openemr   SELECT                           patients, encounters, problems, clinical_notes
```

Für den Migrationslauf (`RUN_SQL_MIGRATIONS=true`) braucht die Rolle zusätzlich `CREATE`
auf beiden Schemas beziehungsweise Eigentümerschaft der Objekte.

## Sicherheit

**Der Dienst hat keine eigene Authentifizierung.** Wer den Port erreicht, kann Rechnungen
anlegen, finalisieren, stornieren und Entwürfe löschen. Das Compose-Beispiel veröffentlicht
den Port deshalb nur auf `127.0.0.1`. Für den Betrieb im Netz gehört der Dienst hinter einen
Reverse-Proxy mit Zugangskontrolle (TLS, Auth, Quell-Adressen) oder auf eine Adresse, die nur
berechtigte Clients erreichen. Niemals auf `0.0.0.0` ohne etwas davor.

## OpenEMR-Anbindung

Patientensuche, Encounter-Liste, Diagnosen und Notizen lesen das Schema `openemr`, einen
Read-Only-Spiegel der OpenEMR-Datenbank (MariaDB). Der Spiegel wird von einem separaten
Sync-Container gefüllt, der **nicht** Teil dieses Repositories ist. Ohne Spiegel laufen alle
Rechnungsfunktionen; nur die Patientenseiten zeigen dann keine oder alte Daten. Struktur des
Spiegels: `sql/003_openemr_read_model.sql`.

## API in Kürze

* `GET /health`
* `GET /customers?q=&limit=` · Kundenstamm (Anlegen und Pflege über die UI)
* `GET /services?provider_code=TI|PP|MS` · Leistungskatalog je Leistungserbringer
* `GET|POST /service-entries` · `POST /service-entries/batch` · `PATCH|DELETE /service-entries/{id}` · Leistungsjournal
* `POST /invoices/draft` · `POST /invoices/{id}/lines` · `POST /invoices/{id}/add-lines-from-service-entries`
* `POST /invoices/{id}/finalize` · `POST /invoices/{id}/cancel`
* `POST /workflow/invoices/draft-from-service-entries` · Entwurf direkt aus offenen Leistungen
* `GET /exports/invoices/{id}/pdf` · PDF mit eingebettetem ZUGFeRD-XML · `GET /exports/invoices/{id}/zugferd` · nur XML
* HTMX-UI unter `/ui`

Details: `docs/api.md`, `docs/integration.md` (vollständige Aufrufe), `docs/workflow.md`, `docs/ui.md`, `docs/dev.md`, `docs/CHANGELOG.md`.

## Entwicklung

`app/` und `sql/` sind im Compose-Beispiel als Read-Only-Mounts eingehängt: Codeänderung plus
`docker compose restart invoice-api`, kein Neubau nötig. Neue Pakete: `requirements.txt`
ändern und `docker compose up -d --build`.

## Lizenz

MIT, siehe `LICENSE`.
