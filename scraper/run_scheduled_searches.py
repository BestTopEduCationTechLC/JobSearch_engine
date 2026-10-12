"""Run every due recurring search, then clean up stuck one-off runs.

Started hourly by .github/workflows/scheduled-scrapes.yml. Safe to overlap or
repeat: each schedule is claimed by moving its next_run_at forward in a single
conditional update, so only one runner can win it.
"""

import sys
from datetime import datetime, timedelta, timezone

from scraper import common, scrape_defaults as defaults
from scraper.common import log

FREQUENCY = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}


def iso(moment):
    return moment.isoformat()


def sweep_stuck_runs(store, now):
    """One-off searches that never finished (job lost, queue stuck) become 'failed'."""
    cutoff = iso(now - timedelta(minutes=defaults.STUCK_RUN_MINUTES))
    stuck = store.patch(
        "search_runs",
        {"status": "in.(pending,running)", "created_at": f"lt.{cutoff}"},
        {"status": "failed", "error": "The search timed out. Please run it again.", "completed_at": iso(now)},
    )
    if stuck:
        log(f"marked {len(stuck)} stuck search(es) as failed")
    return len(stuck)


def run_schedule(store, schedule, now, scrape_fn=None):
    """Claim and run one schedule. Returns True if this runner ran it."""
    delta = FREQUENCY.get(schedule.get("frequency"), FREQUENCY["daily"])
    claimed = store.patch(
        "scheduled_searches",
        {"id": f"eq.{schedule['id']}", "next_run_at": f"eq.{schedule['next_run_at']}", "is_active": "eq.true"},
        {"next_run_at": iso(now + delta), "last_status": "running"},
    )
    if not claimed:
        return False  # another runner got it, or it was paused meanwhile
    sid = schedule["id"]
    try:
        opts = defaults.parse_params(schedule.get("params"))
        term = (schedule.get("search_term") or "").strip() or "jobs"
        location = (schedule.get("location") or "").strip() or defaults.LOCATION
        jobs, failed = common.scrape_sites(opts["sites"], term, location, opts, scrape_fn)
        rows = common.to_rows(jobs)
        if not rows and failed:
            raise RuntimeError("Every site failed. " + "; ".join(f"{s}: {m}" for s, m in failed.items()))
        stamp = iso(now)
        store.insert(
            "scheduled_search_results",
            [dict(r, schedule_id=sid, user_id=schedule["user_id"], run_at=stamp) for r in rows],
        )
        note = ("Some sites failed: " + "; ".join(f"{s}: {m}" for s, m in failed.items())) if failed else None
        store.patch(
            "scheduled_searches",
            {"id": f"eq.{sid}"},
            {"last_status": "completed", "last_run_at": stamp, "last_error": note[:500] if note else None},
        )
        log(f"schedule {sid}: stored {len(rows)} jobs")
    except Exception as exc:  # noqa: BLE001
        log(f"schedule {sid}: failed: {exc}")
        store.patch(
            "scheduled_searches",
            {"id": f"eq.{sid}"},
            {"last_status": "failed", "last_run_at": iso(now), "last_error": str(exc)[:500]},
        )
    return True


def run(store, now=None, scrape_fn=None):
    now = now or datetime.now(timezone.utc)
    sweep_stuck_runs(store, now)
    due = store.select(
        "scheduled_searches",
        select="*",
        is_active="eq.true",
        next_run_at=f"lte.{iso(now)}",
        order="next_run_at.asc",
        limit=str(defaults.MAX_SCHEDULES_PER_RUN),
    )
    log(f"{len(due)} schedule(s) due")
    ran = sum(1 for schedule in due if run_schedule(store, schedule, now, scrape_fn))
    return ran


def main(argv):
    run(common.Supabase.from_env())
    return 0


if __name__ == "__main__":
    common.finish(main(sys.argv))
