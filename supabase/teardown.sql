-- DANGER: DELETES ALL APP TABLES AND THEIR DATA. IT CANNOT BE UNDONE.
-- Use it only to wipe a test project and start again from the initial migration.
-- Never run it on a project that holds real users' data.
--
-- It removes only what migrations/20261010000000_initial_schema.sql created.
-- It does not touch auth.users, so sign-ups survive. To also remove
-- accounts, delete them under Authentication -> Users in the dashboard.
--
-- Run it in the SQL editor, then run the initial migration again.

begin;

-- Order matters: children before parents. "if exists" makes it safe to rerun.
drop table if exists public.scheduled_search_results;
drop table if exists public.scheduled_searches;
drop table if exists public.search_results;
drop table if exists public.search_runs;
drop table if exists public.saved_jobs;

drop function if exists public.prune_scheduled_search_results(integer);
drop function if exists public.prune_search_runs(integer);
drop function if exists public.get_login_email(text);

commit;
