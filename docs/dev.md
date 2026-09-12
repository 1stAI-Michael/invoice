# Dokument: Dev
# Version: v1.0
# Letzte Änderung: 2025-02-06

## Setup / Start
```bash
docker compose up -d --build invoice-api openemr-sync
```
UI: http://localhost:8010/ui  
Health: http://localhost:8010/health

## ENV (aus /srv/Container/.env)
- DB: `VIVO_DB_HOST`, `VIVO_DB_PORT`, `VIVO_DB_NAME`, `VIVO_DB_USER`, `VIVO_DB_PASSWORD`, `VIVO_DB_SSLMODE`, `VIVO_DB_SCHEMA` (Standard invoice).
- OpenEMR Sync: `OPENEMR_DB_HOST`, `OPENEMR_DB_PORT`, `OPENEMR_DB_NAME`, `OPENEMR_DB_USER`, `OPENEMR_DB_PASSWORD`, `OPENEMR_SYNC_INTERVAL_SECONDS`, `OPENEMR_FULL_SYNC_ON_START`.
- Misc: `TZ`, `INVOICE_API_PORT`.
- ZUGFeRD Felder: Provider IBAN/BIC, Services currency (char(3), Default EUR), Kunden Zahlungsbedingung Default SOFORT.
- Stammdaten: `vat_category` (S/Z/E/AE/K), `invoice_type_code` (380/381/384/389); Provider/Kunde mit `vat_id`/`tax_id`.

## Logs / Debug
- `docker compose logs -f invoice-api`
- `docker compose logs -f openemr-sync`
- Health: `curl http://localhost:8000/health` (im Container-Netz)

## DB-Schema
- Schema `invoice`: customers, providers, services_master, service_entries, invoices, invoice_lines, invoice_diagnoses, payment_terms, invoice_number_sequences, triggers/Funktionen (invoice_finalize).
- Schema `openemr`: patients, encounters, clinical_notes, problems (read-only Spiegel), gepflegt durch openemr-sync.

## ZUGFeRD / EN16931 Datenmodell
- Mapping (CII):
  - `invoices.invoice_currency_code` → CII `InvoiceCurrencyCode`
  - `invoices.payment_means_code` → CII `PaymentMeansCode`
  - `invoice_lines.unit_code` → CII `UnitCode`
  - `invoice_lines.rechnungs_code` → CII `TaxCategoryCode`
- CII Root Reihenfolge: Context -> Document -> Transaction.
- DateTimeString format=102 (YYYYMMDD).
- Keine Trigger fuer Vererbung: Die Logik `line.rechnungs_code ?? invoice.rechnungs_code` bleibt bewusst im Code/XML, damit DB-Constraints schlicht bleiben und keine stillen Nebenwirkungen bei Draft-Aenderungen entstehen.
- Defaults: `EUR`, `58`, `C62` setzen einen gueltigen Standard fuer ZUGFeRD, reduzieren UI-Komplexitaet und minimieren Backfill-Aufwand.
- Snapshots: Finalize kopiert Provider/Kunde samt IDs, Country ISO2 und Zahlungsinformationen ins JSON, damit Exporte revisionssicher bleiben, auch wenn Stammdaten spaeter angepasst werden.

## CII Namespaces & Struktur
- Keine Default-Namespaces; Prefixe rsm/ram/udt explizit setzen.
- Seller/Buyer stehen unter `ram:ApplicableHeaderTradeAgreement`.
- Empfohlene Prefixe: `rsm` (CrossIndustryInvoice), `ram` (ReusableAggregateBusinessInformationEntity), `udt` (UnqualifiedDataType).
- SupplyChainTradeTransaction Reihenfolge: LineItems -> Agreement -> HeaderDelivery -> HeaderSettlement.
- LineID muss als `ram:LineID` unter `ram:AssociatedDocumentLineDocument` stehen.
- Product muss über `ram:SpecifiedTradeProduct` angegeben werden.

Beispiel (Top-Level):
```xml
<rsm:CrossIndustryInvoice
  xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
  xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
  xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocumentContext>
    <ram:GuidelineSpecifiedDocumentContextParameter>
      <ram:ID>urn:cen.eu:en16931:2017#compliant#urn:zugferd.de:2p1:basic</ram:ID>
    </ram:GuidelineSpecifiedDocumentContextParameter>
  </rsm:ExchangedDocumentContext>
  <rsm:ExchangedDocument>
    <ram:ID>TI2400001</ram:ID>
    <ram:TypeCode>380</ram:TypeCode>
    <ram:IssueDateTime>
      <udt:DateTimeString format="102">20260107</udt:DateTimeString>
    </ram:IssueDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty><ram:Name>Provider GmbH</ram:Name></ram:SellerTradeParty>
      <ram:BuyerTradeParty><ram:Name>Max Mustermann</ram:Name></ram:BuyerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
```

Beispiel (LineItem):
```xml
<ram:IncludedSupplyChainTradeLineItem>
  <ram:AssociatedDocumentLineDocument>
    <ram:LineID>1</ram:LineID>
  </ram:AssociatedDocumentLineDocument>
  <ram:SpecifiedTradeProduct>
    <ram:Name>Leistung A</ram:Name>
  </ram:SpecifiedTradeProduct>
  <ram:SpecifiedLineTradeAgreement>
    <ram:GrossPriceProductTradePrice><ram:ChargeAmount>100.00</ram:ChargeAmount></ram:GrossPriceProductTradePrice>
    <ram:NetPriceProductTradePrice><ram:ChargeAmount>100.00</ram:ChargeAmount></ram:NetPriceProductTradePrice>
  </ram:SpecifiedLineTradeAgreement>
  <ram:SpecifiedLineTradeDelivery>
    <ram:BilledQuantity unitCode="C62">1.00</ram:BilledQuantity>
  </ram:SpecifiedLineTradeDelivery>
  <ram:SpecifiedLineTradeSettlement>
    <ram:ApplicableTradeTax>
      <ram:TypeCode>VAT</ram:TypeCode>
      <ram:CategoryCode>S</ram:CategoryCode>
      <ram:RateApplicablePercent>19.00</ram:RateApplicablePercent>
    </ram:ApplicableTradeTax>
    <ram:SpecifiedTradeSettlementLineMonetarySummation>
      <ram:LineTotalAmount>100.00</ram:LineTotalAmount>
    </ram:SpecifiedTradeSettlementLineMonetarySummation>
  </ram:SpecifiedLineTradeSettlement>
</ram:IncludedSupplyChainTradeLineItem>
```

## CII LineItem Struktur (schema-konform)
- Mapping:
  - `menge` -> `ram:BilledQuantity`
  - `unit_code` -> `ram:BilledQuantity@unitCode` (Fallback `C62`)
  - `einzelpreis` -> `ram:GrossPriceProductTradePrice/ram:ChargeAmount` und `ram:NetPriceProductTradePrice/ram:ChargeAmount`
  - `gesamtpreis` -> `ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount`
  - `rechnungs_code` -> `ram:ApplicableTradeTax/ram:CategoryCode` (Line, sonst Invoice)
  - `mwst_satz` -> `ram:ApplicableTradeTax/ram:RateApplicablePercent` (0.00 bei E/AE/K)

Beispiel (LineItem):
```xml
<ram:IncludedSupplyChainTradeLineItem>
  <ram:AssociatedDocumentLineDocument>
    <ram:LineID>1</ram:LineID>
  </ram:AssociatedDocumentLineDocument>
  <ram:SpecifiedTradeProduct>
    <ram:Name>Leistung A</ram:Name>
  </ram:SpecifiedTradeProduct>
  <ram:SpecifiedLineTradeAgreement>
    <ram:GrossPriceProductTradePrice><ram:ChargeAmount>100.00</ram:ChargeAmount></ram:GrossPriceProductTradePrice>
    <ram:NetPriceProductTradePrice><ram:ChargeAmount>100.00</ram:ChargeAmount></ram:NetPriceProductTradePrice>
  </ram:SpecifiedLineTradeAgreement>
  <ram:SpecifiedLineTradeDelivery>
    <ram:BilledQuantity unitCode="C62">1.00</ram:BilledQuantity>
  </ram:SpecifiedLineTradeDelivery>
  <ram:SpecifiedLineTradeSettlement>
    <ram:ApplicableTradeTax>
      <ram:TypeCode>VAT</ram:TypeCode>
      <ram:CategoryCode>S</ram:CategoryCode>
      <ram:RateApplicablePercent>19.00</ram:RateApplicablePercent>
    </ram:ApplicableTradeTax>
    <ram:SpecifiedTradeSettlementLineMonetarySummation>
      <ram:LineTotalAmount>100.00</ram:LineTotalAmount>
    </ram:SpecifiedTradeSettlementLineMonetarySummation>
  </ram:SpecifiedLineTradeSettlement>
</ram:IncludedSupplyChainTradeLineItem>
```

## HeaderTradeSettlement Reihenfolge + Tax breakdown + PaymentMeans
- Reihenfolge: `InvoiceCurrencyCode` -> `SpecifiedTradeSettlementPaymentMeans` -> `ApplicableTradeTax` -> `SpecifiedTradeSettlementHeaderMonetarySummation`.
- PaymentMeans: TypeCode `58`, IBAN/BIC aus Provider-Snapshot (falls leer, Felder weglassen).
- Tax breakdown: Gruppierung nach `(tax_category, rate)` mit `BasisAmount` und `CalculatedAmount`.
- Summation: `LineTotalAmount`, `ChargeTotalAmount`, `AllowanceTotalAmount`, `TaxBasisTotalAmount`, `TaxTotalAmount` (mit `currencyID="EUR"`), `GrandTotalAmount`, `DuePayableAmount`.

Beispiel (HeaderTradeSettlement):
```xml
<ram:ApplicableHeaderTradeSettlement>
  <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
  <ram:SpecifiedTradeSettlementPaymentMeans>
    <ram:TypeCode>58</ram:TypeCode>
    <ram:PayeePartyCreditorFinancialAccount>
      <ram:IBANID>DE12500105170648489890</ram:IBANID>
    </ram:PayeePartyCreditorFinancialAccount>
    <ram:PayeeSpecifiedCreditorFinancialInstitution>
      <ram:BICID>COBADEFFXXX</ram:BICID>
    </ram:PayeeSpecifiedCreditorFinancialInstitution>
  </ram:SpecifiedTradeSettlementPaymentMeans>
  <ram:ApplicableTradeTax>
    <ram:TypeCode>VAT</ram:TypeCode>
    <ram:CategoryCode>S</ram:CategoryCode>
    <ram:RateApplicablePercent>19.00</ram:RateApplicablePercent>
    <ram:BasisAmount>100.00</ram:BasisAmount>
    <ram:CalculatedAmount>19.00</ram:CalculatedAmount>
  </ram:ApplicableTradeTax>
  <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    <ram:LineTotalAmount>100.00</ram:LineTotalAmount>
    <ram:ChargeTotalAmount>0.00</ram:ChargeTotalAmount>
    <ram:AllowanceTotalAmount>0.00</ram:AllowanceTotalAmount>
    <ram:TaxBasisTotalAmount>100.00</ram:TaxBasisTotalAmount>
    <ram:TaxTotalAmount currencyID="EUR">19.00</ram:TaxTotalAmount>
    <ram:GrandTotalAmount>119.00</ram:GrandTotalAmount>
    <ram:DuePayableAmount>119.00</ram:DuePayableAmount>
  </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
</ram:ApplicableHeaderTradeSettlement>
```
## Entwicklung / Erweiterung
- Neue API-Routen: unter `app/routes_*.py` anlegen und in `app/main.py` registrieren.
- UI-Erweiterungen: Jinja/HTMX in `app/routes_ui.py` und `app/templates/`.
- DB-Anpassungen: SQL-Dateien unter `sql/` erstellen, optional mit sql_runner via ENV `RUN_SQL_MIGRATIONS=true`.
- Exports: PDF/ZUGFeRD-Logik in `app/pdf_renderer.py`, `app/zugferd.py`, Routen in `app/routes_exports.py`.
- ZUGFeRD Lite Check: `GET /exports/invoices/{id}/zugferd/validate-lite` (embedded XML/CII-Prüfung ohne externe Tools).

## Versionierung der Doku
- Bei Code-/Feature-Änderungen: `docs/VERSION.txt` anheben, `docs/CHANGELOG.md` ergänzen, betroffene `docs/*.md` aktualisieren.
