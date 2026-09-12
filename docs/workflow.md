# Dokument: Workflow
# Version: v1.3
# Letzte Änderung: 2026-01-07

## Ablauf (Kurz)
1. OpenEMR-PID verwenden → Auto-Provisioning eines Customers (`external_system='openemr', external_id=pid`).
2. Service Entries erfassen (provider_code TI/PP/MS, service_id aus services_master, optional encounter_id).
3. Draft aus offenen Leistungen erstellen (`/workflow/invoices/draft-from-service-entries` oder UI-Button).
4. Invoice bei Bedarf erweitern (Lines, Diagnosen) solange Status=draft.
5. Finalisieren (`/invoices/{id}/finalize`): vergibt Nummer, berechnet totals/hash, sperrt Updates/Deletes.

## Regeln
- Status `draft`: editierbar (Lines, Diagnosen, Service Entries).
- Status `final`: durch Trigger gesperrt (Headers/Lines), nur Finalize-Funktion erlaubt.
- Nummernkreis: pro Provider-Code/Jahr, Format `<CODE><YY><NNNNN>`.
- Reverse Charge: falls gesetzt, VAT-Berechnung entfällt.

## Stammdaten / Hinweise
- Provider-Code: TI/PP/MS.
- services_master: Preis (standard_einzelpreis), Faktor (standard_faktor), MwSt-Satz, Kommentar-Template.
- payment_terms optional je Kunde; `payment_terms_text` wird in die Rechnung kopiert.
- openemr-Schema: patients, encounters, clinical_notes, problems (read-only Spiegel für UI/Zuordnung).

## Storno und Korrektur
- Voraussetzung: Original-Rechnung ist `final` und nicht storniert.
- Ablauf: `/invoices/{id}/cancel` erzeugt Gutschrift (status=final, negative Lines), setzt Original auf `cancelled`, öffnet Leistungen neu als service_entries (status=open).
- Gutschrift bekommt eigene Rechnungsnummer und verweist via `cancellation_of_invoice_id` auf das Original.
- UI: Storno-Button bei finalen Rechnungen; Hinweis/Link bei stornierten bzw. Gutschrift-Rechnungen.

## Steuerlogik / Rechnungscode
- VAT-Kategorien (`vat_category`): S (Standard), Z (0 %), E (steuerbefreit), AE (Reverse Charge), K (innergemeinschaftliche Lieferung).
- Invoices: `rechnungs_code` Default nach Provider (PP→E, TI→S, MS→E, sonst S).
- `invoice_type_code`: 380/381/384/389 (Standard/Gutschrift/Korrektur/Abschlag).
- Auf der Rechnung (UI/PDF) werden Rechnungscode + Bedeutung sowie VAT-/TAX-IDs von Provider/Kunde unterhalb der Summen ausgewiesen; Reverse-Charge/Exempt-Hinweise erscheinen dort.
- Drafts: `rechnungs_code` kann in der UI geändert werden, bevor finalisiert wird; finale/cancelled Rechnungen sind read-only.
- Diagnosen: nur für Provider PP; werden vor Positionsliste angezeigt (UI & PDF, Seite 1, begrenzt/ggf. gekürzt).

## Steuerlogik und ZUGFeRD-Vorbereitung
- Steuerkategorien: S (Standard), Z (0 %), E (steuerbefreit), AE (Reverse Charge), K (innergemeinschaftliche Lieferung).
- Invoice-weite Steuerlogik: `invoices.rechnungs_code` bleibt die primäre Quelle fuer Steuerhinweise/Logik.
- Optionale Line-spezifische Steuerlogik: `invoice_lines.rechnungs_code` kann kuenftig pro Position gesetzt werden (z.B. Mischrechnungen).
- Vererbungsregel: Falls eine Line keinen Code hat, erbt sie den Invoice-Code (`line.rechnungs_code ?? invoice.rechnungs_code`).
- UI heute: nutzt bewusst nur die Invoice-Ebene, um Draft/Finalize einfach zu halten.
- Spaeter: Mischrechnungen moeglich, indem einzelne Lines eigene Codes tragen; die Vererbung bleibt als Fallback erhalten.

## ZUGFeRD HeaderTradeSettlement (Basic): Reihenfolge + minimale Pflichtfelder
- Reihenfolge: `InvoiceCurrencyCode` -> `SpecifiedTradeSettlementPaymentMeans` -> `ApplicableTradeTax` -> `SpecifiedTradeSettlementHeaderMonetarySummation`.
- ApplicableTradeTax: `CalculatedAmount` -> `TypeCode` -> `CategoryCode` -> `RateApplicablePercent` (ohne BasisAmount).
- MonetarySummation: `LineTotalAmount` -> `ChargeTotalAmount` -> `AllowanceTotalAmount` -> `TaxBasisTotalAmount` -> `TaxTotalAmount` (mit `currencyID="EUR"`) -> `GrandTotalAmount` -> `DuePayableAmount`.

## TI/MS Workflow ohne OpenEMR
- Kundensuche über `/ui` (Invoice-Kunden).
- Leistungen direkt auf `invoice.service_entries` (ohne Encounter) mit Provider TI/MS erfassen.
- Offene Leistungen pro Kunde+Provider einsehen und als Draft-Rechnung übernehmen.
