/* Bring Fast service worker — the whole app works offline.
 *
 * Online, every page comes from the network so an update shows up at once.
 * Offline, the last copy of a page is served and the client retries on a
 * ten minute cadence (BF_OFFLINE_REFRESH_MS) until the network is back.
 */
const VERSION = "bf-pwa-v3";
const SHELL = VERSION + "-shell";
const PAGES = VERSION + "-pages";
const ASSETS = VERSION + "-assets";
const OFFLINE_URL = "/offline";
const OFFLINE_REFRESH_MS = 600000;
const NET_TIMEOUT_MS = 6000;
const REFRESH_FLOOR_MS = 30000;
const STAMP = "x-bf-cached-at";

const PRECACHE = [
  OFFLINE_URL,
  "/static/pwa/icon-192.png",
  "/static/pwa/icon-512.png",
  "/static/pwa/icon-180.png",
  "/static/pwa/icon-512-maskable.png",
  "/manifest.webmanifest",
];

/* Never cached: sessions, tokens and update state must always be live. */
const LIVE_PATHS = [
  "/update/",
  "/health",
  "/mcp",
  "/oauth",
  "/token",
  "/authorize",
  "/.well-known/",
  "/login",
  "/forgot",
  "/reset",
  "/rotate-token",
];

const FONT_HOSTS = ["fonts.googleapis.com", "fonts.gstatic.com"];

let lastRefresh = 0;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  const keep = [SHELL, PAGES, ASSETS];
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => keep.indexOf(k) === -1).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isLive(pathname) {
  return LIVE_PATHS.some((p) => (p.endsWith("/") ? pathname.startsWith(p) : pathname === p || pathname.startsWith(p + "/")));
}

/* Consumes `res`: callers that still need the body must pass a clone. */
function stamped(res) {
  const headers = new Headers(res.headers);
  headers.set(STAMP, new Date().toUTCString());
  return res.blob().then((body) => new Response(body, { status: res.status, statusText: res.statusText, headers }));
}

function cacheable(res) {
  return res && res.ok && res.type === "basic" && res.status === 200;
}

function timeout(ms) {
  return new Promise((_, reject) => setTimeout(() => reject(new Error("slow network")), ms));
}

function fromNetwork(req, ms) {
  const hit = fetch(req);
  return ms ? Promise.race([hit, timeout(ms)]) : hit;
}

async function savePage(req, copy) {
  const cache = await caches.open(PAGES);
  await cache.put(req, await stamped(copy));
}

async function cachedPage(req) {
  const cache = await caches.open(PAGES);
  return (await cache.match(req)) || (await cache.match(req, { ignoreSearch: true }));
}

/* A page the device already saw wins over an error screen, every time. */
async function page(event) {
  const req = event.request;
  const saved = await cachedPage(req);
  try {
    const res = await fromNetwork(req, saved ? NET_TIMEOUT_MS : 0);
    /* Clone now: after this returns the body belongs to the page. */
    if (cacheable(res)) event.waitUntil(savePage(req, res.clone()));
    return res;
  } catch (e) {
    if (saved) return saved;
    const fallback = await caches.match(OFFLINE_URL);
    if (fallback) return fallback;
    return new Response("Offline", { status: 503, headers: { "Content-Type": "text/plain" } });
  }
}

async function asset(event) {
  const req = event.request;
  const cache = await caches.open(ASSETS);
  const hit = await cache.match(req);
  if (hit) {
    /* Refresh in the background so an updated build lands on the next load. */
    event.waitUntil(
      fetch(req)
        .then((res) => (res && (res.ok || res.type === "opaque") ? cache.put(req, res.clone()) : null))
        .catch(() => {})
    );
    return hit;
  }
  try {
    const res = await fetch(req);
    if (res && (res.ok || res.type === "opaque")) event.waitUntil(cache.put(req, res.clone()));
    return res;
  } catch (e) {
    return new Response("", { status: 504 });
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  const sameOrigin = url.origin === self.location.origin;

  if (!sameOrigin) {
    if (FONT_HOSTS.indexOf(url.hostname) !== -1) event.respondWith(asset(event));
    return;
  }
  if (url.pathname === "/logout") {
    event.respondWith(caches.delete(PAGES).then(() => fetch(req)));
    return;
  }
  if (isLive(url.pathname)) return;
  if (req.mode === "navigate") {
    event.respondWith(page(event));
    return;
  }
  if (url.pathname.startsWith("/static/") || url.pathname === "/favicon.ico" || url.pathname === "/apple-touch-icon.png") {
    event.respondWith(asset(event));
  }
});

/* Re-fetch every saved page so the next offline read is as fresh as the
 * cadence allows: at once while online, every ten minutes while not. */
async function refreshPages(force) {
  if (navigator.onLine === false) return { refreshed: 0, offline: true };
  const now = Date.now();
  if (!force && now - lastRefresh < REFRESH_FLOOR_MS) return { refreshed: 0, throttled: true };
  lastRefresh = now;
  const cache = await caches.open(PAGES);
  const keys = await cache.keys();
  let refreshed = 0;
  await Promise.all(
    keys.map((req) =>
      fetch(req.url, { credentials: "same-origin" })
        .then(async (res) => {
          if (!cacheable(res)) return;
          await cache.put(req, await stamped(res));
          refreshed += 1;
        })
        .catch(() => {})
    )
  );
  return { refreshed: refreshed, offline: false };
}

async function pageInfo(url) {
  const saved = await cachedPage(new Request(url, { credentials: "same-origin" }));
  if (!saved) return { cached: false };
  return { cached: true, at: saved.headers.get(STAMP) || saved.headers.get("date") || "" };
}

self.addEventListener("message", (event) => {
  const data = event.data || {};
  const reply = (payload) => {
    const port = event.ports && event.ports[0];
    if (port) port.postMessage(payload);
  };
  if (data.type === "bf-refresh") {
    event.waitUntil(refreshPages(!!data.force).then(reply));
    return;
  }
  if (data.type === "bf-page-info") {
    event.waitUntil(pageInfo(data.url || "/").then(reply));
    return;
  }
  if (data.type === "bf-clear-pages") {
    event.waitUntil(caches.delete(PAGES).then(() => reply({ cleared: true })));
    return;
  }
  if (data.type === "bf-interval") {
    reply({ offlineRefreshMs: OFFLINE_REFRESH_MS });
  }
});

/* Chrome only, and only for installed apps: same ten minute cadence. */
self.addEventListener("periodicsync", (event) => {
  if (event.tag === "bf-refresh") event.waitUntil(refreshPages(true));
});
