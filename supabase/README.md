# Supabase schema

The database schema now lives here as **versioned migrations**. This folder
is the record of what the production database should look like. Change the
schema only by adding a new migration; never edit one that has been applied.

```
supabase/
  migrations/
    20261005000000_baseline.sql                  schema already live in production (unchanged copy)
    20261005000100_indexes_constraints_rls.sql   indexes, constraints, faster RLS, safer login lookup
    20261005000200_retention.sql                 cleanup functions for tables that grow forever
  tests/
    supabase_stub.sql   stand-in for Supabase's auth schema + roles (local only)
    schema_test.sql     assertions: RLS isolation, constraints, retention, index use
    run_local.sh        apply all migrations to a scratch DB and run the assertions
```

All migrations after the baseline are **non-breaking**. No table, column or
policy is renamed or removed, so `docs/assets/app.js` and the service-role
writers (the GitHub Actions scraper and the `trigger-scrape` function) need no
changes.

## Applying to the live project

The baseline is already live. Apply only the newer files, oldest first.

**SQL editor (no tooling):** Project → SQL Editor → New query. Paste
`20261005000100_indexes_constraints_rls.sql` and run it, then do the same for
`20261005000200_retention.sql`. If the first one prints
`Left <constraint> … NOT VALID`, old rows break that rule. New rows are still
checked. Fix those old rows, then run
`alter table … validate constraint …`.

**Supabase CLI (once it is set up for this repo):** record the baseline as
already applied, then push the rest:

```
supabase link --project-ref <project-ref>
supabase migration repair --status applied 20261005000000
supabase db push
```

The CLI steps are standard Supabase usage but were not run from this repo; the
SQL itself was tested as described below.

## Testing a migration before it goes live

Any local PostgreSQL 16+ works; no Supabase project is touched.

```
PGHOST=<socket dir or host> PGPORT=<port> PGUSER=postgres sh supabase/tests/run_local.sh
```

It builds a scratch database, applies the stub and every migration, then runs
`schema_test.sql`. The last line must be `ALL SCHEMA TESTS PASSED`.

## Adding a migration

1. Create `supabase/migrations/<YYYYMMDDHHMMSS>_<what_it_does>.sql`.
2. Keep it additive where possible. Add constraints `NOT VALID` first, and
   create indexes `if not exists`.
3. Add assertions for it to `tests/schema_test.sql` and run `run_local.sh`.
4. Apply it to production (above) and log it in `Progress/Supabase.md`.

## Assumptions behind the design

These were not confirmed; change the design if they turn out wrong.

- **Load:** small and read-heavy. Expect under 10 requests per second at peak
  for the next year, with writes mostly from the scraper.
- **Tenancy:** one shared database. Each user's rows are isolated by row level
  security on `user_id`.
- **Data class:** personal data (emails, search history), under Singapore's PDPA.
- **SLO / backups:** none defined yet. Supabase's daily backups are the only
  recovery point. That means up to 24 hours of data loss on the free/pro tier.

## Retention

Each scheduled run re-inserts every job it finds, full descriptions included.
`scheduled_search_results` therefore grows by roughly
(active schedules × jobs per run) every day. The cleanup functions do nothing
until something calls them. With the `pg_cron` extension enabled
(Database → Extensions), schedule them once in the SQL editor:

```sql
select cron.schedule('prune-scheduled-results', '17 3 * * *',
  $$select public.prune_scheduled_search_results(90)$$);
select cron.schedule('prune-search-runs', '23 3 * * *',
  $$select public.prune_search_runs(180)$$);
```

(Not run here. `pg_cron` is not part of the local test setup.)

## Known issues and next steps

Ordered by value. Each one changes the API surface, so each needs matching
changes in `app.js` and the scraper. That is why none is applied yet.

1. **Username → email lookup is public.** `get_login_email` lets anyone,
   signed in or not, get any account's email from its username. That leaks
   personal data and allows account enumeration. Fix: sign in by email, or
   move username sign-in into an Edge Function so the email never reaches
   the browser.
2. **Usernames are not unique.** The username sits only in auth metadata, so
   two accounts can share one; login then picks either. Fix: a `profiles`
   table with a unique index on `lower(username)`, filled by a trigger on
   `auth.users`.
3. **Job data is copied three times.** `saved_jobs`, `search_results` and
   `scheduled_search_results` each store full job rows and descriptions. Fix:
   one `jobs` table keyed by `job_id`, with the three tables reduced to links.
   This gives the biggest storage saving, but every reader and writer changes.
4. **`date_posted` is text.** Convert it to `date` once the scraper always
   writes ISO dates. That makes date filtering and sorting indexable.
5. **Partition `scheduled_search_results` by month** once it passes about 10
   million rows. Retention then becomes dropping whole partitions.
