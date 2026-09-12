-- Masterdata for VAT codes, invoice type, IDs and defaults
SET search_path TO invoice, public;

-- 1) VAT Categories
CREATE TABLE IF NOT EXISTS invoice.vat_category (
    code text PRIMARY KEY,
    meaning text NOT NULL,
    tax_logic text NOT NULL,
    typical_cases text,
    comment text
);

INSERT INTO invoice.vat_category (code, meaning, tax_logic, typical_cases, comment) VALUES
('S', 'Standard rate', 'Steuerpflichtig, Steuer wird berechnet', 'Beratung ohne Heilbehandlung, Gutachten, Coaching, IGeL ohne §4', 'Normalfall außerhalb §4 UStG'),
('Z', 'Zero rated', 'Steuerpflichtig, Steuersatz = 0 %', 'Sonderfälle mit gesetzlich 0 % (selten)', 'Nicht steuerfrei! Nur 0 %'),
('E', 'Exempt', 'Steuerbefreit, keine Steuer', 'Ärztliche Heilbehandlung (§4 Nr.14 UStG), Klinikleistungen', 'Häufigster Praxisfall'),
('AE', 'Reverse Charge', 'Steuer schuldet der Empfänger', 'Bauleistungen, bestimmte Auslandsleistungen', 'Käufer führt Steuer ab'),
('K', 'Intra-Community supply', 'Steuerfrei (EU), nicht §4', 'EU-Lieferung DE → EU-Unternehmen', 'Beide USt-IDs nötig')
ON CONFLICT (code) DO NOTHING;

-- 2) Tax/VAT IDs on customers/providers
ALTER TABLE invoice.customers
    ADD COLUMN IF NOT EXISTS tax_id text,
    ADD COLUMN IF NOT EXISTS vat_id text;

ALTER TABLE invoice.providers
    ADD COLUMN IF NOT EXISTS tax_id text,
    ADD COLUMN IF NOT EXISTS vat_id text;

-- 3) Countries
CREATE TABLE IF NOT EXISTS invoice.countries (
    plate_code text PRIMARY KEY,
    iso2 text NOT NULL UNIQUE,
    name_de text NOT NULL
);

INSERT INTO invoice.countries (plate_code, iso2, name_de) VALUES
('D', 'DE', 'Deutschland'),
('A', 'AT', 'Österreich'),
('CH', 'CH', 'Schweiz'),
('PL', 'PL', 'Polen'),
('UA', 'UA', 'Ukraine'),
('RUS', 'RU', 'Russland'),
('GE', 'GE', 'Georgien'),
('USA', 'US', 'USA'),
('UK', 'GB', 'England'),
('F', 'FR', 'Frankreich')
ON CONFLICT (plate_code) DO NOTHING;

-- 4) Invoices: rechnungs_code + invoice_type_code
ALTER TABLE invoice.invoices
    ADD COLUMN IF NOT EXISTS rechnungs_code text,
    ADD COLUMN IF NOT EXISTS invoice_type_code text NOT NULL DEFAULT '380';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'invoices_rechnungs_code_fk'
          AND table_name = 'invoices'
          AND table_schema = 'invoice'
    ) THEN
        ALTER TABLE invoice.invoices
            ADD CONSTRAINT invoices_rechnungs_code_fk
            FOREIGN KEY (rechnungs_code) REFERENCES invoice.vat_category(code)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END;
$$;

-- Backfill invoice_type_code
UPDATE invoice.invoices
SET invoice_type_code = '380'
WHERE invoice_type_code IS NULL OR btrim(invoice_type_code) = '';

-- Backfill rechnungs_code based on provider
UPDATE invoice.invoices i
SET rechnungs_code = CASE p.code WHEN 'PP' THEN 'E' WHEN 'TI' THEN 'S' ELSE 'S' END
FROM invoice.providers p
WHERE i.provider_id = p.id
  AND (i.rechnungs_code IS NULL OR btrim(i.rechnungs_code) = '');

-- 5) Trigger to set defaults on insert
CREATE OR REPLACE FUNCTION invoice.set_invoice_defaults()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_code text;
BEGIN
    IF NEW.rechnungs_code IS NULL OR btrim(NEW.rechnungs_code) = '' THEN
        SELECT code INTO v_code FROM invoice.providers WHERE id = NEW.provider_id;
        NEW.rechnungs_code := CASE v_code WHEN 'PP' THEN 'E' WHEN 'TI' THEN 'S' ELSE 'S' END;
    END IF;
    IF NEW.invoice_type_code IS NULL OR btrim(NEW.invoice_type_code) = '' THEN
        NEW.invoice_type_code := '380';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_invoices_defaults ON invoice.invoices;
CREATE TRIGGER trg_invoices_defaults
BEFORE INSERT ON invoice.invoices
FOR EACH ROW
EXECUTE FUNCTION invoice.set_invoice_defaults();

-- 6) Invoice type code table + FK
CREATE TABLE IF NOT EXISTS invoice.invoice_type_code (
    code text PRIMARY KEY,
    meaning text NOT NULL
);

INSERT INTO invoice.invoice_type_code (code, meaning) VALUES
('380', 'Rechnung (Invoice)'),
('381', 'Gutschrift'),
('384', 'Korrekturrechnung'),
('389', 'Abschlagsrechnung')
ON CONFLICT (code) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'invoices_invoice_type_code_fk'
          AND table_name = 'invoices'
          AND table_schema = 'invoice'
    ) THEN
        ALTER TABLE invoice.invoices
            ADD CONSTRAINT invoices_invoice_type_code_fk
            FOREIGN KEY (invoice_type_code) REFERENCES invoice.invoice_type_code(code)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END;
$$;

-- Checks (manual)
-- select * from invoice.vat_category;
-- select * from invoice.invoice_type_code;
-- select * from invoice.countries;
-- select zahlungsbedingung_key, count(*) from invoice.customers group by 1;
-- select p.code, i.rechnungs_code, count(*) from invoice.invoices i join invoice.providers p on p.id=i.provider_id group by 1,2;
