-- Development-only capability grant for the Audit Network application role.
-- Run as a database administrator while connected to audit_network.
-- It never grants BYPASSRLS and makes no change to any other database.

GRANT CREATE ON DATABASE audit_network TO audit_app;

DO $$
DECLARE
  schema_name text;
  project_schemas text[] := ARRAY[
    'aiops', 'artifact', 'audit', 'belief', 'catalog', 'control', 'event',
    'graph', 'iam', 'knowledge', 'ops', 'policy', 'quant', 'risk', 'semantic'
  ];
BEGIN
  FOREACH schema_name IN ARRAY project_schemas LOOP
    EXECUTE format('GRANT USAGE, CREATE ON SCHEMA %I TO audit_app', schema_name);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO audit_app', schema_name);
    EXECUTE format('GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA %I TO audit_app', schema_name);
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE audit_migrator IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO audit_app',
      schema_name
    );
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE audit_migrator IN SCHEMA %I GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO audit_app',
      schema_name
    );
  END LOOP;
END
$$;
