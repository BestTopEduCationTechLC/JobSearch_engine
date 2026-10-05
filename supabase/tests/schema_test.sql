-- Assertions for the migrated schema. Any failure raises and stops psql.
-- Run through run_local.sh (it applies supabase_stub.sql + all migrations first).

\set ON_ERROR_STOP on

insert into auth.users (id, email, raw_user_meta_data) values
  ('11111111-1111-1111-1111-111111111111', 'alice@example.com', '{"username":"Alice"}'),
  ('22222222-2222-2222-2222-222222222222', 'bob@example.com',   '{"username":"bob"}');

-- Service role (the scraper) seeds a run + results for Alice.
begin;
set local role service_role;
insert into public.search_runs (id, user_id, search_term, location, status)
  values ('aaaaaaaa-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'data analyst', 'Singapore', 'completed');
insert into public.search_results (run_id, job_id, title)
  values ('aaaaaaaa-0000-0000-0000-000000000001', 'in-1', 'Analyst');
commit;

-- Alice, signed in: can write and read her own rows.
begin;
set local role authenticated;
set local request.jwt.claim.sub = '11111111-1111-1111-1111-111111111111';
insert into public.saved_jobs (job_id, title) values ('in-1', 'Analyst');
insert into public.search_runs (search_term, location) values ('python', 'Singapore');
insert into public.scheduled_searches (search_term, location, frequency) values ('sql', 'Singapore', 'daily');
do $$ begin
  assert (select count(*) from public.saved_jobs) = 1, 'alice should see 1 saved job';
  assert (select count(*) from public.search_runs) = 2, 'alice should see 2 runs';
  assert (select count(*) from public.search_results) = 1, 'alice should see her run results';
  assert (select count(*) from public.scheduled_searches) = 1, 'alice should see 1 schedule';
end $$;
commit;

-- Bob, signed in: sees none of Alice's data, cannot write rows as Alice.
begin;
set local role authenticated;
set local request.jwt.claim.sub = '22222222-2222-2222-2222-222222222222';
do $$ begin
  assert (select count(*) from public.saved_jobs) = 0, 'bob must not see alice saved jobs';
  assert (select count(*) from public.search_runs) = 0, 'bob must not see alice runs';
  assert (select count(*) from public.search_results) = 0, 'bob must not see alice results';
  assert (select count(*) from public.scheduled_searches) = 0, 'bob must not see alice schedules';
  begin
    insert into public.saved_jobs (user_id, job_id) values ('11111111-1111-1111-1111-111111111111', 'x');
    raise exception 'bob inserted a row for alice';
  exception when insufficient_privilege then null;  -- RLS rejected it, as intended
  end;
end $$;
commit;

-- Anonymous: sees nothing, but can resolve a username to its login email.
begin;
set local role anon;
do $$ begin
  assert (select count(*) from public.saved_jobs) = 0, 'anon must see no saved jobs';
  assert (select count(*) from public.search_runs) = 0, 'anon must see no runs';
  assert public.get_login_email('ALICE') = 'alice@example.com', 'username lookup is case-insensitive';
  assert public.get_login_email('nobody') is null, 'unknown username returns null';
end $$;
commit;

-- Constraints reject bad values.
begin;
set local role service_role;
do $$ begin
  begin
    update public.search_runs set status = 'bogus';
    raise exception 'status check did not fire';
  exception when check_violation then null;
  end;
  begin
    insert into public.search_runs (user_id, search_term)
      values ('11111111-1111-1111-1111-111111111111', repeat('x', 301));
    raise exception 'search_term size check did not fire';
  exception when check_violation then null;
  end;
end $$;
commit;

-- Retention functions: callable by service_role only.
begin;
set local role anon;
do $$ begin
  begin
    perform public.prune_search_runs(0);
    raise exception 'anon could call prune_search_runs';
  exception when insufficient_privilege then null;
  end;
end $$;
commit;

begin;
set local role service_role;
update public.search_runs set created_at = now() - interval '400 days'
  where id = 'aaaaaaaa-0000-0000-0000-000000000001';
do $$ begin
  assert public.prune_search_runs(180) = 1, 'old completed run should be pruned';
  assert (select count(*) from public.search_results) = 0, 'its results should cascade away';
  assert (select count(*) from public.search_runs) = 1, 'the pending run must stay';
end $$;
commit;

-- The scheduler's due-query uses the partial index.
-- (seqscan off: the test tables are tiny, so the planner would scan anyway)
set enable_seqscan = off;
do $$
declare
  r record;
  uses_index boolean := false;
begin
  for r in execute 'explain (costs off) select id from public.scheduled_searches
                    where is_active and next_run_at <= now()' loop
    uses_index := uses_index or r."QUERY PLAN" like '%scheduled_searches_due_idx%';
  end loop;
  assert uses_index, 'due query should use scheduled_searches_due_idx';
end $$;
reset enable_seqscan;

\echo 'ALL SCHEMA TESTS PASSED'
