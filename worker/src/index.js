const APPS_KEY = "apps";
const MAX_BODY_BYTES = 2 * 1024 * 1024;

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
};

function json(body, status = 200, extra = {}) {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json; charset=utf-8", ...extra },
  });
}

function timingSafeEqual(a, b) {
  const encoder = new TextEncoder();
  const left = encoder.encode(a);
  const right = encoder.encode(b);
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i++) diff |= left[i] ^ right[i];
  return diff === 0;
}

function isValidPayload(data) {
  return (
    data &&
    Array.isArray(data.apps) &&
    data.apps.length > 0 &&
    data.apps.every((app) => app && app.trackId && app.trackName && typeof app.userRatingCount === "number")
  );
}

async function ingest(request, env) {
  const auth = request.headers.get("Authorization") || "";
  if (!env.INGEST_SECRET || !timingSafeEqual(auth, `Bearer ${env.INGEST_SECRET}`)) {
    return json({ error: "unauthorized" }, 401);
  }
  const text = await request.text();
  if (text.length > MAX_BODY_BYTES) return json({ error: "too large" }, 413);

  let data;
  try {
    data = JSON.parse(text);
  } catch (error) {
    return json({ error: "invalid json" }, 400);
  }
  if (!isValidPayload(data)) return json({ error: "invalid payload" }, 400);

  await env.APPS_KV.put(APPS_KEY, JSON.stringify(data));
  await caches.default.delete(new Request(new URL("/apps.json", request.url).toString()));
  return json({ ok: true, apps: data.apps.length });
}

async function serve(request, env, ctx) {
  const cacheKey = new Request(new URL("/apps.json", request.url).toString());
  const cached = await caches.default.match(cacheKey);
  if (cached) return cached;

  const stored = await env.APPS_KV.get(APPS_KEY);
  if (!stored) return json({ error: "no data yet" }, 503);

  const response = json(stored, 200, { "Cache-Control": "public, max-age=300" });
  ctx.waitUntil(caches.default.put(cacheKey, response.clone()));
  return response;
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS_HEADERS });

    const { pathname } = new URL(request.url);
    if (pathname === "/ingest" && request.method === "POST") return ingest(request, env);
    if ((pathname === "/" || pathname === "/apps.json") && request.method === "GET") return serve(request, env, ctx);
    return json({ error: "not found" }, 404);
  },
};
