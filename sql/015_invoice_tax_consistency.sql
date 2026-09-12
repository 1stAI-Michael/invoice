-- Keep invoice tax category, reverse-charge flag and totals consistent
SET search_path TO invoice, public;

UPDATE invoice.invoices
SET reverse_charge = true
WHERE rechnungs_code = 'AE'
  AND reverse_charge IS DISTINCT FROM true;

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
    cust_country_iso2 text;
    prov_country_iso2 text;
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
    IF COALESCE(inv.rechnungs_code, '') = 'AE' THEN
        inv.reverse_charge := true;
    ELSIF inv.reverse_charge AND (inv.rechnungs_code IS NULL OR btrim(inv.rechnungs_code) = '') THEN
        inv.rechnungs_code := 'AE';
    END IF;

    SELECT text INTO payment_text
    FROM invoice.payment_terms
    WHERE key = cust.zahlungsbedingung_key;

    SELECT iso2 INTO cust_country_iso2
    FROM invoice.countries
    WHERE plate_code = cust.land OR iso2 = cust.land;

    SELECT iso2 INTO prov_country_iso2
    FROM invoice.countries
    WHERE plate_code = prov.land OR iso2 = prov.land;

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
        'country_iso2', cust_country_iso2,
        'vat_id', cust.vat_id,
        'tax_id', cust.tax_id,
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
        'country_iso2', prov_country_iso2,
        'vat_id', prov.vat_id,
        'tax_id', prov.tax_id,
        'email', prov.email,
        'telefon', prov.telefon,
        'website', prov.website,
        'freitext1', prov.freitext1,
        'freitext2', prov.freitext2,
        'iban', prov.iban,
        'bic', prov.bic
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
        IF NOT inv.reverse_charge AND COALESCE(inv.rechnungs_code, 'S') NOT IN ('E', 'AE', 'K') THEN
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
