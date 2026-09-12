-- Add standard price to services_master for price defaults on invoice lines
ALTER TABLE invoice.services_master
    ADD COLUMN IF NOT EXISTS standard_einzelpreis numeric(12,2) NOT NULL DEFAULT 0.00;
