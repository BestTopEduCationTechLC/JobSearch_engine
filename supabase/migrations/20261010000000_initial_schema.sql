-- Initial schema for a NEW Supabase project. Run it once, on an empty project.
-- (Tables, constraints, indexes, row level security, login lookup, retention.)
-- To change the schema later, add a new migration file; do not edit this one.
-- To delete everything and start over, see supabase/teardown.sql.
--
-- Access model:
--   * Signed-in users only ever see their own rows (row level security).
--   * Scraper writes (search_results, scheduled_search_results, run status)
--     come from the service role key, which bypasses row level security.

-- ---------------------------------------------------------------------------
-- Saved jobs: jobs a user bookmarked from the search page.

create table public.saved_jobs (
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  job_id text not null,
  title text,
  company text,
  location text,
  job_url text,
  job_type text,
  site text,
  date_posted text,
  description text,
  saved_at timestamptz not null default now(),
  primary key (user_id, job_id)
);

-- personal.html: saved jobs, newest first
create index saved_jobs_user_saved_at_idx on public.saved_jobs (user_id, saved_at desc);

alter table public.saved_jobs enable row level security;

create policy "Users can view their own saved jobs" on public.saved_jobs
  for select to authenticated using ((select auth.uid()) = user_id);
create policy "Users can insert their own saved jobs" on public.saved_jobs
  for insert to authenticated with check ((select auth.uid()) = user_id);
create policy "Users can update their own saved jobs" on public.saved_jobs
  for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "Users can delete their own saved jobs" on public.saved_jobs
  for delete to authenticated using ((select auth.uid()) = user_id);

-- ---------------------------------------------------------------------------
-- One-off searches. Each "Run New Search" click creates one run; the scraper
-- writes that run's jobs into search_results, so users never share results.

create table public.search_runs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  search_term text,
  location text,
  params jsonb not null default '{}'::jsonb,
  status text not null default 'pending',
  error text,
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  constraint search_runs_status_check
    check (status in ('pending', 'running', 'completed', 'failed')),
  -- this table takes inserts straight from the browser: cap the sizes
  constraint search_runs_input_size_check
    check (char_length(coalesce(search_term, '')) <= 300
       and char_length(coalesce(location, '')) <= 200
       and pg_column_size(params) <= 4096)
);

-- personal.html: search history, newest 20
create index search_runs_user_created_at_idx on public.search_runs (user_id, created_at desc);

alter table public.search_runs enable row level security;

create policy "Users can view their own search runs" on public.search_runs
  for select to authenticated using ((select auth.uid()) = user_id);
create policy "Users can create their own search runs" on public.search_runs
  for insert to authenticated with check ((select auth.uid()) = user_id);
-- No update/delete policy: only the scraper (service role) changes a run's status.

create table public.search_results (
  run_id uuid not null references public.search_runs(id) on delete cascade,
  job_id text not null,
  title text,
  company text,
  location text,
  job_url text,
  job_type text,
  site text,
  date_posted text,
  description text,
  primary key (run_id, job_id)
);

alter table public.search_results enable row level security;

create policy "Users can view results of their own search runs" on public.search_results
  for select to authenticated using (exists (
    select 1 from public.search_runs r
    where r.id = search_results.run_id and r.user_id = (select auth.uid())
  ));
-- No insert/update/delete policy: results are written only by the scraper.

-- ---------------------------------------------------------------------------
-- Recurring (daily/weekly) searches. A schedule is a standing request; its
-- results live in their own table so they never mix with saved jobs.

create table public.scheduled_searches (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  frequency text not null default 'daily',
  search_term text,
  location text,
  params jsonb not null default '{}'::jsonb,
  is_active boolean not null default true,
  last_status text,
  last_error text,
  last_run_at timestamptz,
  next_run_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  constraint scheduled_searches_frequency_check check (frequency in ('daily', 'weekly')),
  constraint scheduled_searches_last_status_check
    check (last_status is null or last_status in ('running', 'completed', 'failed')),
  constraint scheduled_searches_input_size_check
    check (char_length(coalesce(search_term, '')) <= 300
       and char_length(coalesce(location, '')) <= 200
       and pg_column_size(params) <= 4096)
);

-- scheduled.html: the user's schedules, newest first
create index scheduled_searches_user_created_at_idx
  on public.scheduled_searches (user_id, created_at desc);
-- hourly scheduler: "which active schedules are due?" (paused ones cost nothing)
create index scheduled_searches_due_idx on public.scheduled_searches (next_run_at) where is_active;

alter table public.scheduled_searches enable row level security;

create policy "Users can view their own scheduled searches" on public.scheduled_searches
  for select to authenticated using ((select auth.uid()) = user_id);
create policy "Users can create their own scheduled searches" on public.scheduled_searches
  for insert to authenticated with check ((select auth.uid()) = user_id);
create policy "Users can update their own scheduled searches" on public.scheduled_searches
  for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "Users can delete their own scheduled searches" on public.scheduled_searches
  for delete to authenticated using ((select auth.uid()) = user_id);

create table public.scheduled_search_results (
  id uuid primary key default gen_random_uuid(),
  schedule_id uuid not null references public.scheduled_searches(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  run_at timestamptz not null default now(),
  job_id text not null,
  title text,
  company text,
  location text,
  job_url text,
  job_type text,
  site text,
  date_posted text,
  description text,
  unique (schedule_id, run_at, job_id)
);

-- row level security filter, and the cascade when a user is deleted
create index scheduled_search_results_user_idx on public.scheduled_search_results (user_id);
-- age filter used by the cleanup function below
create index scheduled_search_results_run_at_idx on public.scheduled_search_results (run_at);

alter table public.scheduled_search_results enable row level security;

create policy "Users can view their own scheduled search results" on public.scheduled_search_results
  for select to authenticated using ((select auth.uid()) = user_id);
create policy "Users can delete their own scheduled search results" on public.scheduled_search_results
  for delete to authenticated using ((select auth.uid()) = user_id);
-- No insert/update policy: results are written only by the scraper.

-- ---------------------------------------------------------------------------
-- Login lookup: the frontend turns a username into the account's email.
-- Empty search_path (all names qualified) stops search_path hijacking.
-- KNOWN ISSUE: anyone can call this, so it reveals an email for any username.

create function public.get_login_email(p_username text)
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

-- ---------------------------------------------------------------------------
-- Retention: scheduled results grow with every run. These only delete, and
-- nothing calls them until you schedule them. Only the service role may run them.

create function public.prune_scheduled_search_results(keep_days integer default 90)
returns bigint
language sql
security definer
set search_path = ''
as $$
  with deleted as (
    delete from public.scheduled_search_results
    where run_at < now() - make_interval(days => keep_days)
    returning 1
  )
  select count(*) from deleted;
$$;

create function public.prune_search_runs(keep_days integer default 180)
returns bigint
language sql
security definer
set search_path = ''
as $$
  with deleted as (
    delete from public.search_runs
    where created_at < now() - make_interval(days => keep_days)
      and status in ('completed', 'failed')   -- never remove a run still in flight
    returning 1
  )
  select count(*) from deleted;
$$;

revoke all on function public.prune_scheduled_search_results(integer) from public, anon, authenticated;
revoke all on function public.prune_search_runs(integer) from public, anon, authenticated;
grant execute on function public.prune_scheduled_search_results(integer) to service_role;
grant execute on function public.prune_search_runs(integer) to service_role;
