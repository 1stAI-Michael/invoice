-- Payment terms master data defaults
SET search_path TO invoice, public;

INSERT INTO invoice.payment_terms(key, text)
VALUES ('SOFORT', 'Zahlbar sofort ohne Abzug')
ON CONFLICT (key) DO NOTHING;

INSERT INTO invoice.payment_terms(key, text)
SELECT '14TNET', '14 Tage Net'
WHERE NOT EXISTS (
    SELECT 1
    FROM invoice.payment_terms
    WHERE key IN ('14TNET', 'NET14')
       OR text ILIKE '%14%tag%'
       OR text ILIKE '%14%day%'
);
