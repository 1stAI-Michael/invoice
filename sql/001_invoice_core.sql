-- Invoice core schema objects and business logic (idempotent)
CREATE SCHEMA IF NOT EXISTS invoice;
SET search_path TO invoice, public;

-- Needed for hashing
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =========================
-- Master data
-- =========================
CREATE TABLE IF NOT EXISTS payment_terms (
    key text PRIMARY KEY,
    text text NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    id bigserial PRIMARY KEY,
    external_system text,
    external_id bigint,
    firma text,
    vorname text,
    nachname text,
    strasse text,
    plz text,
    ort text,
    land text,
    zahlungsbedingung_key text REFERENCES invoice.payment_terms(key),
    reverse_charge boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- unique external mapping where both are set
CREATE UNIQUE INDEX IF NOT EXISTS ux_customers_external
    ON invoice.customers (external_system, external_id)
    WHERE external_system IS NOT NULL AND external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS providers (
    id bigserial PRIMARY KEY,
    code text NOT NULL CHECK (code IN ('TI','PP','MS')),
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

CREATE TABLE IF NOT EXISTS services_master (
    id bigserial PRIMARY KEY,
    nummer text NOT NULL,
    beschreibung text NOT NULL,
    standard_faktor numeric(6,2) NOT NULL DEFAULT 1.00,
    standard_menge numeric(10,2) NOT NULL DEFAULT 1.00,
    kommentar_template text,
    mwst_satz numeric(5,2) NOT NULL DEFAULT 19.00,
    rechnungskuertzel text NOT NULL CHECK (rechnungskuertzel IN ('TI','PP','MS')),
    requires_diagnosis boolean NOT NULL DEFAULT false,
    UNIQUE (rechnungskuertzel, nummer)
);

-- =========================
-- Service journal
-- =========================
CREATE TABLE IF NOT EXISTS service_entries (
    id bigserial PRIMARY KEY,
    customer_id bigint NOT NULL REFERENCES invoice.customers(id),
    provider_id bigint NOT NULL REFERENCES invoice.providers(id),
    service_id bigint NOT NULL REFERENCES invoice.services_master(id),
    external_encounter_id bigint,
    leistungsdatum date NOT NULL,
    menge numeric(10,2) NOT NULL DEFAULT 1.00,
    faktor numeric(6,2) NOT NULL DEFAULT 1.00,
    kommentar text,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','invoiced')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_service_entries_customer_date ON invoice.service_entries (customer_id, leistungsdatum DESC);
CREATE INDEX IF NOT EXISTS idx_service_entries_provider_status ON invoice.service_entries (provider_id, status);

-- =========================
-- Invoice headers
-- =========================
CREATE TABLE IF NOT EXISTS invoices (
    id bigserial PRIMARY KEY,
    provider_id bigint NOT NULL REFERENCES invoice.providers(id),
    customer_id bigint NOT NULL REFERENCES invoice.customers(id),
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','final','cancelled')),
    invoice_number text UNIQUE,
    invoice_date date,
    leistungsdatum_max date,
    reverse_charge boolean NOT NULL DEFAULT false,
    customer_snapshot jsonb,
    provider_snapshot jsonb,
    payment_terms_text text,
    totals jsonb,
    hash text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finalized_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_invoices_provider_created ON invoice.invoices (provider_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_invoices_customer_created ON invoice.invoices (customer_id, created_at DESC);

-- =========================
-- Invoice lines
-- =========================
CREATE TABLE IF NOT EXISTS invoice_lines (
    id bigserial PRIMARY KEY,
    invoice_id bigint NOT NULL REFERENCES invoice.invoices(id) ON DELETE CASCADE,
    position_no integer NOT NULL,
    service_snapshot jsonb NOT NULL,
    leistungsdatum date NOT NULL,
    menge numeric(10,2) NOT NULL,
    faktor numeric(6,2) NOT NULL,
    mwst_satz numeric(5,2) NOT NULL DEFAULT 0.00,
    einzelpreis numeric(12,2) NOT NULL DEFAULT 0.00,
    gesamtpreis numeric(12,2) NOT NULL DEFAULT 0.00,
    UNIQUE (invoice_id, position_no)
);
CREATE INDEX IF NOT EXISTS idx_invoice_lines_invoice ON invoice.invoice_lines (invoice_id);

-- =========================
-- Diagnoses
-- =========================
CREATE TABLE IF NOT EXISTS invoice_diagnoses (
    id bigserial PRIMARY KEY,
    invoice_id bigint NOT NULL REFERENCES invoice.invoices(id) ON DELETE CASCADE,
    title text NOT NULL,
    icd_code text,
    begdate date,
    enddate date
);
CREATE INDEX IF NOT EXISTS idx_invoice_diagnoses_invoice ON invoice.invoice_diagnoses (invoice_id);

-- =========================
-- Number sequences per provider/year
-- =========================
CREATE TABLE IF NOT EXISTS invoice_number_sequences (
    provider_code text NOT NULL,
    year smallint NOT NULL,
    last_no integer NOT NULL DEFAULT 0,
    PRIMARY KEY (provider_code, year)
);

-- =========================
-- Triggers and functions
-- =========================
CREATE OR REPLACE FUNCTION invoice.touch_customer_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_customers_touch_updated_at ON invoice.customers;
CREATE TRIGGER trg_customers_touch_updated_at
BEFORE UPDATE ON invoice.customers
FOR EACH ROW
EXECUTE FUNCTION invoice.touch_customer_updated_at();

-- Block modifications to finalized/cancelled invoices and their lines
CREATE OR REPLACE FUNCTION invoice.block_mutation_on_final_or_cancelled()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    parent_status text;
    target_invoice_id bigint;
BEGIN
    IF TG_TABLE_NAME = 'invoices' THEN
        target_invoice_id := COALESCE(NEW.id, OLD.id);
    ELSE
        target_invoice_id := COALESCE(NEW.invoice_id, OLD.invoice_id);
    END IF;

    SELECT status INTO parent_status FROM invoice.invoices WHERE id = target_invoice_id;
    IF parent_status IN ('final','cancelled') THEN
        RAISE EXCEPTION 'Invoice % is % and immutable', target_invoice_id, parent_status;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_invoices_block_changes ON invoice.invoices;
CREATE TRIGGER trg_invoices_block_changes
BEFORE UPDATE OR DELETE ON invoice.invoices
FOR EACH ROW
EXECUTE FUNCTION invoice.block_mutation_on_final_or_cancelled();

DROP TRIGGER IF EXISTS trg_invoice_lines_block_changes ON invoice.invoice_lines;
CREATE TRIGGER trg_invoice_lines_block_changes
BEFORE INSERT OR UPDATE OR DELETE ON invoice.invoice_lines
FOR EACH ROW
EXECUTE FUNCTION invoice.block_mutation_on_final_or_cancelled();

-- Next invoice number per provider code/year
CREATE OR REPLACE FUNCTION invoice.invoice_next_number(p_provider_code text, p_date date)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    yr integer := EXTRACT(YEAR FROM p_date)::int;
    yy text := right(yr::text, 2);
    next_no integer;
BEGIN
    INSERT INTO invoice.invoice_number_sequences(provider_code, year, last_no)
    VALUES (p_provider_code, yr, 0)
    ON CONFLICT (provider_code, year) DO NOTHING;

    UPDATE invoice.invoice_number_sequences
    SET last_no = invoice.invoice_number_sequences.last_no + 1
    WHERE provider_code = p_provider_code AND year = yr
    RETURNING last_no INTO next_no;

    RETURN p_provider_code || yy || lpad(next_no::text, 5, '0');
END;
$$;

-- Finalize an invoice: assign number, snapshots, totals, hash
CREATE OR REPLACE FUNCTION invoice.invoice_finalize(p_invoice_id bigint)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    inv record;
    prov record;
    cust record;
    payment_text text;
    lines_json jsonb;
    totals_json jsonb;
    net_total numeric(18,2) := 0;
    vat_total numeric(18,2) := 0;
    gross_total numeric(18,2) := 0;
    line record;
BEGIN
    SELECT * INTO inv FROM invoice.invoices WHERE id = p_invoice_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Invoice % not found', p_invoice_id;
    END IF;
    IF inv.status <> 'draft' THEN
        RAISE EXCEPTION 'Invoice % must be draft to finalize (current=%)', p_invoice_id, inv.status;
    END IF;

    SELECT * INTO cust FROM invoice.customers WHERE id = inv.customer_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Customer % not found for invoice %', inv.customer_id, p_invoice_id;
    END IF;
    SELECT * INTO prov FROM invoice.providers WHERE id = inv.provider_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Provider % not found for invoice %', inv.provider_id, p_invoice_id;
    END IF;

    inv.invoice_date := COALESCE(inv.invoice_date, CURRENT_DATE);
    inv.reverse_charge := COALESCE(inv.reverse_charge, cust.reverse_charge);

    SELECT text INTO payment_text
    FROM invoice.payment_terms
    WHERE key = cust.zahlungsbedingung_key;

    -- snapshots
    inv.customer_snapshot := jsonb_build_object(
        'id', cust.id,
        'external_system', cust.external_system,
        'external_id', cust.external_id,
        'firma', cust.firma,
        'vorname', cust.vorname,
        'nachname', cust.nachname,
        'strasse', cust.strasse,
        'plz', cust.plz,
        'ort', cust.ort,
        'land', cust.land,
        'zahlungsbedingung_key', cust.zahlungsbedingung_key,
        'reverse_charge', cust.reverse_charge
    );

    inv.provider_snapshot := jsonb_build_object(
        'id', prov.id,
        'code', prov.code,
        'firma', prov.firma,
        'vorname', prov.vorname,
        'nachname', prov.nachname,
        'strasse', prov.strasse,
        'plz', prov.plz,
        'ort', prov.ort,
        'land', prov.land,
        'email', prov.email,
        'telefon', prov.telefon,
        'website', prov.website,
        'freitext1', prov.freitext1,
        'freitext2', prov.freitext2
    );

    inv.payment_terms_text := payment_text;

    -- leistungsdatum_max
    SELECT max(leistungsdatum) INTO inv.leistungsdatum_max
    FROM invoice.invoice_lines WHERE invoice_id = inv.id;

    -- assign invoice number if missing
    IF inv.invoice_number IS NULL THEN
        inv.invoice_number := invoice.invoice_next_number(prov.code, inv.invoice_date);
    END IF;

    -- totals and line json aggregation
    SELECT COALESCE(jsonb_agg(
        jsonb_build_object(
            'id', id,
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
    INTO lines_json
    FROM invoice.invoice_lines
    WHERE invoice_id = inv.id;

    -- compute totals
    FOR line IN
        SELECT mwst_satz, gesamtpreis FROM invoice.invoice_lines WHERE invoice_id = inv.id
    LOOP
        net_total := net_total + line.gesamtpreis;
        IF NOT inv.reverse_charge THEN
            vat_total := vat_total + (line.gesamtpreis * line.mwst_satz / 100);
        END IF;
    END LOOP;
    gross_total := net_total + vat_total;

    totals_json := jsonb_build_object(
        'net', COALESCE(net_total, 0),
        'vat', COALESCE(vat_total, 0),
        'gross', COALESCE(gross_total, 0)
    );

    -- hash header + lines
    inv.hash := encode(
        digest(
            (jsonb_build_object(
                'invoice', to_jsonb(inv) - 'hash',
                'lines', lines_json
            ))::text,
            'sha256'
        ),
        'hex'
    );

    UPDATE invoice.invoices
    SET invoice_number     = inv.invoice_number,
        invoice_date       = inv.invoice_date,
        leistungsdatum_max = inv.leistungsdatum_max,
        reverse_charge     = inv.reverse_charge,
        customer_snapshot  = inv.customer_snapshot,
        provider_snapshot  = inv.provider_snapshot,
        payment_terms_text = inv.payment_terms_text,
        totals             = totals_json,
        hash               = inv.hash,
        status             = 'final',
        finalized_at       = now()
    WHERE id = inv.id;
END;
$$;

-- =========================
-- Suggested manual tests (commented)
-- =========================
-- insert into invoice.providers(code,firma) values ('TI','Test Provider') returning id;
-- insert into invoice.customers(vorname,nachname,reverse_charge) values ('Max','Mustermann',false) returning id;
-- insert into invoice.services_master(nummer,beschreibung,rechnungskuertzel) values ('001','Leistung A','TI') returning id;
-- insert into invoice.invoices(provider_id,customer_id) values (1,1) returning id;
-- insert into invoice.invoice_lines(invoice_id,position_no,service_snapshot,leistungsdatum,menge,faktor,mwst_satz,einzelpreis,gesamtpreis)
--   values (1,1,'{}',current_date,1,1,19,100,100);
-- select invoice.invoice_finalize(1);
