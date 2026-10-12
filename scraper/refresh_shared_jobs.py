"""Rebuild docs/data/jobs.json, the public job list every visitor sees.

Started daily by .github/workflows/refresh-shared-jobs.yml, which commits the
file when it changed. Needs no Supabase access. If the scrape returns nothing,
the existing file is left alone so a blocked site never blanks the page.

Settings (environment): SEARCH_TERM, SITES, RESULTS_PER_SITE, HOURS_OLD.
"""

import json
import os
import sys
from datetime import datetime, timezone

from scraper import common, scrape_defaults as defaults
from scraper.common import log

OUTPUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "data", "jobs.json")


def build(term, params, scrape_fn=None):
    opts = defaults.parse_params(params)
    jobs, failed = common.scrape_sites(opts["sites"], term, defaults.LOCATION, opts, scrape_fn)
    rows, seen = [], set()
    for record in jobs:
        shared = common.to_shared_row(record)
        if shared["id"] and shared["title"] and shared["id"] not in seen:
            seen.add(shared["id"])
            rows.append(shared)
    rows.sort(key=lambda r: r["date_posted"] or "", reverse=True)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "search_term": term,
        "location": defaults.LOCATION,
        "count": len(rows),
        "jobs": rows,
    }, failed


def main(argv, scrape_fn=None, output=OUTPUT):
    term = os.environ.get("SEARCH_TERM", "").strip() or "data analyst"
    params = {
        "site_names": os.environ.get("SITES", "indeed,linkedin,google"),
        "results_wanted": os.environ.get("RESULTS_PER_SITE", "100"),
        "hours_old": os.environ.get("HOURS_OLD", "168"),
    }
    payload, failed = build(term, params, scrape_fn)
    if not payload["jobs"]:
        log("no jobs found; leaving the existing file unchanged")
        return 1
    tmp = output + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, output)  # never leave a half-written file
    log(f"wrote {payload['count']} jobs to {output}" + (f" (failed sites: {', '.join(failed)})" if failed else ""))
    return 0


if __name__ == "__main__":
    common.finish(main(sys.argv))
