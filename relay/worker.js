// Bitbucket webhook → Claude Code routine /fire relay.
// Bitbucket cannot send the Authorization and anthropic-* headers /fire needs, so this Worker
// verifies the webhook signature, keeps only open non-draft PRs of the routed repos, and fires the
// repo's routine once per head SHA (`pr=<id> sha=<sha>`, a review) and once per developer reply
// (`reply pr=<id> comment=<id>`; the routine checks that the reply is in an AI finding thread).
//
// Var ROUTES: JSON {"workspace/repo": "<suffix>"}; each repo has its own routine, whose secrets are
// ROUTINE_FIRE_URL<suffix> and ROUTINE_FIRE_TOKEN<suffix> (core: empty suffix). Webhook secrets:
// BB_WEBHOOK_SECRET (core webhook), BB_WEBHOOK_SECRET_APP (app webhook).
// Optional KV binding SEEN: remembers the last fired SHA per PR (title/description edits do not re-fire)
// and the replies already fired.

const EVENTS = new Set(["pullrequest:created", "pullrequest:updated", "pullrequest:comment_created"]);
// every comment the bot posts carries one of its hidden markers; its own replies must not fire it again
const BOT_MARKER = /\(monnett-ai-[a-z]+[ )]/;

export default {
  async fetch(req, env) {
    const res = await handle(req, env);
    console.log(JSON.stringify({ event: req.headers.get("X-Event-Key"), status: res.status, result: await res.clone().text() }));
    return res;
  },
};

async function handle(req, env) {
  if (req.method !== "POST") return new Response("ok");
  const body = await req.text();
  const sig = req.headers.get("X-Hub-Signature") || "";
  // one secret per repo webhook (core: BB_WEBHOOK_SECRET); any configured one authenticates the body
  const secrets = [env.BB_WEBHOOK_SECRET, env.BB_WEBHOOK_SECRET_APP].filter(Boolean);
  if (!(await anyValid(sig, body, secrets))) {
    return new Response("bad signature", { status: 401 });
  }
  const event = req.headers.get("X-Event-Key");
  if (!EVENTS.has(event)) return new Response("ignored: event");

  const data = JSON.parse(body);
  const pr = data.pullrequest;
  if (!pr || pr.state !== "OPEN" || pr.draft) return new Response("ignored: state");
  const suffix = JSON.parse(env.ROUTES || "{}")[pr.destination?.repository?.full_name || data.repository?.full_name];
  if (suffix === undefined) return new Response("ignored: repo");
  const fireUrl = env[`ROUTINE_FIRE_URL${suffix}`];
  const fireToken = env[`ROUTINE_FIRE_TOKEN${suffix}`];
  if (!fireUrl || !fireToken) return new Response(`ignored: no routine secrets for ${suffix || "core"}`);
  const id = String(pr.id);
  if (!/^\d{1,6}$/.test(id)) return new Response("ignored: payload");

  if (event === "pullrequest:comment_created") {
    const c = data.comment || {};
    if (!c.parent?.id) return new Response("ignored: not a reply");
    if (BOT_MARKER.test(c.content?.raw || "")) return new Response("ignored: own comment");
    const cid = String(c.id);
    if (!/^\d{1,12}$/.test(cid)) return new Response("ignored: payload");
    const key = `${suffix.toLowerCase()}c:${cid}`;
    if (env.SEEN && (await env.SEEN.get(key))) return new Response("ignored: seen");
    return fire(env, fireUrl, fireToken, `reply pr=${id} comment=${cid}`, key, "1");
  }

  const sha = pr.source?.commit?.hash || "";
  if (!/^[0-9a-f]{7,40}$/.test(sha)) return new Response("ignored: payload");
  const key = `${suffix.toLowerCase()}pr:${id}`; // core keeps the original `pr:<id>` keys
  if (env.SEEN && (await env.SEEN.get(key)) === sha) return new Response("ignored: seen");
  return fire(env, fireUrl, fireToken, `pr=${id} sha=${sha}`, key, sha);
}

async function fire(env, fireUrl, fireToken, text, key, seenValue) {
  const r = await fetch(fireUrl, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${fireToken}`,
      "anthropic-beta": "experimental-cc-routine-2026-04-01",
      "anthropic-version": "2023-06-01",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ text }),
  });
  if (!r.ok) return new Response(`fire failed ${r.status}`, { status: 502 });
  if (env.SEEN) await env.SEEN.put(key, seenValue, { expirationTtl: 60 * 60 * 24 * 30 });
  return new Response(`fired ${text}`);
}

async function anyValid(header, body, secrets) {
  for (const secret of secrets) if (await validSignature(header, body, secret)) return true;
  return false;
}

// Bitbucket sends X-Hub-Signature: sha256=<hex HMAC of the raw body>.
async function validSignature(header, body, secret) {
  if (!secret || !header.startsWith("sha256=")) return false;
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body)));
  const expected = [...mac].map((b) => b.toString(16).padStart(2, "0")).join("");
  const got = header.slice(7);
  if (got.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < got.length; i++) diff |= got.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}
