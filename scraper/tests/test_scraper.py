"""Run with:  python -m unittest discover -s scraper/tests -t .

No network and no Supabase: FakeStore imitates the few PostgREST behaviours
the scraper relies on (eq / in / lt / lte filters, conditional updates).
"""

import datetime as dt
import json
import os
import tempfile
import unittest

import pandas as pd

from scraper import common, refresh_shared_jobs, run_scheduled_searches, run_search, scrape_defaults as defaults

RUN = "11111111-1111-1111-1111-111111111111"
UTC = dt.timezone.utc


class FakeStore:
    def __init__(self, **tables):
        self.tables = {name: [dict(r) for r in rows] for name, rows in tables.items()}
        self.inserted = {}

    @staticmethod
    def _match(row, filters):
        for column, cond in filters.items():
            op, _, arg = cond.partition(".")
            value = row.get(column)
            if op == "eq" and str(value).lower() != arg.lower():  # PostgREST: true == True
                return False
            if op == "in" and str(value) not in arg.strip("()").split(","):
                return False
            if op == "lt" and not (value is not None and str(value) < arg):
                return False
            if op == "lte" and not (value is not None and str(value) <= arg):
                return False
        return True

    def select(self, table, **query):
        filters = {k: v for k, v in query.items() if k not in ("select", "order", "limit")}
        rows = [dict(r) for r in self.tables.get(table, []) if self._match(r, filters)]
        return rows[: int(query["limit"])] if "limit" in query else rows

    def patch(self, table, filters, values):
        changed = []
        for row in self.tables.get(table, []):
            if self._match(row, filters):
                row.update(values)
                changed.append(dict(row))
        return changed

    def insert(self, table, rows, batch=200):
        self.inserted.setdefault(table, []).extend(rows)


def frame(*records):
    return pd.DataFrame(list(records))


def good_scrape(**kwargs):
    site = kwargs["site_name"][0]
    return frame(
        {"id": f"{site}-1", "site": site, "title": "Data Analyst", "company": "Acme", "location": "Singapore",
         "job_url": f"https://{site}.example/1", "job_type": "fulltime", "date_posted": dt.date(2026, 10, 1),
         "description": "SQL and Python", "min_amount": float("nan"), "currency": None},
        {"id": f"{site}-2", "site": site, "title": "BI Analyst", "company": None, "location": "Singapore",
         "job_url": f"https://{site}.example/2", "job_type": None, "date_posted": None, "description": None},
    )


def one_site_down(**kwargs):
    if kwargs["site_name"][0] == "linkedin":
        raise RuntimeError("429 blocked")
    return good_scrape(**kwargs)


def all_down(**kwargs):
    raise RuntimeError("blocked")


class ParamTests(unittest.TestCase):
    def test_unknown_sites_dropped_and_numbers_clamped(self):
        p = defaults.parse_params({"site_names": "indeed,; rm -rf /,google", "results_wanted": "99999",
                                   "hours_old": "0", "job_type": "bogus", "distance": "x"})
        self.assertEqual(p["sites"], ["indeed", "google"])
        self.assertEqual(p["results_wanted"], defaults.MAX_RESULTS_PER_SITE)
        self.assertEqual(p["hours_old"], 1)
        self.assertIsNone(p["job_type"])
        self.assertEqual(p["distance"], defaults.MAX_DISTANCE)

    def test_garbage_params_use_defaults(self):
        p = defaults.parse_params("not a dict")
        self.assertEqual(p["sites"], list(defaults.DEFAULT_SITES))
        self.assertIsNone(p["hours_old"])
        self.assertFalse(p["is_remote"])


class RowTests(unittest.TestCase):
    def test_nan_dates_and_columns_become_database_safe(self):
        rows = common.to_rows(good_scrape(site_name=["indeed"]).to_dict("records"))
        self.assertEqual([r["job_id"] for r in rows], ["indeed-1", "indeed-2"])
        self.assertEqual(rows[0]["date_posted"], "2026-10-01")
        self.assertIsNone(rows[1]["company"])
        self.assertIsNone(rows[1]["date_posted"])
        json.dumps(rows, allow_nan=False)  # NaN would raise here

    def test_missing_id_falls_back_to_url_hash_and_duplicates_dropped(self):
        rec = {"id": None, "title": "T", "job_url": "https://x/1"}
        rows = common.to_rows([rec, rec, {"id": "a", "title": ""}])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["job_id"].startswith("u-"))

    def test_shared_row_matches_existing_jobs_json_keys(self):
        shared = common.to_shared_row(good_scrape(site_name=["indeed"]).to_dict("records")[0])
        self.assertEqual(list(shared), ["id", "site", "title", "company", "location", "job_url", "job_type",
                                        "date_posted", "min_amount", "max_amount", "currency", "interval", "description"])
        self.assertIsNone(shared["min_amount"])
        json.dumps(shared, allow_nan=False)


class ScrapeSiteTests(unittest.TestCase):
    OPTS = defaults.parse_params({})

    def test_one_failing_site_keeps_the_others(self):
        jobs, failed = common.scrape_sites(["indeed", "linkedin", "google"], "analyst", "Singapore", self.OPTS, one_site_down)
        self.assertEqual({j["site"] for j in jobs}, {"indeed", "google"})
        self.assertEqual(list(failed), ["linkedin"])

    def test_indeed_gets_singapore_and_google_gets_its_own_term(self):
        seen = {}
        def spy(**kw):
            seen[kw["site_name"][0]] = kw
            return good_scrape(**kw)
        common.scrape_sites(["indeed", "google"], "analyst", "Singapore", self.OPTS, spy)
        self.assertEqual(seen["indeed"]["country_indeed"], "Singapore")
        self.assertEqual(seen["google"]["google_search_term"], "analyst jobs in Singapore")

    def test_a_hung_site_times_out_instead_of_blocking(self):
        import time
        def slow(**kw):
            if kw["site_name"][0] == "indeed":
                time.sleep(3)
            return good_scrape(**kw)
        started = time.time()
        jobs, failed = common.scrape_sites(["indeed", "google"], "a", "Singapore", self.OPTS, slow, timeout=1)
        self.assertLess(time.time() - started, 2.5)
        self.assertIn("indeed", failed)
        self.assertTrue(jobs)


class RunSearchTests(unittest.TestCase):
    def store(self, status="pending", params=None):
        return FakeStore(search_runs=[{"id": RUN, "status": status, "search_term": "analyst",
                                       "location": "Singapore", "params": params or {"site_names": "indeed,linkedin"}}])

    def test_success_stores_results_and_completes(self):
        s = self.store()
        self.assertEqual(run_search.run(s, RUN, good_scrape), 0)
        self.assertEqual(s.tables["search_runs"][0]["status"], "completed")
        self.assertEqual(len(s.inserted["search_results"]), 4)
        self.assertTrue(all(r["run_id"] == RUN for r in s.inserted["search_results"]))

    def test_partial_failure_still_completes_and_names_the_site(self):
        s = self.store()
        run_search.run(s, RUN, one_site_down)
        row = s.tables["search_runs"][0]
        self.assertEqual(row["status"], "completed")
        self.assertIn("linkedin", row["error"])
        self.assertEqual(len(s.inserted["search_results"]), 2)

    def test_all_sites_down_marks_failed_with_reason(self):
        s = self.store()
        run_search.run(s, RUN, all_down)
        row = s.tables["search_runs"][0]
        self.assertEqual(row["status"], "failed")
        self.assertIn("Every site failed", row["error"])
        self.assertNotIn("search_results", s.inserted)

    def test_a_crash_while_saving_marks_failed(self):
        s = self.store()
        def boom(table, rows, batch=200):
            raise RuntimeError("db down")
        s.insert = boom
        self.assertEqual(run_search.run(s, RUN, good_scrape), 1)
        self.assertEqual(s.tables["search_runs"][0]["status"], "failed")

    def test_already_started_or_missing_run_is_a_no_op(self):
        for status in ("running", "completed", "failed"):
            s = self.store(status)
            self.assertEqual(run_search.run(s, RUN, good_scrape), 0)
            self.assertEqual(s.tables["search_runs"][0]["status"], status)
            self.assertNotIn("search_results", s.inserted)
        self.assertEqual(run_search.run(FakeStore(search_runs=[]), RUN, good_scrape), 0)

    def test_mark_failed_leaves_finished_runs_alone(self):
        s = self.store("completed")
        run_search.mark_failed(s, RUN, "x")
        self.assertEqual(s.tables["search_runs"][0]["status"], "completed")
        s = self.store("running")
        run_search.mark_failed(s, RUN, "x")
        self.assertEqual(s.tables["search_runs"][0]["status"], "failed")


class ScheduledTests(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

    def schedule(self, **over):
        base = {"id": "s1", "user_id": "u1", "frequency": "daily", "search_term": "analyst", "location": "Singapore",
                "params": {"site_names": "indeed"}, "is_active": True, "next_run_at": "2026-10-12T11:00:00+00:00"}
        base.update(over)
        return base

    def test_due_schedule_runs_stores_results_and_moves_forward(self):
        s = FakeStore(scheduled_searches=[self.schedule()], search_runs=[])
        self.assertEqual(run_scheduled_searches.run(s, self.NOW, good_scrape), 1)
        row = s.tables["scheduled_searches"][0]
        self.assertEqual(row["last_status"], "completed")
        self.assertEqual(row["next_run_at"], (self.NOW + dt.timedelta(days=1)).isoformat())
        saved = s.inserted["scheduled_search_results"]
        self.assertEqual({r["user_id"] for r in saved}, {"u1"})
        self.assertEqual({r["schedule_id"] for r in saved}, {"s1"})

    def test_weekly_moves_a_week(self):
        s = FakeStore(scheduled_searches=[self.schedule(frequency="weekly")], search_runs=[])
        run_scheduled_searches.run(s, self.NOW, good_scrape)
        self.assertEqual(s.tables["scheduled_searches"][0]["next_run_at"], (self.NOW + dt.timedelta(days=7)).isoformat())

    def test_paused_or_future_schedules_are_skipped(self):
        s = FakeStore(scheduled_searches=[self.schedule(is_active=False),
                                          self.schedule(id="s2", next_run_at="2026-10-13T00:00:00+00:00")], search_runs=[])
        self.assertEqual(run_scheduled_searches.run(s, self.NOW, good_scrape), 0)

    def test_a_second_runner_cannot_claim_the_same_schedule(self):
        stale = self.schedule()
        s = FakeStore(scheduled_searches=[stale], search_runs=[])
        self.assertTrue(run_scheduled_searches.run_schedule(s, stale, self.NOW, good_scrape))
        # a runner that read the same row earlier now holds a stale next_run_at
        self.assertFalse(run_scheduled_searches.run_schedule(s, stale, self.NOW, good_scrape))
        self.assertEqual(len(s.inserted["scheduled_search_results"]), 2)

    def test_failure_is_recorded_and_schedule_still_moves_on(self):
        s = FakeStore(scheduled_searches=[self.schedule()], search_runs=[])
        run_scheduled_searches.run(s, self.NOW, all_down)
        row = s.tables["scheduled_searches"][0]
        self.assertEqual(row["last_status"], "failed")
        self.assertIn("Every site failed", row["last_error"])
        self.assertGreater(row["next_run_at"], "2026-10-12T12:00")

    def test_stuck_one_off_runs_are_failed_but_fresh_ones_are_not(self):
        s = FakeStore(scheduled_searches=[], search_runs=[
            {"id": "old", "status": "pending", "created_at": "2026-10-12T10:00:00+00:00"},
            {"id": "new", "status": "running", "created_at": "2026-10-12T11:50:00+00:00"},
            {"id": "done", "status": "completed", "created_at": "2026-10-12T08:00:00+00:00"}])
        run_scheduled_searches.sweep_stuck_runs(s, self.NOW)
        status = {r["id"]: r["status"] for r in s.tables["search_runs"]}
        self.assertEqual(status, {"old": "failed", "new": "running", "done": "completed"})


class SharedJobsTests(unittest.TestCase):
    def test_writes_site_format_and_keeps_old_file_when_nothing_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "jobs.json")
            os.environ["SITES"] = "indeed,linkedin"
            try:
                self.assertEqual(refresh_shared_jobs.main([], good_scrape, out), 0)
                data = json.load(open(out))
                self.assertEqual(set(data), {"generated_at", "search_term", "location", "count", "jobs"})
                self.assertEqual(data["count"], len(data["jobs"]))
                before = open(out).read()
                self.assertEqual(refresh_shared_jobs.main([], all_down, out), 1)
                self.assertEqual(open(out).read(), before)  # untouched
            finally:
                os.environ.pop("SITES", None)


if __name__ == "__main__":
    unittest.main()
