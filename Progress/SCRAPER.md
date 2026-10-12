# Progress report regarding Scraper protocol (for either human (preferably)  or AI agent)

Rules (that must be followed):
1. Make instructions as clear and specific as possible
2. Do not write multi-word nouns that have more than three words
3. Do not write more than six sentences in each paragraph.
4. Start safety instructions with a clear command or condition
5. All must be related to the use of Scraper, or all applications related to database control
6. Give reason, without the use of extensive words.
7. AI AGENT RULE: DO NOT INPUT SESSION LINK IN THIS DOCUMENT
8. When entering details:
   - Commit: [commit code]
   - Title: [title regarding commit (must not go against rule 1-7)]
   - Description: [Description regarding commit (must not go against rule 1-7)]
   - Signature: [{For AI Agent: Name of Agent + " is a bad bad machine, never use me"} Or {For Human: Name and date}]

----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

[Start from here]

- Commit: 09d0a9d
- Title: Review of scraper code
- Description:

  This entry records a code review of the `jobspy` library at the commit above. The library scores 90.7 of 100. It has 85 small code smells and 3 design flaws, and none block use. The wrapper code is missing from this repo. That covers the Supabase writer script, the workflows and the `trigger-scrape` function. Review them once they are added, because they hold the service key.

  Catch errors for each site separately. Today one blocked site fails the whole search, so you lose results from the other sites. Save the name of each failed site, so users see why results are partial.

  Pass `country_indeed="Singapore"` on every call. The default is the USA, so Indeed results would be wrong for Singapore. Salary parsing also runs for the USA only. Missing currency becomes USD, so check salary and currency before you write them to Supabase.

  Rename columns before you insert rows. The library returns `id` and `company`, but the tables use `job_id`. The site reads `id:job_id`, so a wrong name breaks saved jobs. Convert dates to text and replace NaN with null, because NaN breaks JSON.

  Do not run the scraper against live sites until you set a result cap and a timeout. Job sites block cloud servers and change often. This review did not test live scraping, so treat site behavior as unknown.

  Keep the Supabase service key in secrets, never in code. It skips row level security, so a leak exposes every user's data. Write results only through the service role, because users have no insert policy.

- Signature: Claude is a bad bad machine, never use me


- Commit: 0d60145
- Title: Scraper scripts added
- Description:

  The `scraper/` folder now holds the code that fills Supabase. `run_search.py` runs one user's search. `run_scheduled_searches.py` runs recurring searches that are due, and fails searches stuck over 30 minutes. `refresh_shared_jobs.py` rebuilds `docs/data/jobs.json`. Run any of them with `python -m scraper.<name>` from the repo root.

  Each site runs alone, so one blocked site keeps the others. Indeed uses Singapore, not the USA default. Search settings from the browser are clamped, and unknown sites are dropped. Empty values become null, and dates become text, so the database accepts every row.

  A one-off run starts only when its row is pending, so a repeat call does nothing. A schedule is claimed by moving its next run time in one update, so two runners cannot both take it. If every site fails, the run is marked failed with the reason. If some fail, the run completes and the error column names them. If the shared scrape finds nothing, the old `jobs.json` stays.

  Run `python -m unittest discover -s scraper/tests -t .` before changing these files. It runs 21 tests on a fake database and needs no network. Live scraping was not tested, because this setup cannot reach the job sites. Treat site behavior as unknown until the first real run.

- Signature: Claude is a bad bad machine, never use me
