# Dokument: UI
# Version: v1.6
# Letzte Änderung: 2026-01-12

## URLs
- `/ui` – Startseite mit OpenEMR-Suche und Kunden-Suche.
- `/ui/patient/{pid}` – Timeline, Diagnosen, Encounter-Notizen, Leistungen erfassen, offene Leistungen, Draft-Rechnungen.
- `/ui/customer/{id}` – Kunde (TI/MS) ohne Encounter: Leistungen erfassen, offene Leistungen, Draft erstellen.
- `/ui/invoice/{id}` – Rechnung anzeigen, Finalize, PDF/ZUGFeRD herunterladen.
- `/ui/customers` – Kundenübersicht mit Filtern (Firma/Nachname/Ort), Links zum Bearbeiten/Detail und Button „Neuen Kunden anlegen“.
- `/ui/customers/new` und `/ui/customer/{id}/edit` – Pflege aller Kundenfelder (Adresse, VAT/TAX, ext. IDs, Reverse Charge).
- `/ui/services-master` – Leistungskatalog mit Filtern (Provider/Nummer/Beschreibung), Neuanlage und Bearbeiten je Leistung.
- `/ui/services-master/new` und `/ui/services-master/{id}/edit` – Leistung anlegen bzw. bearbeiten (Formular identisch).
- `/ui/payment-terms` – Zahlungsbedingungen pflegen; Kundenanlage und Kundenbearbeitung nutzen diese Einträge als Dropdown.

## Bedienung (Kurz)
1. Patienten suchen (`/ui`), auswählen.
2. Im Patient-View pro Encounter eine Leistung erfassen (Provider/Service wählen, Datum vorbelegt, Kommentar möglich).
3. Offene Leistungen werden unten angezeigt; per Button „Rechnung aus offenen Leistungen erstellen“ wird ein Draft erzeugt.
4. Draft öffnen (`/ui/invoice/{id}`), prüfen, optional finalisieren.
5. Downloads: PDF, ZUGFeRD (nur final).
6. Alternativ TI/MS: Kunde über Kundensuche aufrufen (`/ui/customer/{id}`), Provider wählen, Leistungen erfassen, offene Leistungen sehen und Draft erzeugen.
7. Im Invoice-View werden Steuerkategorie, Invoice-Type-Code sowie VAT-/TAX-IDs von Provider/Kunde angezeigt; im PDF erscheinen diese Angaben unter den Summen.
8. Solange Draft: Steuerkategorie (S/Z/E/AE/K) per Dropdown änderbar; Final/Cancelled nur read-only.
9. Bei Provider „PP“ werden Diagnosen (falls hinterlegt) im Invoice-View angezeigt; im PDF auf Seite 1 vor den Positionen (ggf. gekürzt).
10. Rechnungsübersicht unter `/ui/invoices`: Filter (Suche, Provider, Status, Datum) und Tabelle mit klickbarer Rechnungsnummer.
11. Kundenübersicht unter `/ui/customers`: Filter (Firma/Nachname/Ort), Bearbeiten-Link je Kunde, Button „Neuen Kunden anlegen“.
12. Leistungskatalog unter `/ui/services-master`: Filterbar, neue Leistung anlegen, bestehende Leistungen bearbeiten.
13. Zahlungsbedingungen unter `/ui/payment-terms`: neue Terms anlegen, bestehende Texte bearbeiten und bei Kunden auswählen.
14. In der Patient-Ansicht: Beträge pro Zeile, Summen pro Tag/Encounter und inline Edit per EDIT/DEL.
