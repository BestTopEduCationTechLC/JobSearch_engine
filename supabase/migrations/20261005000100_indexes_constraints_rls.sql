-- Scaling + hardening pass over the baseline. NON-BREAKING: no table, column
-- or policy is renamed or dropped, so docs/assets/app.js and the service-role
-- writers (GitHub Actions scraper, trigger-scrape function) keep working.

-- ---------------------------------------------------------------------------
-- 1. Indexes, one per real access path (see docs/assets/app.js).
--    Already covered by existing keys, so not repeated here:
--      search_results        .eq("run_id")                   -> PK (run_id, job_id)
--      scheduled_search_results .eq("schedule_id").order(run_at) -> unique (schedule_id, run_at, job_id)

-- personal.html: saved jobs, newest first
create index if not exists saved_jobs_user_saved_at_idx
  on public.saved_jobs (user_id, saved_at desc);

-- personal.html: search history, newest 20
create index if not exists search_runs_user_created_at_idx
  on public.search_runs (user_id, created_at desc);

-- scheduled.html: the user's schedules, newest first
create index if not exists scheduled_searches_user_created_at_idx
  on public.scheduled_searches (user_id, created_at desc);

-- hourly scheduler: "which active schedules are due?" Partial, so paused
-- schedules cost nothing.
create index if not exists scheduled_searches_due_idx
  on public.scheduled_searches (next_run_at)
  where is_active;

-- RLS filter + ON DELETE CASCADE from auth.users (without it, deleting a user
-- scans the whole table).
create index if not exists scheduled_search_results_user_idx
  on public.scheduled_search_results (user_id);

-- ---------------------------------------------------------------------------
-- 2. Constraints. Added NOT VALID so existing rows are not checked (no long
--    lock, no failure on old data); new and updated rows are checked at once.
--    Each is then validated only if no existing row breaks it, otherwise a
--    NOTICE names it and it stays NOT VALID until the data is cleaned.

alter table public.search_runs
  add constraint search_runs_status_check
  check (status in ('pending', 'running', 'completed', 'failed')) not valid;

alter table public.scheduled_searches
  add constraint scheduled_searches_last_status_check
  check (last_status is null or last_status in ('running', 'completed', 'failed')) not valid;

-- These tables take inserts straight from the browser, so cap sizes to stop
-- a client from storing megabytes per row.
alter table public.search_runs
  add constraint search_runs_input_size_check
  check (char_length(coalesce(search_term, '')) <= 300
     and char_length(coalesce(location, '')) <= 200
     and pg_column_size(params) <= 4096) not valid;

alter table public.scheduled_searches
  add constraint scheduled_searches_input_size_check
  check (char_length(coalesce(search_term, '')) <= 300
     and char_length(coalesce(location, '')) <= 200
     and pg_column_size(params) <= 4096) not valid;

do $$
declare
  c record;
begin
  for c in
    select conrelid::regclass as tbl, conname
    from pg_constraint
    where conname in ('search_runs_status_check',
                      'scheduled_searches_last_status_check',
                      'search_runs_input_size_check',
                      'scheduled_searches_input_size_check')
      and not convalidated
  loop
    begin
      execute format('alter table %s validate constraint %I', c.tbl, c.conname);
    exception when check_violation then
      raise notice 'Left % on % NOT VALID: existing rows violate it.', c.conname, c.tbl;
    end;
  end loop;
end $$;

-- ---------------------------------------------------------------------------
-- 3. RLS policies: same names and meaning, two changes.
--    a) `to authenticated`: anon requests skip these policies entirely.
--    b) `(select auth.uid())` instead of `auth.uid()`: Postgres evaluates it
--       once per query instead of once per row (Supabase RLS performance guide).

alter policy "Users can view their own saved jobs" on public.saved_jobs
  to authenticated using ((select auth.uid()) = user_id);
alter policy "Users can insert their own saved jobs" on public.saved_jobs
  to authenticated with check ((select auth.uid()) = user_id);
alter policy "Users can update their own saved jobs" on public.saved_jobs
  to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
alter policy "Users can delete their own saved jobs" on public.saved_jobs
  to authenticated using ((select auth.uid()) = user_id);

alter policy "Users can view their own search runs" on public.search_runs
  to authenticated using ((select auth.uid()) = user_id);
alter policy "Users can create their own search runs" on public.search_runs
  to authenticated with check ((select auth.uid()) = user_id);

alter policy "Users can view results of their own search runs" on public.search_results
  to authenticated using (exists (
    select 1 from public.search_runs r
    where r.id = search_results.run_id and r.user_id = (select auth.uid())
  ));

alter policy "Users can view their own scheduled searches" on public.scheduled_searches
  to authenticated using ((select auth.uid()) = user_id);
alter policy "Users can create their own scheduled searches" on public.scheduled_searches
  to authenticated with check ((select auth.uid()) = user_id);
alter policy "Users can update their own scheduled searches" on public.scheduled_searches
  to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
alter policy "Users can delete their own scheduled searches" on public.scheduled_searches
  to authenticated using ((select auth.uid()) = user_id);

alter policy "Users can view their own scheduled search results" on public.scheduled_search_results
  to authenticated using ((select auth.uid()) = user_id);
alter policy "Users can delete their own scheduled search results" on public.scheduled_search_results
  to authenticated using ((select auth.uid()) = user_id);

-- ---------------------------------------------------------------------------
-- 4. get_login_email: same signature and result. Pinned to an empty
--    search_path (every name is schema-qualified) so a security-definer
--    function can't be hijacked through search_path, and marked STABLE.

create or replace function public.get_login_email(p_username text)
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select email from auth.users
  where lower(raw_user_meta_data->>'username') = lower(p_username)
  limit 1;
$$;

revoke all on function public.get_login_email(text) from public;
grant execute on function public.get_login_email(text) to anon, authenticated;
