// Shared logic for the JobSpy Search static site: Supabase auth (username +
// password, no GitHub login), server-stored saved jobs (Postgres + Row Level
// Security), boolean search parsing, triggering the scraper workflow via a
// Supabase Edge Function, and client-side PDF export.
(function (global) {
  "use strict";

  // Fill these in from your Supabase project (Project Settings -> API).
  // The anon key is safe to expose in a public frontend — Row Level Security
  // on the saved_jobs table is what actually restricts access per user.
  const SUPABASE_URL = "https://inniqenqdcmqqmvvlrzb.supabase.co";
  const SUPABASE_ANON_KEY = "sb_publishable_aS8f54NDRKx_0qEhEeGGfA_Ey9NSFse";

  const supabase = global.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

  // ---------- Auth (username + password; a real, confirmed email is required) ----------
  // Sign-up collects a real email address and Supabase sends a confirmation
  // link to it — the account cannot log in until that link is clicked
  // (Supabase's standard "Confirm email" flow, left ON). The chosen username
  // is stored in user_metadata.username and is what the user actually types
  // to log in; get_login_email() resolves username -> current email so sign-in
  // never has to guess or reconstruct an address.
  const USERNAME_RE = /^[a-zA-Z0-9_.-]{3,32}$/;
  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  async function getUser() {
    const { data: { session } } = await supabase.auth.getSession();
    return session ? sessionToUser(session) : null;
  }

  function sessionToUser(session) {
    const meta = session.user.user_metadata || {};
    return {
      id: session.user.id,
      username: meta.username || (session.user.email || "").split("@")[0] || "User",
      email: session.user.email,
    };
  }

  function normalizeUsername(username) {
    return String(username || "").trim().toLowerCase();
  }

  function validateUsername(username) {
    if (!USERNAME_RE.test(normalizeUsername(username))) {
      return "Username must be 3-32 characters: letters, numbers, . _ or - only.";
    }
    return null;
  }

  // Used at sign-in only: just enough to give a clear inline error for an
  // empty submission instead of a confusing round-trip to Supabase. Sign-in
  // deliberately never re-checks an existing password against the rules
  // below — an account created before PASSWORD_RULES existed (or before a
  // rule was added) must keep working with the password it already has.
  function validatePassword(password) {
    if (!password) return "Enter your password.";
    return null;
  }

  // The rules enforced whenever a password is being *set* (sign-up, or
  // choosing a new one from a reset link) — shown to the user as a live
  // checklist (see login.html / reset-password.html) and enforced here so
  // the same rules apply regardless of whether the user reads the checklist.
  const PASSWORD_RULES = [
    { key: "length", label: "At least 10 characters", test: (p) => p.length >= 10 },
    { key: "uppercase", label: "At least one uppercase letter (A-Z)", test: (p) => /[A-Z]/.test(p) },
    { key: "lowercase", label: "At least one lowercase letter (a-z)", test: (p) => /[a-z]/.test(p) },
  ];

  function checkPasswordRules(password) {
    const pw = String(password || "");
    return PASSWORD_RULES.map((rule) => ({ key: rule.key, label: rule.label, met: rule.test(pw) }));
  }

  function validateNewPassword(password) {
    if (checkPasswordRules(password).some((rule) => !rule.met)) {
      return "Password does not meet all the requirements listed below.";
    }
    return null;
  }

  async function signUp(username, email, password) {
    const err = validateUsername(username) || validateNewPassword(password);
    if (err) throw new Error(err);
    const trimmedEmail = String(email || "").trim();
    if (!EMAIL_RE.test(trimmedEmail)) throw new Error("Enter a valid email address.");
    const { data, error } = await supabase.auth.signUp({
      email: trimmedEmail,
      password,
      options: {
        data: { username: normalizeUsername(username) },
        emailRedirectTo: location.origin + location.pathname.replace(/[^/]*$/, "index.html"),
      },
    });
    if (error) {
      if (/already registered|already exists/i.test(error.message)) {
        throw new Error("An account with that email already exists. Try signing in instead.");
      }
      throw new Error(error.message);
    }
    // Supabase's anti-enumeration behavior: signing up again with an email
    // that's already registered returns 200 OK with no error, but the
    // returned user has an empty identities array instead of a new one —
    // that's the only signal a duplicate happened. Without checking this,
    // re-signing-up with an existing (even unconfirmed) email silently
    // looks like success ("check your inbox") when no new email was sent.
    if (data.user && Array.isArray(data.user.identities) && data.user.identities.length === 0) {
      throw new Error("An account with that email already exists. Try signing in instead.");
    }
    // With "Confirm email" on, signUp() does not return an active session —
    // the account exists but can't sign in until the emailed link is clicked.
    return { needsConfirmation: !data.session };
  }

  async function signIn(username, password) {
    const err = validateUsername(username) || validatePassword(password);
    if (err) throw new Error(err);
    const { data: email, error: lookupError } = await supabase.rpc("get_login_email", {
      p_username: normalizeUsername(username),
    });
    if (lookupError) {
      // A real backend/config problem (e.g. the RPC isn't deployed or isn't
      // granted to anon/authenticated) — surface it plainly instead of
      // masking it behind "no account found", which would be misleading.
      throw new Error(`Could not verify that username: ${lookupError.message}`);
    }
    if (!email) throw new Error("No account found for that username.");
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) {
      if (/confirm/i.test(error.message)) {
        throw new Error("Please confirm your email first — check the inbox you signed up with.");
      }
      if (/invalid login credentials/i.test(error.message)) {
        throw new Error("Incorrect password for that username.");
      }
      throw new Error(error.message);
    }
  }

  async function logout() {
    await supabase.auth.signOut();
    location.reload();
  }

  // ---------- Forgot password ----------
  // Resolves username -> current email (same RPC as sign-in) and asks
  // Supabase to email a recovery link. That link lands on reset-password.html
  // with a recovery token in the URL, which Supabase's client picks up
  // automatically and turns into a temporary session (see reset-password.html)
  // used only to call updatePassword() below.
  async function requestPasswordReset(username) {
    const err = validateUsername(username);
    if (err) throw new Error(err);
    // Deliberately doesn't distinguish "no such username" from "email sent" —
    // revealing which usernames exist is exactly what a forgot-password form
    // shouldn't do. Callers should always show one generic message.
    const { data: email, error: lookupError } = await supabase.rpc("get_login_email", {
      p_username: username.trim().toLowerCase(),
    });
    if (lookupError || !email) return;
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: location.origin + location.pathname.replace(/[^/]*$/, "reset-password.html"),
    });
    if (error) throw new Error(error.message);
  }

  async function updatePassword(newPassword) {
    const err = validateNewPassword(newPassword);
    if (err) throw new Error(err);
    const { error } = await supabase.auth.updateUser({ password: newPassword });
    if (error) throw new Error(error.message);
  }

  // ---------- Changing the account's email later ----------
  // Uses Supabase's own secure-email-change flow: it sends a confirmation
  // link to the new address and only swaps it in once clicked, so login (via
  // get_login_email above) keeps resolving to whichever email is current.
  async function getEmailStatus() {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) return null;
    return { current: user.email || null, pending: user.new_email || null };
  }

  async function updateContactEmail(email) {
    if (!EMAIL_RE.test(email)) throw new Error("Enter a valid email address.");
    const { error } = await supabase.auth.updateUser(
      { email },
      { emailRedirectTo: location.origin + location.pathname.replace(/[^/]*$/, "personal.html") }
    );
    if (error) throw new Error(error.message);
  }

  // ---------- Saved jobs (server-stored via Supabase, per signed-in user) ----------
  async function getSavedJobs() {
    const { data, error } = await supabase
      .from("saved_jobs")
      .select("id:job_id, title, company, location, job_url, job_type, site, date_posted, description")
      .order("saved_at", { ascending: false });
    if (error) throw error;
    return data || [];
  }

  async function saveJobs(jobs) {
    const rows = jobs.map((j) => ({
      job_id: j.id,
      title: j.title || null,
      company: j.company || null,
      location: j.location || null,
      job_url: j.job_url || null,
      job_type: j.job_type || null,
      site: j.site || null,
      date_posted: j.date_posted || null,
      description: j.description || null,
    }));
    const { error } = await supabase.from("saved_jobs").upsert(rows, { onConflict: "user_id,job_id" });
    if (error) throw error;
  }

  async function removeSavedJob(jobId) {
    const { error } = await supabase.from("saved_jobs").delete().eq("job_id", jobId);
    if (error) throw error;
  }

  // ---------- Private per-user search runs ----------
  // Each "Run New Search" creates its own row here first; the GitHub Actions
  // workflow writes that run's results into search_results (via a service
  // role key, bypassing RLS) instead of the one shared docs/data/jobs.json —
  // so one user's search never overwrites or leaks into another's results.
  async function createSearchRun({ searchTerm, location: loc, params }) {
    const { data, error } = await supabase
      .from("search_runs")
      .insert({ search_term: searchTerm, location: loc, params })
      .select()
      .single();
    if (error) throw error;
    return data;
  }

  async function getSearchRun(runId) {
    const { data, error } = await supabase.from("search_runs").select("*").eq("id", runId).single();
    if (error) throw error;
    return data;
  }

  async function listSearchRuns() {
    const { data, error } = await supabase
      .from("search_runs")
      .select("*")
      .order("created_at", { ascending: false })
      .limit(20);
    if (error) throw error;
    return data || [];
  }

  async function getSearchResults(runId) {
    const { data, error } = await supabase
      .from("search_results")
      .select("id:job_id, title, company, location, job_url, job_type, site, date_posted, description")
      .eq("run_id", runId);
    if (error) throw error;
    return data || [];
  }

  // Polls a run's status until it leaves "pending"/"running", calling
  // onUpdate(run) after every check. Returns a function to stop polling early.
  function pollSearchRun(runId, onUpdate, intervalMs) {
    let stopped = false;
    (async function tick() {
      while (!stopped) {
        let run;
        try {
          run = await getSearchRun(runId);
        } catch (err) {
          onUpdate(null, err);
          return;
        }
        onUpdate(run, null);
        if (run.status === "completed" || run.status === "failed") return;
        await new Promise((r) => setTimeout(r, intervalMs || 5000));
      }
    })();
    return () => { stopped = true; };
  }

  // ---------- Recurring (daily/weekly) per-user scrapes ----------
  // Entirely separate storage from saved_jobs and from the one-off
  // search_runs/search_results above: a schedule is a standing request kept
  // running by scraper/run_scheduled_searches.py (via
  // .github/workflows/scheduled-scrapes.yml, hourly, service-role key), and
  // its results live only in scheduled_search_results.
  async function createScheduledSearch({ frequency, searchTerm, location: loc, params }) {
    const { data, error } = await supabase
      .from("scheduled_searches")
      .insert({ frequency, search_term: searchTerm, location: loc, params })
      .select()
      .single();
    if (error) throw error;
    return data;
  }

  async function listScheduledSearches() {
    const { data, error } = await supabase
      .from("scheduled_searches")
      .select("*")
      .order("created_at", { ascending: false });
    if (error) throw error;
    return data || [];
  }

  async function setScheduledSearchActive(scheduleId, isActive) {
    const { error } = await supabase
      .from("scheduled_searches")
      .update({ is_active: isActive })
      .eq("id", scheduleId);
    if (error) throw error;
  }

  async function deleteScheduledSearch(scheduleId) {
    const { error } = await supabase.from("scheduled_searches").delete().eq("id", scheduleId);
    if (error) throw error;
  }

  async function getScheduledSearchResults(scheduleId) {
    const { data, error } = await supabase
      .from("scheduled_search_results")
      .select("id:job_id, title, company, location, job_url, job_type, site, date_posted, description, run_at")
      .eq("schedule_id", scheduleId)
      .order("run_at", { ascending: false });
    if (error) throw error;
    return data || [];
  }

  // ---------- Trigger a new scrape ----------
  // Calls a Supabase Edge Function that holds one GitHub bot token
  // server-side, so signed-in users never need a token of their own.
  async function dispatchScrape(inputs, runId) {
    const { data, error } = await supabase.functions.invoke("trigger-scrape", { body: { inputs, run_id: runId } });
    if (error) throw new Error(error.message || "Failed to trigger scrape");
    if (data && data.error) throw new Error(data.error);
  }

  // ---------- Boolean search parsing (AND / OR / NOT) ----------
  // The search bar accepts AND / OR / NOT (plus quoted phrases and parentheses).
  // This is resolved HERE, client-side, before anything reaches the scraper:
  //  - buildPredicate() gives an exact boolean filter over already-scraped jobs.
  //  - toScraperTerm() rewrites the same query into JobSpy/Indeed's own syntax
  //    (implicit AND via spaces, "-word" for NOT, OR kept as-is) so the raw
  //    AND/OR/NOT keywords never get passed into the scraper directly.
  function tokenize(input) {
    const spaced = String(input || "").replace(/([()])/g, " $1 ");
    return spaced.match(/"[^"]*"|\S+/g) || [];
  }

  function stripQuotes(tok) {
    return tok.replace(/^"|"$/g, "");
  }

  function parseBooleanQuery(input) {
    const tokens = tokenize(input);
    let pos = 0;
    const peek = () => tokens[pos];
    const next = () => tokens[pos++];

    function parseExpr() {
      let node = parseTerm();
      while (peek() && String(peek()).toUpperCase() === "OR") {
        next();
        node = { type: "OR", left: node, right: parseTerm() };
      }
      return node;
    }

    function parseTerm() {
      let node = parseFactor();
      while (peek() && peek() !== ")" && String(peek()).toUpperCase() !== "OR") {
        if (String(peek()).toUpperCase() === "AND") next();
        node = { type: "AND", left: node, right: parseFactor() };
      }
      return node;
    }

    function parseFactor() {
      const tok = peek();
      if (!tok) return { type: "TRUE" };
      if (String(tok).toUpperCase() === "NOT") {
        next();
        return { type: "NOT", node: parseFactor() };
      }
      if (tok === "(") {
        next();
        const inner = parseExpr();
        if (peek() === ")") next();
        return inner;
      }
      next();
      return { type: "TERM", value: stripQuotes(tok).toLowerCase() };
    }

    const ast = tokens.length ? parseExpr() : { type: "TRUE" };

    function evaluate(node, haystack) {
      switch (node.type) {
        case "TERM": return haystack.includes(node.value);
        case "AND": return evaluate(node.left, haystack) && evaluate(node.right, haystack);
        case "OR": return evaluate(node.left, haystack) || evaluate(node.right, haystack);
        case "NOT": return !evaluate(node.node, haystack);
        default: return true;
      }
    }

    return {
      matches(text) { return evaluate(ast, String(text || "").toLowerCase()); },
    };
  }

  function buildPredicate(input, fieldsFn) {
    const query = parseBooleanQuery(input);
    return function (job) {
      if (!input || !input.trim()) return true;
      return query.matches(fieldsFn(job));
    };
  }

  function toScraperTerm(input) {
    const tokens = tokenize(input);
    const out = [];
    for (let i = 0; i < tokens.length; i++) {
      const tok = tokens[i];
      const upper = String(tok).toUpperCase();
      if (upper === "AND" || tok === "(" || tok === ")") continue; // implicit AND; drop grouping for the scraper term
      if (upper === "NOT") {
        const nextTok = tokens[i + 1];
        if (nextTok && nextTok !== "(") {
          out.push("-" + stripQuotes(nextTok));
          i++;
        }
        continue;
      }
      // Strip surrounding quotes: the literal " characters were passed straight
      // into JobSpy's search_term, and most sites don't treat that as phrase
      // syntax — it just made the term unrecognizable to the scraper.
      out.push(stripQuotes(tok));
    }
    return out.join(" ").trim();
  }

  // ---------- PDF export ----------
  // Uses jsPDF (loaded separately via <script> tag on the page). Exports the
  // given jobs as a simple PDF, optionally including the full description.
  function exportJobsToPdf(jobs, options) {
    const includeDescription = !!(options && options.includeDescription);
    const jsPDFCtor = global.jspdf && global.jspdf.jsPDF;
    if (!jsPDFCtor) {
      global.alert("PDF library failed to load. Check your connection and try again.");
      return;
    }
    const doc = new jsPDFCtor({ unit: "pt", format: "a4" });
    const marginLeft = 40;
    const maxWidth = 515;
    const pageHeight = doc.internal.pageSize.getHeight();
    let y = 48;

    doc.setFontSize(16);
    doc.setFont(undefined, "bold");
    doc.text(`JobSpy export (${jobs.length} job${jobs.length === 1 ? "" : "s"})`, marginLeft, y);
    y += 26;

    jobs.forEach((job, idx) => {
      if (y > pageHeight - 90) { doc.addPage(); y = 48; }

      doc.setFontSize(13);
      doc.setFont(undefined, "bold");
      doc.text(doc.splitTextToSize(job.title || "Untitled", maxWidth), marginLeft, y);
      y += 18;

      doc.setFontSize(10.5);
      doc.setFont(undefined, "normal");
      doc.text(`${job.company || "Unknown company"} · ${job.location || ""}`, marginLeft, y);
      y += 14;

      const tags = [job.site, job.job_type, job.date_posted].filter(Boolean).join("  |  ");
      if (tags) { doc.text(tags, marginLeft, y); y += 14; }

      if (job.job_url) {
        doc.setTextColor(37, 99, 235);
        doc.textWithLink(job.job_url, marginLeft, y, { url: job.job_url });
        doc.setTextColor(0, 0, 0);
        y += 14;
      }

      if (includeDescription && job.description) {
        y += 4;
        const lines = doc.splitTextToSize(job.description, maxWidth);
        for (const line of lines) {
          if (y > pageHeight - 60) { doc.addPage(); y = 48; }
          doc.text(line, marginLeft, y);
          y += 13;
        }
      }

      y += 14;
      if (idx < jobs.length - 1) {
        doc.setDrawColor(220);
        doc.line(marginLeft, y - 8, marginLeft + maxWidth, y - 8);
      }
    });

    doc.save(`jobspy-export-${Date.now()}.pdf`);
  }

  // ---------- Shared nav ----------
  // Sign-in/sign-up/sign-out all live on login.html now (no popup modal).
  // The nav's auth button either signs the user out inline, or sends them to
  // login.html with a redirect back to the current page.
  async function renderNav(activePage) {
    const container = document.getElementById("topnav");
    if (!container) return;
    const user = await getUser();
    const link = (href, label, key) =>
      `<a class="navlink${activePage === key ? " active" : ""}" href="${href}">${label}</a>`;
    container.innerHTML = `
      <div class="inner">
        <span class="brand">Besttop Career Intelligence</span>
        ${link("index.html", "Search", "index")}
        ${link("personal.html", "My Saved Jobs", "personal")}
        ${link("scheduled.html", "Scheduled Searches", "scheduled")}
        ${link("contact.html", "Contact", "contact")}
        <span class="whoami">
          ${user ? `<span class="whoami-name">Hi, <strong>${escapeHtml(user.username)}</strong></span>` : ""}
          <button type="button" id="navAuthBtn" class="${user ? "" : "signin"}">${user ? "Sign out" : "Sign Up/Sign In"}</button>
        </span>
      </div>`;
    const btn = document.getElementById("navAuthBtn");
    if (btn) btn.addEventListener("click", () => (user ? logout() : goToLogin()));
    return user;
  }

  function goToLogin(redirectTo) {
    const target = redirectTo || (location.pathname.split("/").pop() || "index.html") + location.search;
    location.href = "login.html?redirect=" + encodeURIComponent(target);
  }

  function escapeHtml(str) {
    return String(str == null ? "" : str).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  global.JobSpyApp = {
    supabase, getUser, signUp, signIn, logout, goToLogin,
    PASSWORD_RULES, checkPasswordRules, validateNewPassword,
    getEmailStatus, updateContactEmail, requestPasswordReset, updatePassword,
    getSavedJobs, saveJobs, removeSavedJob,
    createSearchRun, getSearchRun, listSearchRuns, getSearchResults, pollSearchRun,
    createScheduledSearch, listScheduledSearches, setScheduledSearchActive,
    deleteScheduledSearch, getScheduledSearchResults,
    dispatchScrape,
    parseBooleanQuery, buildPredicate, toScraperTerm,
    exportJobsToPdf, renderNav, escapeHtml,
  };
})(window);
