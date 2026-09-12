-- Read-only OpenEMR replica schema
CREATE SCHEMA IF NOT EXISTS openemr;
SET search_path TO openemr, public;

CREATE TABLE IF NOT EXISTS patients (
    pid bigint PRIMARY KEY,
    title text,
    fname text,
    lname text,
    mname text,
    dob date,
    street text,
    postal_code text,
    city text,
    state text,
    country_code text
);

CREATE TABLE IF NOT EXISTS encounters (
    id bigint PRIMARY KEY,
    pid bigint NOT NULL,
    date timestamptz,
    reason text
);
CREATE INDEX IF NOT EXISTS idx_encounters_pid_date ON encounters (pid, date DESC);

CREATE TABLE IF NOT EXISTS clinical_notes (
    id bigint PRIMARY KEY,
    pid bigint NOT NULL,
    encounter_id bigint,
    date date,
    codetext text,
    description text
);
CREATE INDEX IF NOT EXISTS idx_clinical_notes_pid_date ON clinical_notes (pid, date DESC);

CREATE TABLE IF NOT EXISTS problems (
    id bigint PRIMARY KEY,
    pid bigint NOT NULL,
    title text,
    diagnosis text,
    begdate date,
    enddate date
);
CREATE INDEX IF NOT EXISTS idx_problems_pid ON problems (pid);
CREATE INDEX IF NOT EXISTS idx_problems_pid_active ON problems (pid) WHERE enddate IS NULL;
