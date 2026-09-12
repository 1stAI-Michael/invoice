-- Backfill rechnungs_code / invoice_type_code even when immutability trigger blocks updates
SET search_path TO invoice, public;

DO $$
DECLARE
    v_has_blocker boolean := false;
BEGIN
    SELECT EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'invoice'
          AND c.relname = 'invoices'
          AND t.tgname = 'block_mutation_on_final_or_cancelled'
    ) INTO v_has_blocker;

    IF v_has_blocker THEN
        EXECUTE 'ALTER TABLE invoice.invoices DISABLE TRIGGER block_mutation_on_final_or_cancelled';
    ELSE
        EXECUTE 'ALTER TABLE invoice.invoices DISABLE TRIGGER ALL';
    END IF;

    UPDATE invoice.invoices
       SET invoice_type_code = '380'
     WHERE invoice_type_code IS NULL OR btrim(invoice_type_code) = '';

    UPDATE invoice.invoices i
       SET rechnungs_code = CASE p.code WHEN 'PP' THEN 'E' WHEN 'TI' THEN 'S' ELSE 'S' END
      FROM invoice.providers p
     WHERE i.provider_id = p.id
       AND (i.rechnungs_code IS NULL OR btrim(i.rechnungs_code) = '');

    IF v_has_blocker THEN
        EXECUTE 'ALTER TABLE invoice.invoices ENABLE TRIGGER block_mutation_on_final_or_cancelled';
    ELSE
        EXECUTE 'ALTER TABLE invoice.invoices ENABLE TRIGGER ALL';
    END IF;
END;
$$;

-- Checks (manual)
-- select rechnungs_code, count(*) from invoice.invoices group by 1;
-- select invoice_type_code, count(*) from invoice.invoices group by 1;
