"""Run one user's one-off search (a row in search_runs) and store its results.

Started by .github/workflows/scrape.yml, which is started by the
`trigger-scrape` Edge Function. Usage:

    python -m scraper.run_search <run_id>
    python -m scraper.run_search --mark-failed <run_id>   # used if the job dies early

The run row is the only input: its params come from the database, never from
the workflow call, so nothing a browser sends can reach the command line.
"""

import sys

from scraper import common, scrape_defaults as defaults
from scraper.common import UUID_RE, log


def now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def mark_failed(store, run_id, message):
    """Fail a run that has not finished (leaves completed runs alone)."""
    store.patch(
        "search_runs",
        {"id": f"eq.{run_id}", "status": "in.(pending,running)"},
        {"status": "failed", "error": message[:500], "completed_at": now_iso()},
    )


def run(store, run_id, scrape_fn=None):
    """Returns 0 when finished (completed or failed cleanly), 1 on a crash."""
    # Claim the run: only a 'pending' run can start, so a repeated dispatch does nothing.
    claimed = store.patch("search_runs", {"id": f"eq.{run_id}", "status": "eq.pending"}, {"status": "running"})
    if not claimed:
        log(f"run {run_id} is not pending (missing or already started); nothing to do")
        return 0
    row = claimed[0]
    try:
        opts = defaults.parse_params(row.get("params"))
        term = (row.get("search_term") or "").strip() or "jobs"
        location = (row.get("location") or "").strip() or defaults.LOCATION
        log(f"run {run_id}: '{term}' in {location} on {', '.join(opts['sites'])}")

        jobs, failed = common.scrape_sites(opts["sites"], term, location, opts, scrape_fn)
        rows = common.to_rows(jobs)
        if not rows and failed:
            detail = "; ".join(f"{site}: {msg}" for site, msg in failed.items())
            mark_failed(store, run_id, f"Every site failed. {detail}")
            return 0

        store.insert("search_results", [dict(r, run_id=run_id) for r in rows])
        partial = ("Some sites failed: " + "; ".join(f"{s}: {m}" for s, m in failed.items())) if failed else None
        store.patch(
            "search_runs",
            {"id": f"eq.{run_id}"},
            {"status": "completed", "error": partial[:500] if partial else None, "completed_at": now_iso()},
        )
        log(f"run {run_id}: stored {len(rows)} jobs" + (f" ({partial})" if partial else ""))
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"run {run_id}: crashed: {exc}")
        try:
            mark_failed(store, run_id, f"Search failed: {exc}")
        except Exception as inner:  # noqa: BLE001
            log(f"could not mark run failed: {inner}")
        return 1


def main(argv):
    mark_only = "--mark-failed" in argv
    ids = [a for a in argv[1:] if not a.startswith("--")]
    if len(ids) != 1 or not UUID_RE.match(ids[0]):
        print(__doc__)
        return 2
    store = common.Supabase.from_env()
    if mark_only:
        mark_failed(store, ids[0], "The scraper stopped before it finished. Please run the search again.")
        return 0
    return run(store, ids[0])


if __name__ == "__main__":
    common.finish(main(sys.argv))
