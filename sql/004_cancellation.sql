-- Invoice cancellation workflow
SET search_path TO invoice, public;

-- New columns
ALTER TABLE invoice.invoices
    ADD COLUMN IF NOT EXISTS cancelled_at timestamptz,
    ADD COLUMN IF NOT EXISTS cancellation_of_invoice_id bigint REFERENCES invoice.invoices(id),
    ADD COLUMN IF NOT EXISTS cancellation_reason text;

-- Cancellation function
CREATE OR REPLACE FUNCTION invoice.invoice_cancel(p_invoice_id bigint, p_reason text)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_orig          invoice.invoices%ROWTYPE;
    v_new_id        bigint;
    v_entry_count   integer := 0;
BEGIN
    -- Lock original
    SELECT * INTO v_orig
    FROM invoice.invoices
    WHERE id = p_invoice_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'invoice % not found', p_invoice_id;
    END IF;
    IF v_orig.status <> 'final' THEN
        RAISE EXCEPTION 'invoice % is not final', p_invoice_id;
    END IF;
    IF v_orig.status = 'cancelled' OR v_orig.cancelled_at IS NOT NULL THEN
        RAISE EXCEPTION 'invoice % already cancelled', p_invoice_id;
    END IF;

    -- Create credit invoice header
    INSERT INTO invoice.invoices (
        provider_id, customer_id, status, invoice_date,
        cancellation_of_invoice_id, reverse_charge,
        customer_snapshot, provider_snapshot, payment_terms_text
    ) VALUES (
        v_orig.provider_id, v_orig.customer_id, 'final', current_date,
        v_orig.id, v_orig.reverse_charge,
        v_orig.customer_snapshot, v_orig.provider_snapshot, v_orig.payment_terms_text
    ) RETURNING id INTO v_new_id;

    -- Insert negative lines mirroring original
    INSERT INTO invoice.invoice_lines (
        invoice_id, position_no, service_snapshot, leistungsdatum,
        menge, faktor, mwst_satz, einzelpreis, gesamtpreis
    )
    SELECT
        v_new_id,
        l.position_no,
        l.service_snapshot,
        l.leistungsdatum,
        -l.menge,
        -l.faktor,
        l.mwst_satz,
        -l.einzelpreis,
        -l.gesamtpreis
    FROM invoice.invoice_lines l
    WHERE l.invoice_id = v_orig.id;

    -- Compute totals for credit (negative)
    UPDATE invoice.invoices i
    SET totals = sub.totals
    FROM (
        SELECT jsonb_build_object(
            'net', COALESCE(SUM(l.gesamtpreis),0),
            'vat_breakdown', (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'rate', l2.mwst_satz,
                        'net', SUM(l2.gesamtpreis),
                        'vat', CASE WHEN v_orig.reverse_charge THEN 0 ELSE SUM(l2.gesamtpreis * l2.mwst_satz / 100) END
                    )
                )
                FROM invoice.invoice_lines l2
                WHERE l2.invoice_id = v_new_id
                GROUP BY l2.mwst_satz
            ),
            'gross', CASE WHEN v_orig.reverse_charge THEN COALESCE(SUM(l.gesamtpreis),0) ELSE COALESCE(SUM(l.gesamtpreis),0) + COALESCE(SUM(l.gesamtpreis * l.mwst_satz / 100),0) END
        ) AS totals
        FROM invoice.invoice_lines l
        WHERE l.invoice_id = v_new_id
    ) AS sub
    WHERE i.id = v_new_id;

    -- Mark original cancelled
    UPDATE invoice.invoices
    SET status = 'cancelled',
        cancelled_at = now(),
        cancellation_reason = p_reason
    WHERE id = v_orig.id;

    -- Reopen service entries based on original lines
    INSERT INTO invoice.service_entries (
        customer_id, provider_id, service_id, external_encounter_id,
        leistungsdatum, menge, faktor, kommentar, status
    )
    SELECT
        v_orig.customer_id,
        v_orig.provider_id,
        (l.service_snapshot ->> 'service_id')::bigint,
        NULL,
        l.leistungsdatum,
        abs(l.menge),
        abs(l.faktor),
        COALESCE(l.service_snapshot ->> 'kommentar', ''),
        'open'
    FROM invoice.invoice_lines l
    WHERE l.invoice_id = v_orig.id;

    GET DIAGNOSTICS v_entry_count = ROW_COUNT;

    RETURN v_new_id;
END;
$$;
