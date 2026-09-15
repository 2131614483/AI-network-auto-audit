-- One-time DBA step for O9 (daily backup + restore drill), 2026-09-08 R2.
--
-- Run once with the PostgreSQL superuser:
--   psql -U postgres -h localhost -d postgres -f scripts/init-backup-role.sql
--
-- Creates audit_backup with BYPASSRLS (pg_dump must COPY RLS-FORCE tables such
-- as aiops.alerts / control.*) and CREATEDB (the restore drill builds a fresh
-- scratch database), plus SELECT across every project schema and default
-- privileges for tables the migrator will create later.  Idempotent.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_backup') THEN
    CREATE ROLE audit_backup LOGIN PASSWORD 'admin' BYPASSRLS CREATEDB;
  END IF;
END $$;

GRANT CONNECT ON DATABASE audit_network TO audit_backup;
GRANT CONNECT ON DATABASE audit_network_test TO audit_backup;

DO $$
DECLARE
  schema_name text;
BEGIN
  FOREACH schema_name IN ARRAY ARRAY[
    'public','iam','policy','catalog','control','event','artifact','semantic',
    'graph','knowledge','belief','audit','quant','aiops','risk','ops','topology'
  ]
  LOOP
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO audit_backup', schema_name);
    EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO audit_backup', schema_name);
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE audit_migrator IN SCHEMA %I GRANT SELECT ON TABLES TO audit_backup',
      schema_name
    );
  END LOOP;
END $$;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO audit_backup;
GRANT USAGE ON SCHEMA public TO audit_backup;
