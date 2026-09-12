# Dokument: CHANGELOG
# Version: v1.7
# Letzte Änderung: 2026-01-13

## v1.7 - 2026-01-13
- PDF-/ZUGFeRD-Export behandelt Reverse-Charge (`AE`) ohne MwSt-Ausweis, ergänzt Zahlungsbedingungen im PDF/XML und synchronisiert `reverse_charge` beim Steuerkategorie-Wechsel.
- Zahlungsbedingungen können über `/ui/payment-terms` gepflegt und in Kundenmasken per Dropdown ausgewählt werden; `14TNET` (`14 Tage Net`) wird als Default-Stammdatensatz ergänzt.
- Service-Master-Edit: Zahlenfelder normalisieren Kommas zu Punkten bei der Eingabe.

## v1.6 - 2026-01-12
- Patient-UI: Betrag pro Zeile, Summen pro Tag/Encounter, Edit-Workflow mit EDIT/DEL, Live-Refresh nach Erstellen/Ändern.

## v1.5 - 2025-12-22
- Datenmodell für ZUGFeRD EN16931 vorbereitet.

## v1.4 - 2025-12-22
- Leistungskatalog (services_master): Neuanlage von Leistungen über `/ui/services-master/new` (gleicher Screen wie Bearbeiten).

## v1.3 - 2025-12-21
- Default Steuerkategorie: Provider MS jetzt auf `E` (statt `S`) beim Anlegen neuer Drafts.
- Kundenverwaltung: Übersicht mit Filtern (Firma/Nachname/Ort), Neuanlage und Bearbeiten (alle Felder inkl. Reverse Charge, ext. IDs).
- Leistungskatalog (services_master): Übersicht mit Filtern (Provider/Nummer/Beschreibung) und Bearbeiten aller Felder.

## v1.2 - 2025-02-06
- Neue Rechnungsübersicht (/ui/invoices) mit Filtern (Suche, Provider, Status, Datum) und Link von der Startseite.
- Tabelle zeigt Datum, Rechnungsnummer (klickbar), Kunde, Provider, Brutto, Status, Aktion.

## v1.1 - 2025-02-06
- Diagnosen für PP-Rechnungen: Anzeige im UI (Invoice-Detail) und im PDF vor den Positionen, begrenzt/gekürzt.

## v1.0 - 2025-02-06
- Steuerkategorie (rechnungs_code) im Draft per UI/HTMX änderbar; PATCH /invoices/{id} für rechnungs_code ergänzt.
- Invoice-Detail zeigt Tax-Select (Draft) oder Read-only (Final/Cancelled).

## v0.9 - 2025-02-06
- UI: Startseite mit OpenEMR- und Kunden-Suche (HTMX), neue Customer-Detailseite für TI/MS-Workflow ohne Encounter.
- Leistungen können direkt für invoice.customers erfasst werden; offene Leistungen sichtbar und zu Draft-Invoices zusammengeführt.
- Invoice-Header zeigt Provider-Code und Kundenname.

## v0.8 - 2025-02-06
- (interne Zwischenversion ohne öffentliche Doku)

## v0.7 - 2025-02-06
- PDF: fester Headerbereich (Fensterkuvert-stabil), Steuer/ID-Block unter Summen mit Reverse-Charge/Exempt-Hinweisen.
- Anzeige von Steuerkategorie (S/Z/E/AE/K), Invoice-Type-Code und VAT/TAX-IDs in UI und PDF.
- Datenanreicherung um vat_category/invoice_type_code für Exporte.

## v0.6 - 2025-02-06
- (interne Wartungsversion, kleinere Fixes, keine gesonderte Doku)

## v0.5 - 2025-02-06
- Neue Stammdaten: VAT-Kategorien (S/Z/E/AE/K), invoice_type_code (380/381/384/389), Länder-Tabelle.
- Kunden/Provider: tax_id, vat_id; Services: currency=EUR; Default Zahlungsbedingung SOFORT.
- Invoices: rechnungs_code, invoice_type_code mit Provider-abhängigem Default (Trigger).
- UI: einfache Provider/Customer-Edit-Forms, Anzeige der neuen Codes im Invoice-Header.
- ZUGFeRD-Lite-Check robuster.

## v0.4 - 2025-02-06
- Storno-Workflow: invoice_cancel(), Gutschrift mit negativen Lines, Re-Open Service Entries.
- UI: Storno-Button, Hinweise zu Storno/Gutschrift, PDF-Layout verbessert.
- Docs aktualisiert, VERSION auf v0.4.
- ZUGFeRD Lite Check Endpoint ergänzt.

## v0.3 - 2025-02-06
- OpenEMR Read-Model + Sync (patients, encounters, clinical_notes, problems).
- Service Entries + Workflow: Draft aus offenen Leistungen, Finalize via DB-Logik.
- HTMX UI: Patientensuche, Encounter-Ansicht, Leistungen erfassen, Draft-Invoice erzeugen, Finalisierung anstoßen.
- Exports: PDF + ZUGFeRD-Export-Routen.
