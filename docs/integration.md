# Dokument: Integration
# Version: v1.0
# Letzte Änderung: 2026-09-12

Vollständige Aufrufe für ein externes System, das Leistungen erfasst und später
in Rechnungen überführen lässt. Basis-URL im Beispiel: `http://<host>:8010`
(Compose-Veröffentlichung, siehe README). Die API hat **keine Authentifizierung**;
der Zugriff wird über Bind-Adresse und Firewall begrenzt.

Provider-Codes sind fest: `TI`, `PP`, `MS`.

## 1. Kunden suchen — `GET /customers`

Parameter: `q` (Teilstring über Firma, Vorname, Nachname, Ort, externe ID, ID),
`limit` (1–500, Standard 100).

```bash
curl -s "http://<host>:8010/customers?q=muster&limit=5"
```

Antwort:

```json
[
  {
    "id": 42,
    "firma": null,
    "vorname": "Max",
    "nachname": "Mustermann",
    "ort": "Berlin",
    "external_system": "openemr",
    "external_id": 1234
  }
]
```

`external_system`/`external_id` verweisen auf die Quelle des Kunden (bei
OpenEMR-Patienten die `pid`). Alternativ zur `customer_id` kann bei der
Leistungserfassung direkt `openemr_pid` übergeben werden; der Kunde wird dann
aus dem OpenEMR-Spiegel angelegt oder wiederverwendet.

## 2. Leistungskatalog lesen — `GET /services`

Parameter: `provider_code` (Pflicht, `TI|PP|MS`).

```bash
curl -s "http://<host>:8010/services?provider_code=MS"
```

Antwort:

```json
[
  {
    "id": 7,
    "nummer": "1",
    "beschreibung": "Beratung",
    "standard_einzelpreis": "10.72",
    "mwst_satz": "0.00",
    "standard_faktor": "2.30",
    "kommentar_template": null
  }
]
```

`id` ist die `service_id` für die Leistungserfassung. `standard_einzelpreis`
und `standard_faktor` sind Vorschlagswerte; beim Rechnungsentwurf werden sie
aus dem Katalog gezogen, die Erfassung überträgt nur `menge` und `faktor`.

## 3. Leistungen erfassen — `POST /service-entries` und `POST /service-entries/batch`

Felder je Eintrag:

| Feld | Pflicht | Bedeutung |
|---|---|---|
| `provider_code` | ja | `TI`, `PP` oder `MS` |
| `customer_id` **oder** `openemr_pid` | eins von beiden | Kunde (siehe 1.) |
| `service_id` | ja | aus `GET /services` |
| `leistungsdatum` | ja | `YYYY-MM-DD` |
| `menge` | nein | Dezimal, Standard `1.00` |
| `faktor` | nein | Dezimal, Standard `1.00` |
| `external_encounter_id` | nein | Referenz auf den Behandlungsfall der Quelle |
| `kommentar` | nein | Freitext, erscheint auf der Rechnungszeile |

Einzeln:

```bash
curl -s -X POST "http://<host>:8010/service-entries" \
  -H "Content-Type: application/json" \
  -d '{
        "provider_code": "MS",
        "openemr_pid": 1234,
        "service_id": 7,
        "leistungsdatum": "2026-09-12",
        "menge": "1.00",
        "faktor": "2.30",
        "external_encounter_id": 5678,
        "kommentar": "Erstgespräch"
      }'
```

Als Batch (eine Transaktion, alles oder nichts):

```bash
curl -s -X POST "http://<host>:8010/service-entries/batch" \
  -H "Content-Type: application/json" \
  -d '{
        "entries": [
          {"provider_code": "MS", "openemr_pid": 1234, "service_id": 7,
           "leistungsdatum": "2026-09-12", "faktor": "2.30"},
          {"provider_code": "MS", "openemr_pid": 1234, "service_id": 9,
           "leistungsdatum": "2026-09-12"}
        ]
      }'
```

Antwort (einzeln ein Objekt, beim Batch eine Liste in Eingabereihenfolge):

```json
{
  "id": 901,
  "customer_id": 42,
  "provider_id": 3,
  "service_id": 7,
  "external_encounter_id": 5678,
  "leistungsdatum": "2026-09-12",
  "menge": "1.00",
  "faktor": "2.30",
  "kommentar": "Erstgespräch",
  "status": "open",
  "created_at": "2026-09-12T13:10:00.123456+00:00"
}
```

Fehlerbilder: `422` wenn weder `customer_id` noch `openemr_pid` gesetzt ist oder
`entries` leer ist; `404` wenn `customer_id` unbekannt ist.

## 4. Offene Leistungen prüfen — `GET /service-entries`

Parameter (alle optional): `provider_code`, `customer_id` oder `openemr_pid`,
`status` (`open` Standard, `invoiced`), `from_date`, `to_date`.

```bash
curl -s "http://<host>:8010/service-entries?provider_code=MS&openemr_pid=1234&status=open"
```

## 5. Weiter im Rechnungslauf

Aus offenen Einträgen entsteht ein Rechnungsentwurf über
`POST /workflow/invoices/draft-from-service-entries` (`provider_code`,
`customer_id` oder `openemr_pid`, optional `service_entry_ids`, `reverse_charge`);
die Einträge wechseln dabei auf `status=invoiced`. Einem bestehenden Entwurf
lassen sich Einträge über `POST /invoices/{id}/add-lines-from-service-entries`
anhängen. Finalisieren, Stornieren und Export (PDF, ZUGFeRD) sind in
`docs/api.md` und `docs/workflow.md` beschrieben.
