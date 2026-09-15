-- Bootstraps the Docker-provided PostgreSQL so `docker compose up -d postgres`
-- alone yields a database the project can actually migrate.
--
-- Why a second init file exists: scripts/init-native-postgres.sql is written for
-- a native install, where the database already exists and is being initialised
-- by a superuser.  It cannot bootstrap a fresh cluster by itself -- it grants on
-- `audit_network` without creating it -- and it deliberately does not set any
-- password.  Mounting it alone into /docker-entrypoint-initdb.d/ therefore left
-- the container unusable: migration 0001 fails with
--     permission denied to create extension "vector"
-- because the extensions were never created in that database.
--
-- This file is idempotent and safe to re-run against an existing container.
-- Credentials here are local-development only and match .env.example, the
-- scripts/ defaults and CI (`audit_app` / `audit_migrator`, password `admin`).

\set ON_ERROR_STOP on

-- 1. Extensions.  `vector` is not a trusted extension, so this only works as a
--    superuser -- which the initdb entrypoint is, and the application role is
--    deliberately not.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Application and migration roles.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_app') THEN
    CREATE ROLE audit_app NOINHERIT LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_migrator') THEN
    CREATE ROLE audit_migrator NOINHERIT LOGIN;
  END IF;
END
$$;

ALTER ROLE audit_app PASSWORD 'admin';
ALTER ROLE audit_migrator PASSWORD 'admin';

GRANT CONNECT ON DATABASE audit_network TO audit_app;
GRANT USAGE ON SCHEMA public TO audit_app;
GRANT CONNECT ON DATABASE audit_network TO audit_migrator;
GRANT USAGE ON SCHEMA public TO audit_migrator;

-- 3. The dedicated pytest database.  Tests never run against the main database,
--    so it has to exist before `pytest` is meaningful.
--    CREATE DATABASE cannot run inside a transaction block, and the entrypoint
--    feeds this file to psql statement by statement (no --single-transaction),
--    so this is fine.
SELECT 'CREATE DATABASE audit_network_test OWNER audit_migrator'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'audit_network_test')
\gexec

\connect audit_network_test

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS vector;

GRANT ALL ON SCHEMA public TO audit_app;
GRANT ALL ON SCHEMA public TO audit_migrator;
