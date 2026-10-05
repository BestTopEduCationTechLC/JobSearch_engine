#!/bin/sh
# Apply every migration to a fresh throwaway database and run the assertions.
# Needs a local PostgreSQL 16+ you can reach with psql (PGHOST/PGPORT/PGUSER
# as usual). Never point this at the real Supabase project.
#
#   PGHOST=/path/to/socket PGPORT=54329 PGUSER=postgres sh supabase/tests/run_local.sh
set -eu
here=$(cd "$(dirname "$0")" && pwd)
db=jobsearch_schema_test

psql -X -q -d postgres -c "drop database if exists $db" -c "create database $db"
# Roles are cluster-wide; ignore "already exists" from a previous run.
psql -X -q -d postgres -c "do \$\$ begin
  create role anon nologin; exception when duplicate_object then null; end \$\$;" \
  -c "do \$\$ begin create role authenticated nologin; exception when duplicate_object then null; end \$\$;" \
  -c "do \$\$ begin create role service_role nologin bypassrls; exception when duplicate_object then null; end \$\$;"

grep -v '^create role' "$here/supabase_stub.sql" | psql -X -q -v ON_ERROR_STOP=1 -d $db

for f in "$here"/../migrations/*.sql; do
  echo "applying $(basename "$f")"
  psql -X -q -v ON_ERROR_STOP=1 -1 -d $db -f "$f"
done

psql -X -q -v ON_ERROR_STOP=1 -d $db -f "$here/schema_test.sql"
