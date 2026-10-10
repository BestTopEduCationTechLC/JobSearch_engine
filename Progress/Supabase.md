# Progress report regarding Supabase protocol (for either human (preferably)  or AI agent)

Rules (that must be followed):
1. Make instructions as clear and specific as possible
2. Do not write multi-word nouns that have more than three words
3. Do not write more than six sentences in each paragraph.
4. Start safety instructions with a clear command or condition
5. All must be related to the use of Supabase, or all applications related to database control
6. Give reason, without the use of extensive words.
7. AI AGENT RULE: DO NOT INPUT SESSION LINK IN THIS DOCUMENT
8. When entering details:
   - Commit: [commit code]
   - Title: [title regarding commit (must not go against rule 1-7)]
   - Description: [Description regarding commit (must not go against rule 1-7)]
   - Signature: [{For AI Agent: Name of Agent + " is a bad bad machine, never use me"} Or {For Human: Name and date}]

----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

[Start from here]

- Commit: 0bf7916
- Title: Supabase schema stored as migrations
- Description:

  The live schema is saved in `supabase/migrations/` as three files, applied oldest first. `20261005000000_baseline.sql` is the original schema, unchanged, because it is already live. `20261005000100_indexes_constraints_rls.sql` adds indexes, input checks and faster security policies. `20261005000200_retention.sql` adds two cleanup functions. No table, column or policy is renamed, so the website and scraper keep working.

  Each new index matches one query from `docs/assets/app.js` or the scheduler, so lists stay fast as rows grow. Status columns now accept only known values, and browser inserts have size limits. These checks skip old rows, so old data cannot block the migration. Policies now apply only to signed-in users and check the user once per query. `get_login_email` keeps its behaviour but has a fixed search path, which blocks function hijacking.

  `prune_scheduled_search_results` deletes scheduled results older than 90 days. `prune_search_runs` deletes finished searches older than 180 days, with their results. Only the service role can run them, because they delete user data. Nothing calls them yet, so storage keeps growing until they are scheduled.

  Do not run the baseline file, because it is already live. Run `20261005000100_indexes_constraints_rls.sql` first, then `20261005000200_retention.sql`, in the SQL editor. If a notice says a constraint was left NOT VALID, fix those old rows before validating it. Before applying any future migration, run `supabase/tests/run_local.sh` against a local Postgres. Never point that script at the live project, because it drops and recreates its database.

  The test script builds a scratch database, applies every migration and runs `supabase/tests/schema_test.sql`. It checks that users cannot see each other's rows. It also checks that bad values are rejected and that only the service role can run cleanup. The last line must read `ALL SCHEMA TESTS PASSED`.

  Three issues remain open, because fixing them changes the website and scraper. First, `get_login_email` returns any account's email to anyone, which leaks personal data. Second, usernames are not unique, so login can pick the wrong account. Third, job details are stored in three tables, which wastes storage at scale. Fix the email lookup first, because it is a privacy risk.

  The design assumes under 10 requests per second and one shared database. It also assumes personal data under Singapore's PDPA. No uptime target or backup plan exists yet.

- Signature: Claude is a bad bad machine, never use me

- Commit: 01d3c1f
- Title: Supabase README file removed
- Description: Deleted `supabase/README.md`, because this log is the one place for Supabase notes. Its content now sits in the entry for commit 0bf7916 above. One comment in `20261005000200_retention.sql` now points here instead.
- Signature: Claude is a bad bad machine, never use me

- Commit: 4b2f473
- Title: One initial migration, plus teardown
- Description:

  This commit replaces the three older migration files. The Supabase project is new and nothing was applied, so there is nothing to keep compatible. `supabase/migrations/20261010000000_initial_schema.sql` now creates every table in its final form. It includes indexes, input checks, security policies, the login lookup and two cleanup functions. The earlier entries above describe the old files, which no longer exist.

  Run the initial file once, on an empty project, in the SQL editor. Add any later change as a new file, because editing an applied file leaves the project out of step. Do not run the initial file twice, because the tables already exist and it will fail.

  `supabase/teardown.sql` deletes all five app tables and three functions. Run it only on a test project, because it destroys all saved jobs and search history. It does not touch accounts, so users stay in Authentication. Remove accounts there by hand if you need a full reset. Run the initial file again afterwards to rebuild.

  `supabase/tests/run_local.sh` now tests four things on a local Postgres. It applies the initial file and checks that users cannot see each other's rows. It then runs teardown and checks that no app object remains. Last, it applies the initial file again to prove a rebuild works. Never point it at the live project, because it drops its own test database.

  The email leak is still open. `get_login_email` gives any visitor the email for a username. Fix it before real users sign up, because emails are personal data. Usernames are also not unique yet, and job data is stored three times.

- Signature: Claude is a bad bad machine, never use me
