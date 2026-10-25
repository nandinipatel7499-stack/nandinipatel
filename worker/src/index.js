const DEVELOPER_ID = "1742606172";
const DISPLAY_STOREFRONT = "us";
const BATCH_SIZE = 45;
const STATE_KEY = "state";

const STOREFRONTS = (
  "ae af ag ai al am ao ar at au az ba bb bd be bf bg bh bj bm bn bo br bs bt bw by bz " +
  "ca cd cg ch ci cl cm cn co cr cv cy cz de dk dm do dz ec ee eg es fi fj fm fr ga gb gd ge gh gm gr gt gw gy " +
  "hk hn hr hu id ie il in iq is it jm jo jp ke kg kh kn kr kw ky kz la lb lc lk lr lt lu lv ly ma md me mg mk " +
  "ml mm mn mo mr ms mt mu mv mw mx my mz na ne ng ni nl no np nr nz om pa pe pg ph pk pl pt pw py qa ro rs ru " +
  "rw sa sb sc se sg si sk sl sn sr st sv sz tc td th tj tm tn to tr tt tw tz ua ug us uy uz vc ve vg vn vu xk " +
  "ye za zm zw"
).split(" ");

const DISPLAY_FIELDS = [
  "trackId", "trackName", "subtitle", "description", "artistName", "sellerName", "artistId",
  "primaryGenreName", "genres", "trackContentRating", "contentAdvisoryRating", "fileSizeBytes",
  "version", "releaseDate", "currentVersionReleaseDate", "releaseNotes", "minimumOsVersion",
  "price", "formattedPrice", "currency", "artworkUrl60", "artworkUrl100", "artworkUrl512",
  "screenshotUrls", "ipadScreenshotUrls", "languageCodesISO2A", "bundleId", "trackViewUrl",
];

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
};

async function fetchStorefront(country) {
  const url = `https://itunes.apple.com/lookup?id=${DEVELOPER_ID}&entity=software&limit=200&country=${country}&_cb=${Date.now()}`;
  try {
    const response = await fetch(url, { headers: { "User-Agent": "nandinipatel-apps-worker" } });
    if (!response.ok) return null;
    const data = await response.json();
    return (data.results || []).filter((item) => item.wrapperType === "software" && item.trackId);
  } catch (error) {
    return null;
  }
}

function pickDisplay(app) {
  const item = {};
  for (const field of DISPLAY_FIELDS) {
    if (field in app) item[field] = app[field];
  }
  return item;
}

async function readState(env) {
  const state = await env.APPS_KV.get(STATE_KEY, "json");
  return state && state.stores ? state : { cursor: 0, stores: {}, display: {} };
}

async function refresh(env) {
  const state = await readState(env);
  const start = state.cursor % STOREFRONTS.length;
  const batch = STOREFRONTS.slice(start, start + BATCH_SIZE);
  const countries = batch.includes(DISPLAY_STOREFRONT) ? batch : [DISPLAY_STOREFRONT, ...batch];

  const results = await Promise.all(countries.map(async (country) => [country, await fetchStorefront(country)]));

  let loaded = 0;
  for (const [country, apps] of results) {
    if (!apps) continue;
    loaded += 1;
    const store = {};
    for (const app of apps) {
      const id = String(app.trackId);
      store[id] = [app.userRatingCount || 0, Math.round((app.averageUserRating || 0) * 10000) / 10000];
      if (country === DISPLAY_STOREFRONT || !state.display[id]) {
        state.display[id] = pickDisplay(app);
      }
    }
    state.stores[country] = store;
  }

  state.cursor = (start + BATCH_SIZE) % STOREFRONTS.length;
  state.updatedAt = new Date().toISOString();
  state.lastRun = { countries: countries.length, loaded };
  await env.APPS_KV.put(STATE_KEY, JSON.stringify(state));
  return state.lastRun;
}

function buildOutput(state) {
  const liveIds = new Set();
  for (const store of Object.values(state.stores)) {
    for (const id of Object.keys(store)) liveIds.add(id);
  }

  const apps = [];
  for (const id of liveIds) {
    const display = state.display[id];
    if (!display) continue;
    let count = 0;
    let weighted = 0;
    for (const store of Object.values(state.stores)) {
      const entry = store[id];
      if (!entry) continue;
      count += entry[0];
      weighted += entry[0] * entry[1];
    }
    apps.push({
      ...display,
      userRatingCount: count,
      averageUserRating: count ? Math.round((weighted / count) * 10000) / 10000 : 0,
    });
  }
  apps.sort((a, b) => String(b.releaseDate || "").localeCompare(String(a.releaseDate || "")));

  return {
    updatedAt: state.updatedAt,
    developerId: DEVELOPER_ID,
    storefrontsLoaded: Object.keys(state.stores).length,
    storefrontsTotal: STOREFRONTS.length,
    apps,
  };
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS_HEADERS });
    }
    const url = new URL(request.url);
    if (request.method !== "GET" || (url.pathname !== "/" && url.pathname !== "/apps.json")) {
      return new Response("Not found", { status: 404, headers: CORS_HEADERS });
    }

    const cache = caches.default;
    const cacheKey = new Request(new URL("/apps.json", url.origin).toString());
    const cached = await cache.match(cacheKey);
    if (cached) return cached;

    const state = await readState(env);
    const response = new Response(JSON.stringify(buildOutput(state)), {
      headers: {
        ...CORS_HEADERS,
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "public, max-age=300",
      },
    });
    ctx.waitUntil(cache.put(cacheKey, response.clone()));
    return response;
  },

  async scheduled(event, env, ctx) {
    ctx.waitUntil(refresh(env));
  },
};
