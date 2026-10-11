# Progress report regarding Scraper protocol (for either human (preferably)  or AI agent)

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

