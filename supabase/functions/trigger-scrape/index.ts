// trigger-scrape: starts the GitHub Actions workflow that runs one user's search.
//
// The site calls this right after it creates a row in search_runs. Safety rules:
//  - the caller must be signed in (their login token is checked here);
//  - the run must be the caller's own and still pending (row level security
//    makes other users' runs invisible, so they look like "not found");
//  - the workflow is told ONLY the run id. The search settings are re-read
//    from the database by the scraper, so nothing from the browser reaches a
//    command line;
//  - each user is limited to MAX_RUNS_PER_HOUR searches.
//
// Secrets to set (supabase secrets set ...): GITHUB_DISPATCH_TOKEN (fine-grained
// token, repository Actions: read and write), GITHUB_REPO ("owner/name").
// Optional: GITHUB_WORKFLOW (default scrape.yml), GITHUB_REF (default main),
// ALLOWED_ORIGIN (default *). SUPABASE_URL, SUPABASE_ANON_KEY and
// SUPABASE_SERVICE_ROLE_KEY are provided by Supabase itself.

import { createClient } from "jsr:@supabase/supabase-js@2";

const MAX_RUNS_PER_HOUR = 10;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const cors = {
  "Access-Control-Allow-Origin": Deno.env.get("ALLOWED_ORIGIN") ?? "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function reply(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...cors, "Content-Type": "application/json" },
  });
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  if (req.method !== "POST") return reply(405, { error: "Use POST." });

  const url = Deno.env.get("SUPABASE_URL")!;
  const authHeader = req.headers.get("Authorization") ?? "";
  if (!authHeader.startsWith("Bearer ")) return reply(401, { error: "Please sign in first." });

  // A client that acts as the caller, so row level security applies to every query.
  const asUser = createClient(url, Deno.env.get("SUPABASE_ANON_KEY")!, {
    global: { headers: { Authorization: authHeader } },
  });
  const { data: auth } = await asUser.auth.getUser();
  if (!auth?.user) return reply(401, { error: "Please sign in again." });

  let runId = "";
  try {
    runId = String((await req.json())?.run_id ?? "");
  } catch (_) { /* handled below */ }
  if (!UUID.test(runId)) return reply(400, { error: "A valid run_id is required." });

  const { data: run } = await asUser.from("search_runs").select("id, status").eq("id", runId).maybeSingle();
  if (!run) return reply(404, { error: "Search not found." });
  if (run.status !== "pending") return reply(409, { error: "This search has already started." });

  // Only the service role may change a run's status (users have no update policy).
  const asAdmin = createClient(url, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
  const fail = (message: string) =>
    asAdmin.from("search_runs").update({
      status: "failed",
      error: message,
      completed_at: new Date().toISOString(),
    }).eq("id", runId).eq("status", "pending");

  const since = new Date(Date.now() - 3600_000).toISOString();
  const { count } = await asUser.from("search_runs").select("id", { count: "exact", head: true }).gte("created_at", since);
  if ((count ?? 0) > MAX_RUNS_PER_HOUR) {
    await fail("Too many searches in the last hour. Please try again later.");
    return reply(429, { error: "Too many searches in the last hour. Please try again later." });
  }

  const token = Deno.env.get("GITHUB_DISPATCH_TOKEN");
  const repo = Deno.env.get("GITHUB_REPO");
  if (!token || !repo) {
    await fail("The search service is not set up yet.");
    return reply(503, { error: "The search service is not set up yet." });
  }

  const workflow = Deno.env.get("GITHUB_WORKFLOW") ?? "scrape.yml";
  const ref = Deno.env.get("GITHUB_REF") ?? "main";
  let status = 0;
  try {
    const res = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "trigger-scrape",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref, inputs: { run_id: runId } }),
    });
    status = res.status;
  } catch (_) { /* network error: status stays 0 */ }

  if (status !== 204) {
    console.error(`GitHub dispatch failed: HTTP ${status}`); // never log the token
    await fail(`Could not start the scraper (GitHub answered ${status || "no response"}).`);
    return reply(502, { error: "Could not start the scraper. Please try again in a few minutes." });
  }
  return reply(200, { ok: true });
});
