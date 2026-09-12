-- Fix cancellation workflow: relax trigger for final->cancelled and finalize credit notes
SET search_path TO invoice, public;

-- Adjust block function to allow final -> cancelled transition on invoices
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

    -- allow invoice status change from final -> cancelled
    IF TG_TABLE_NAME = 'invoices' AND TG_OP = 'UPDATE' AND OLD.status = 'final' AND NEW.status = 'cancelled' THEN
        RETURN NEW;
    END IF;

    IF parent_status IN ('final','cancelled') THEN
        RAISE EXCEPTION 'Invoice % is % and immutable', target_invoice_id, parent_status;
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

-- Recreate triggers (idempotent)
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

-- Replace cancellation function: create draft credit, finalize via invoice_finalize, then cancel original and reopen entries
CREATE OR REPLACE FUNCTION invoice.invoice_cancel(p_invoice_id bigint, p_reason text)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_orig          invoice.invoices%ROWTYPE;
    v_new_id        bigint;
    v_entry_count   integer := 0;
BEGIN
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

    -- Create credit invoice as draft (will be finalized)
    INSERT INTO invoice.invoices (
        provider_id, customer_id, status, invoice_date,
        cancellation_of_invoice_id, reverse_charge,
        customer_snapshot, provider_snapshot, payment_terms_text
    ) VALUES (
        v_orig.provider_id, v_orig.customer_id, 'draft', current_date,
        v_orig.id, v_orig.reverse_charge,
        v_orig.customer_snapshot, v_orig.provider_snapshot, v_orig.payment_terms_text
    ) RETURNING id INTO v_new_id;

    -- Insert negative lines
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

    -- Finalize credit invoice to assign number/hash/totals
    PERFORM invoice.invoice_finalize(v_new_id);

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
        NULLIF((l.service_snapshot ->> 'service_id'), '')::bigint,
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
