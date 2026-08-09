#!/bin/bash
# Runs once, as the superuser, on first initialisation of the data volume.
#
# Two jobs the application role cannot do for itself:
#   1. Install extensions (requires superuser).
#   2. Create the SELECT-only role the text-to-SQL agent authenticates as.
#
# The read-only role is the primary containment boundary for the SQL agent: it has
# no grants whatsoever in the `app` schema, so a prompt injection that defeats the
# query validator still cannot read users, password hashes, or tokens.

set -euo pipefail

: "${POSTGRES_READONLY_PASSWORD:?POSTGRES_READONLY_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     --set readonly_password="$POSTGRES_READONLY_PASSWORD" <<-'EOSQL'
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE EXTENSION IF NOT EXISTS pg_trgm;

    CREATE SCHEMA IF NOT EXISTS app;
    CREATE SCHEMA IF NOT EXISTS novaretail;

    -- Nobody gets implicit access to anything.
    REVOKE ALL ON SCHEMA public FROM PUBLIC;
    REVOKE ALL ON SCHEMA app FROM PUBLIC;
    REVOKE ALL ON SCHEMA novaretail FROM PUBLIC;

    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'insight_ro') THEN
            CREATE ROLE insight_ro LOGIN;
        END IF;
    END
    $$;
EOSQL

# Set separately so the password is passed as a psql variable rather than being
# interpolated into the heredoc, keeping it out of the container's SQL logs.
psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     --set readonly_password="$POSTGRES_READONLY_PASSWORD" <<-'EOSQL'
    ALTER ROLE insight_ro PASSWORD :'readonly_password';

    -- Read-only, time-boxed, and confined to the synthetic business schema.
    ALTER ROLE insight_ro SET default_transaction_read_only = on;
    ALTER ROLE insight_ro SET statement_timeout = '5s';
    ALTER ROLE insight_ro SET idle_in_transaction_session_timeout = '10s';
    ALTER ROLE insight_ro SET search_path = novaretail;

    GRANT USAGE ON SCHEMA novaretail TO insight_ro;
    GRANT SELECT ON ALL TABLES IN SCHEMA novaretail TO insight_ro;

    -- Tables created later (Phase 5 seeding) inherit the same grant.
    ALTER DEFAULT PRIVILEGES IN SCHEMA novaretail
        GRANT SELECT ON TABLES TO insight_ro;
EOSQL

echo "bootstrap complete: extensions, schemas, and insight_ro role are in place"
