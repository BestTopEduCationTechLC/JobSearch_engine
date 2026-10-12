"""Shared helpers: Supabase REST access, per-site scraping, row cleaning.

Uses only `requests` and `jobspy` (already project dependencies), so the
workflows install with a plain `pip install .`.
"""

import hashlib
import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout

import requests

from scraper import scrape_defaults as defaults

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def log(message):
    print(message, flush=True)


def finish(code):
    """Exit now. jobspy's worker threads have no timeout and would otherwise
    keep the process (and the paid workflow minutes) alive after we are done."""
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


class Supabase:
    """Minimal PostgREST client using the service role key (bypasses RLS)."""

    def __init__(self, url, service_key):
        self.base = url.rstrip("/") + "/rest/v1"
        self.session = requests.Session()
        self.session.headers.update({"apikey": service_key, "Authorization": f"Bearer {service_key}"})

    @classmethod
    def from_env(cls):
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set as repository secrets.")
        return cls(url, key)

    def _check(self, response, what):
        if not response.ok:
            # Never include headers (they carry the key); the body is PostgREST's own message.
            raise RuntimeError(f"Supabase {what} failed: HTTP {response.status_code} {response.text[:300]}")
        return response

    def select(self, table, **query):
        r = self.session.get(f"{self.base}/{table}", params=query, timeout=30)
        return self._check(r, f"select {table}").json()

    def insert(self, table, rows, batch=200):
        """Insert rows, skipping ones that already exist (so a rerun is safe)."""
        headers = {"Prefer": "resolution=ignore-duplicates,return=minimal", "Content-Type": "application/json"}
        for i in range(0, len(rows), batch):
            r = self.session.post(f"{self.base}/{table}", json=rows[i:i + batch], headers=headers, timeout=60)
            self._check(r, f"insert {table}")

    def patch(self, table, filters, values):
        """Update rows matching `filters` (PostgREST syntax, e.g. {'id': 'eq.1'}); returns the changed rows."""
        headers = {"Prefer": "return=representation", "Content-Type": "application/json"}
        r = self.session.patch(f"{self.base}/{table}", params=filters, json=values, headers=headers, timeout=30)
        return self._check(r, f"update {table}").json()


# ------------------------------------------------------------------ scraping

def _default_scrape(**kwargs):
    from jobspy import scrape_jobs  # imported late so tests don't need network libraries working
    return scrape_jobs(**kwargs)


def scrape_sites(sites, search_term, location, opts, scrape_fn=None, timeout=None):
    """Scrape each site on its own so one blocked site cannot sink the rest.

    Returns (list of raw job dicts, {site: error message}).
    """
    scrape_fn = scrape_fn or _default_scrape
    timeout = timeout or defaults.SITE_TIMEOUT_SECONDS

    def one(site):
        kwargs = dict(
            site_name=[site],
            search_term=search_term,
            location=location,
            results_wanted=opts["results_wanted"],
            hours_old=opts["hours_old"],
            job_type=opts["job_type"],
            is_remote=opts["is_remote"],
            easy_apply=opts["easy_apply"],
            distance=opts["distance"],
            country_indeed=defaults.COUNTRY_INDEED,
            description_format="markdown",
        )
        if site == "google":
            kwargs["google_search_term"] = f"{search_term} jobs in {location}"
        frame = scrape_fn(**kwargs)
        return [] if frame is None else frame.to_dict("records")

    jobs, failed = [], {}
    pool = ThreadPoolExecutor(max_workers=max(1, len(sites)))
    futures = {pool.submit(one, site): site for site in sites}
    try:
        for future in as_completed(futures, timeout=timeout):
            site = futures[future]
            try:
                records = future.result()
                log(f"{site}: {len(records)} jobs")
                jobs.extend(records)
            except Exception as exc:  # noqa: BLE001 - any scraper error is reported per site
                failed[site] = f"{type(exc).__name__}: {str(exc)[:200]}"
                log(f"{site}: FAILED {failed[site]}")
    except FuturesTimeout:
        for future, site in futures.items():
            if not future.done():
                failed[site] = f"timed out after {timeout}s"
                log(f"{site}: {failed[site]}")
    pool.shutdown(wait=False, cancel_futures=True)
    return jobs, failed


# ------------------------------------------------------------------ cleaning

def clean(value):
    """None for missing values (None, NaN, pandas NA), else the value."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if type(value).__name__ in ("NAType", "NaTType"):
        return None
    return value


def _text(value, limit=None):
    value = clean(value)
    if value is None:
        return None
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    text = text.replace("\x00", "")
    return text[:limit] if limit else text


def job_key(record):
    """Stable id for a job: the board's own id, else a hash of its URL."""
    own = _text(record.get("id"))
    if own:
        return own
    url = _text(record.get("job_url"))
    return "u-" + hashlib.sha1(url.encode()).hexdigest()[:16] if url else None


def to_row(record):
    """Raw jobspy record -> the columns used by saved_jobs / search_results / scheduled_search_results."""
    return {
        "job_id": job_key(record),
        "title": _text(record.get("title"), 500),
        "company": _text(record.get("company"), 300),
        "location": _text(record.get("location"), 300),
        "job_url": _text(record.get("job_url"), 2000),
        "job_type": _text(record.get("job_type"), 200),
        "site": _text(record.get("site"), 50),
        "date_posted": _text(record.get("date_posted"), 40),
        "description": _text(record.get("description"), defaults.MAX_DESCRIPTION_CHARS),
    }


def to_rows(records):
    """Clean and de-duplicate by job_id (the tables' key), keeping the first copy."""
    seen, rows = set(), []
    for record in records:
        row = to_row(record)
        if not row["job_id"] or not row["title"] or row["job_id"] in seen:
            continue
        seen.add(row["job_id"])
        rows.append(row)
    return rows


def to_shared_row(record):
    """Raw jobspy record -> one entry of docs/data/jobs.json (the format the site already reads)."""
    row = to_row(record)
    shared = {"id": row["job_id"]}
    shared.update({k: row[k] for k in ("site", "title", "company", "location", "job_url", "job_type", "date_posted")})
    for key in ("min_amount", "max_amount"):
        value = clean(record.get(key))
        shared[key] = float(value) if value is not None else None
    shared["currency"] = _text(record.get("currency"), 10)
    shared["interval"] = _text(record.get("interval"), 20)
    shared["description"] = row["description"]
    return shared
