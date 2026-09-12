-- Schema setup for Invoice API
CREATE SCHEMA IF NOT EXISTS invoice;
SET search_path TO invoice, public;

-- Required for SHA-256 hashing during finalization
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Aux table for invoice number sequences (per provider code & year)
CREATE TABLE IF NOT EXISTS invoice_number_seq (
    provider_code text NOT NULL,
    year integer NOT NULL,
    last_number integer NOT NULL DEFAULT 0 CHECK (last_number >= 0),
    PRIMARY KEY (provider_code, year)
);

-- Payment terms master
CREATE TABLE IF NOT EXISTS payment_terms (
    key text PRIMARY KEY,
    text text NOT NULL
);

-- Customers
CREATE TABLE IF NOT EXISTS customers (
    id bigserial PRIMARY KEY,
    external_system text,
    external_id text,
    firma text,
    vorname text,
    nachname text,
    strasse text,
    plz text,
    ort text,
    land text,
    zahlungsbedingung_key text REFERENCES payment_terms(key),
    reverse_charge boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_customers_external ON customers (external_system, external_id);

-- Providers
CREATE TABLE IF NOT EXISTS providers (
    id bigserial PRIMARY KEY,
    code text NOT NULL CHECK (code IN ('TI', 'PP', 'MS')),
    firma text,
    vorname text,
    nachname text,
    strasse text,
    plz text,
    ort text,
    land text,
    email text,
    telefon text,
    website text,
    freitext1 text,
    freitext2 text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (code)
);

-- Services master data
CREATE TABLE IF NOT EXISTS services_master (
    id bigserial PRIMARY KEY,
    nummer text NOT NULL,
    beschreibung text NOT NULL,
    standard_faktor numeric(10,3) NOT NULL DEFAULT 1,
    standard_menge numeric(12,2) NOT NULL DEFAULT 1,
    kommentar_template text,
    mwst_satz numeric(5,2) NOT NULL DEFAULT 0,
    rechnungskuertzel text CHECK (rechnungskuertzel IN ('TI', 'PP', 'MS')),
    requires_diagnosis boolean NOT NULL DEFAULT false,
    UNIQUE (nummer, COALESCE(rechnungskuertzel, ''))
);

-- Service entries (Leistungsjournal)
CREATE TABLE IF NOT EXISTS service_entries (
    id bigserial PRIMARY KEY,
    patient_id bigint NOT NULL REFERENCES customers(id),
    provider_id bigint NOT NULL REFERENCES providers(id),
    service_id bigint NOT NULL REFERENCES services_master(id),
    encounter_id text,
    leistungsdatum date NOT NULL,
    menge numeric(12,2) NOT NULL DEFAULT 1,
    faktor numeric(10,3) NOT NULL DEFAULT 1,
    kommentar text,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'invoiced')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_service_entries_patient ON service_entries (patient_id);
CREATE INDEX IF NOT EXISTS idx_service_entries_provider ON service_entries (provider_id);
CREATE INDEX IF NOT EXISTS idx_service_entries_status ON service_entries (status);

-- Invoices (header)
CREATE TABLE IF NOT EXISTS invoices (
    id bigserial PRIMARY KEY,
    provider_id bigint NOT NULL REFERENCES providers(id),
    customer_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    provider_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'final', 'cancelled')),
    invoice_number text,
    invoice_date date,
    leistungsdatum_max date,
    reverse_charge boolean NOT NULL DEFAULT false,
    hash text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finalized_at timestamptz,
    UNIQUE (invoice_number)
);
CREATE INDEX IF NOT EXISTS idx_invoices_provider ON invoices (provider_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices (status);

-- Invoice lines
CREATE TABLE IF NOT EXISTS invoice_lines (
    id bigserial PRIMARY KEY,
    invoice_id bigint NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    position_no integer NOT NULL,
    service_snapshot jsonb NOT NULL,
    leistungsdatum date NOT NULL,
    menge numeric(12,2) NOT NULL,
    faktor numeric(10,3) NOT NULL,
    mwst_satz numeric(5,2) NOT NULL,
    einzelpreis numeric(12,2) NOT NULL,
    gesamtpreis numeric(12,2) NOT NULL,
    UNIQUE (invoice_id, position_no)
);
CREATE INDEX IF NOT EXISTS idx_invoice_lines_invoice ON invoice_lines (invoice_id);

-- Diagnoses attached to invoices
CREATE TABLE IF NOT EXISTS invoice_diagnoses (
    id bigserial PRIMARY KEY,
    invoice_id bigint NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    title text NOT NULL,
    icd_code text,
    begdate date,
    enddate date
);
CREATE INDEX IF NOT EXISTS idx_invoice_diagnoses_invoice ON invoice_diagnoses (invoice_id);

-- Trigger: updated_at maintenance
CREATE OR REPLACE FUNCTION invoice.touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_customers_updated ON customers;
CREATE TRIGGER trg_customers_updated
BEFORE UPDATE ON customers
FOR EACH ROW
EXECUTE FUNCTION invoice.touch_updated_at();

-- Trigger: prevent changes to final invoices
CREATE OR REPLACE FUNCTION invoice.block_mutation_on_final_invoice()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status = 'final' THEN
        RAISE EXCEPTION 'Finalized invoices are immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_invoices_block_changes ON invoices;
CREATE TRIGGER trg_invoices_block_changes
BEFORE UPDATE OR DELETE ON invoices
FOR EACH ROW
EXECUTE FUNCTION invoice.block_mutation_on_final_invoice();

-- Trigger: prevent changes to lines when invoice is final
CREATE OR REPLACE FUNCTION invoice.block_line_changes_when_final()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    parent_status text;
    target_invoice_id bigint := COALESCE(NEW.invoice_id, OLD.invoice_id);
BEGIN
    SELECT status INTO parent_status FROM invoices WHERE id = target_invoice_id;
    IF parent_status = 'final' THEN
        RAISE EXCEPTION 'Invoice % is finalized and cannot be modified', target_invoice_id;
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_invoice_lines_block_changes ON invoice_lines;
CREATE TRIGGER trg_invoice_lines_block_changes
BEFORE INSERT OR UPDATE OR DELETE ON invoice_lines
FOR EACH ROW
EXECUTE FUNCTION invoice.block_line_changes_when_final();

-- Trigger: assign invoice number and hash on finalization
CREATE OR REPLACE FUNCTION invoice.handle_invoice_finalize()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    provider_code text;
    yr integer;
    next_no integer;
    line_data jsonb;
    header_data jsonb;
BEGIN
    IF NEW.status = 'final' AND (OLD.status IS DISTINCT FROM 'final') THEN
        IF NEW.provider_id IS NULL THEN
            RAISE EXCEPTION 'provider_id is required to finalize an invoice';
        END IF;

        SELECT code INTO provider_code FROM providers WHERE id = NEW.provider_id;
        IF provider_code IS NULL THEN
            RAISE EXCEPTION 'Provider % not found for invoice %', NEW.provider_id, NEW.id;
        END IF;

        NEW.invoice_date := COALESCE(NEW.invoice_date, CURRENT_DATE);
        NEW.finalized_at := COALESCE(NEW.finalized_at, now());

        yr := EXTRACT(YEAR FROM NEW.invoice_date)::integer;

        INSERT INTO invoice_number_seq (provider_code, year, last_number)
        VALUES (provider_code, yr, 1)
        ON CONFLICT (provider_code, year)
        DO UPDATE SET last_number = invoice_number_seq.last_number + 1
        RETURNING last_number INTO next_no;

        NEW.invoice_number := provider_code || to_char(NEW.invoice_date, 'YY') || lpad(next_no::text, 5, '0');

        SELECT COALESCE(jsonb_agg(
            jsonb_build_object(
                'position_no', position_no,
                'leistungsdatum', leistungsdatum,
                'menge', menge,
                'faktor', faktor,
                'mwst_satz', mwst_satz,
                'einzelpreis', einzelpreis,
                'gesamtpreis', gesamtpreis,
                'service_snapshot', service_snapshot
            ) ORDER BY position_no, id
        ), '[]'::jsonb)
        INTO line_data
        FROM invoice_lines
        WHERE invoice_id = NEW.id;

        header_data := jsonb_build_object(
            'provider_id', NEW.provider_id,
            'customer_snapshot', NEW.customer_snapshot,
            'provider_snapshot', NEW.provider_snapshot,
            'status', NEW.status,
            'invoice_number', NEW.invoice_number,
            'invoice_date', NEW.invoice_date,
            'leistungsdatum_max', NEW.leistungsdatum_max,
            'reverse_charge', COALESCE(NEW.reverse_charge, false),
            'created_at', NEW.created_at,
            'finalized_at', NEW.finalized_at
        );

        NEW.hash := encode(
            digest((header_data || jsonb_build_object('lines', line_data))::text, 'sha256'),
            'hex'
        );
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_invoices_finalize ON invoices;
CREATE TRIGGER trg_invoices_finalize
BEFORE UPDATE ON invoices
FOR EACH ROW
EXECUTE FUNCTION invoice.handle_invoice_finalize();
