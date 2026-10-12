"""Fixed defaults and safety limits for every scrape.

The site has no location box, so searches are always for Singapore. Values
that come from the browser (search_runs.params / scheduled_searches.params)
are untrusted text: parse_params() turns them into clamped, validated values.
"""

LOCATION = "Singapore"
COUNTRY_INDEED = "Singapore"  # jobspy defaults to the USA, which gives wrong Indeed results

ALLOWED_SITES = ("indeed", "linkedin", "google", "zip_recruiter", "glassdoor", "bayt")
DEFAULT_SITES = ("indeed", "linkedin", "google")
JOB_TYPES = ("fulltime", "parttime", "internship", "contract")

MAX_RESULTS_PER_SITE = 200
DEFAULT_RESULTS = 100
MAX_HOURS_OLD = 24 * 30
MAX_DISTANCE = 100  # a blank distance means "the widest radius", per the site's note
SITE_TIMEOUT_SECONDS = 600  # per site; jobspy sets no timeout of its own
MAX_DESCRIPTION_CHARS = 20000
MAX_SCHEDULES_PER_RUN = 10
STUCK_RUN_MINUTES = 30  # a one-off run still pending/running after this is marked failed


def _int(value, default, low, high):
    try:
        n = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def _flag(value):
    return str(value).strip().lower() in ("true", "1", "yes")


def parse_params(params):
    """Turn browser-supplied params into safe keyword values for scraping."""
    params = params if isinstance(params, dict) else {}
    sites = [s.strip() for s in str(params.get("site_names", "")).split(",")]
    sites = [s for s in dict.fromkeys(sites) if s in ALLOWED_SITES] or list(DEFAULT_SITES)
    hours = str(params.get("hours_old", "")).strip()
    job_type = str(params.get("job_type", "")).strip()
    return {
        "sites": sites,
        "results_wanted": _int(params.get("results_wanted"), DEFAULT_RESULTS, 1, MAX_RESULTS_PER_SITE),
        "hours_old": _int(hours, None, 1, MAX_HOURS_OLD) if hours else None,
        "job_type": job_type if job_type in JOB_TYPES else None,
        "is_remote": _flag(params.get("is_remote")),
        "easy_apply": True if _flag(params.get("easy_apply")) else None,
        "distance": _int(params.get("distance"), MAX_DISTANCE, 1, MAX_DISTANCE),
    }
