# Progress report regarding workflow protocol (for either human (preferably)  or AI agent)

Rules (that must be followed):
1. Make instructions as clear and specific as possible
2. Do not write multi-word nouns that have more than three words
3. Do not write more than six sentences in each paragraph.
4. Start safety instructions with a clear command or condition
5. All must be related to the use of Supabase, or all applications related to database control
6. Give reason, without the use of extensive words.
7. Verify what branch of Workflow is happening.  
8. AI AGENT RULE: DO NOT INPUT SESSION LINK IN THIS DOCUMENT
9. When entering details:
   - Commit: [commit code]
   - Title: [title regarding commit (must not go against rule 1-7)]
   - Description: [Description regarding commit (must not go against rule 1-7)]
   - Signature: [{For AI Agent: Name of Agent + " is a bad bad machine, never use me"} Or {For Human: Name and date}]

----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

[Start from here]

- Commit: 0d60145
- Title: Workflows and trigger function added
- Description:

  Three workflows now exist, all on the main branch, because scheduled workflows run only from the default branch. `scrape.yml` runs one search and starts only from the function. `scheduled-scrapes.yml` runs hourly at minute 7. `refresh-shared-jobs.yml` runs daily at 06:23 Singapore time and commits `docs/data/jobs.json` if it changed. The first two need the secrets below. The third needs none.

  The `trigger-scrape` function lives in `supabase/functions/trigger-scrape/`. It checks the user is signed in and owns a pending run. It allows 10 searches per user per hour. It sends the workflow only the run id, and the scraper reads the settings from the database. If GitHub refuses, the run is marked failed at once.

  Do these steps after merging, in this order. First, add two repository secrets under GitHub, Settings, Secrets and variables, Actions: `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`. Take both from Supabase, Settings, API. Never put the service key in code, because it skips all row security.

  Second, create a GitHub fine-grained token for this repository only, with Actions set to read and write. Third, deploy the function and store its secrets with the Supabase CLI: `supabase functions deploy trigger-scrape --project-ref <ref>`, then `supabase secrets set GITHUB_DISPATCH_TOKEN=<token> GITHUB_REPO=BestTopEduCationTechLC/JobSearch_engine --project-ref <ref>`. Keep JWT verification on, which is the default. These commands were not run here.

  Fourth, open the GitHub Actions tab and run "Refresh shared job list" once, because the public list is two weeks old. Run "Scheduled scrapes" once to check the secrets. Then sign in on the site and start a search, and watch for a "Scrape (one-off search)" run. If the refresh cannot push, check branch protection on main, because the bot commits straight to it.

  GitHub pauses scheduled workflows after 60 days without repo activity. Watch the Actions tab for red runs.

- Signature: Claude is a bad bad machine, never use me
