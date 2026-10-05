-- Retention for the two tables that grow without bound:
--   scheduled_search_results  every daily/weekly run re-inserts every job it
--                             finds (with full descriptions), per schedule.
--   search_runs (+ search_results via ON DELETE CASCADE)  one row set per
--                             "Run New Search" click.
-- These functions only delete; nothing calls them until you schedule them
-- (see Progress/Supabase.md). Only service_role / postgres may run them.

create or replace function public.prune_scheduled_search_results(keep_days integer default 90)
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

create or replace function public.prune_search_runs(keep_days integer default 180)
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

-- Index backing the age filter above (the PK/unique keys lead with other columns).
create index if not exists scheduled_search_results_run_at_idx
  on public.scheduled_search_results (run_at);
