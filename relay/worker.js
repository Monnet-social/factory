// Bitbucket webhook → Claude Code routine /fire relay.
// Bitbucket cannot send the Authorization and anthropic-* headers /fire needs, so this Worker
// verifies the webhook signature, keeps only open non-draft PRs of one repo, and fires once per head SHA.
//
// Secrets: BB_WEBHOOK_SECRET, ROUTINE_FIRE_URL, ROUTINE_FIRE_TOKEN. Var: REPO (workspace/repo).
// Optional KV binding SEEN: remembers the last fired SHA per PR (title/description edits do not re-fire).

const EVENTS = new Set(["pullrequest:created", "pullrequest:updated"]);

export default {
  async fetch(req, env) {
    if (req.method !== "POST") return new Response("ok");
    const body = await req.text();
    if (!(await validSignature(req.headers.get("X-Hub-Signature") || "", body, env.BB_WEBHOOK_SECRET))) {
      return new Response("bad signature", { status: 401 });
    }
    if (!EVENTS.has(req.headers.get("X-Event-Key"))) return new Response("ignored: event");

    const pr = JSON.parse(body).pullrequest;
    if (!pr || pr.state !== "OPEN" || pr.draft) return new Response("ignored: state");
    if (pr.destination?.repository?.full_name !== env.REPO) return new Response("ignored: repo");
    const id = String(pr.id);
    const sha = pr.source?.commit?.hash || "";
    if (!/^\d{1,6}$/.test(id) || !/^[0-9a-f]{7,40}$/.test(sha)) return new Response("ignored: payload");

    const key = `pr:${id}`;
    if (env.SEEN && (await env.SEEN.get(key)) === sha) return new Response("ignored: seen");

    const r = await fetch(env.ROUTINE_FIRE_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.ROUTINE_FIRE_TOKEN}`,
        "anthropic-beta": "experimental-cc-routine-2026-04-01",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ text: `pr=${id} sha=${sha}` }),
    });
    if (!r.ok) return new Response(`fire failed ${r.status}`, { status: 502 });
    if (env.SEEN) await env.SEEN.put(key, sha, { expirationTtl: 60 * 60 * 24 * 30 });
    return new Response(`fired pr=${id} sha=${sha}`);
  },
};

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
