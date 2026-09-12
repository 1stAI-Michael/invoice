-- Allow draft->final and final->cancelled on invoices; keep lines blocked for final/cancelled
SET search_path TO invoice, public;

CREATE OR REPLACE FUNCTION invoice.block_mutation_on_final_or_cancelled()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    parent_status text;
    target_invoice_id bigint;
BEGIN
    IF TG_TABLE_NAME = 'invoices' THEN
        parent_status := COALESCE(NEW.status, OLD.status);

        -- allow draft -> final
        IF TG_OP = 'UPDATE' AND OLD.status = 'draft' AND NEW.status = 'final' THEN
            RETURN NEW;
        END IF;
        -- allow final -> cancelled
        IF TG_OP = 'UPDATE' AND OLD.status = 'final' AND NEW.status = 'cancelled' THEN
            RETURN NEW;
        END IF;

        IF parent_status IN ('final','cancelled') THEN
            RAISE EXCEPTION 'Invoice % is % and immutable', COALESCE(NEW.id, OLD.id), parent_status;
        END IF;

        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    ELSE
        target_invoice_id := COALESCE(NEW.invoice_id, OLD.invoice_id);
        SELECT status INTO parent_status FROM invoice.invoices WHERE id = target_invoice_id;

        IF parent_status IN ('final','cancelled') THEN
            RAISE EXCEPTION 'Invoice % is % and immutable', target_invoice_id, parent_status;
        END IF;

        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
END;
$$;

-- Recreate triggers
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
