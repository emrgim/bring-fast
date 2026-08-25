/* Bring Fast service worker — the whole app works offline.
 *
 * Online, every page comes from the network so an update shows up at once.
 * Offline, the last copy of a page is served and the client retries on a
 * ten minute cadence (BF_OFFLINE_REFRESH_MS) until the network is back.
 *
 * Nothing here is lazy: a page is saved with the pictures it needs, and the
 * tabs the app can reach are saved before they are asked for.
 */
const VERSION = "bf-pwa-v6";
const SHELL = VERSION + "-shell";
const PAGES = VERSION + "-pages";
const ASSETS = VERSION + "-assets";
const IMAGES = VERSION + "-images";
const OFFLINE_URL = "/offline";
const OFFLINE_REFRESH_MS = 600000;
const NET_TIMEOUT_MS = 6000;
const REFRESH_FLOOR_MS = 30000;
const WARM_FLOOR_MS = 60000;
/* Product shots come from the shops, so the shelf is capped instead of
 * growing for as long as the app is installed. A thumbnail is a few kilobytes
 * and an account can hold hundreds of products, so the cap holds a whole
 * shelf rather than evicting the top of the list the user just scrolled. */
const IMAGE_CAP = 700;
/* Warming a page in the background saves the shots that page draws, not every
 * shot it names: the rest arrive as the list fills, and a background flood is
 * exactly what made a tap wait. */
const SHOT_WARM = 12;
const WARM_AT_ONCE = 3;
const STAMP = "x-bf-cached-at";

const PRECACHE = [
  OFFLINE_URL,
  "/static/pwa/icon-192.png",
  "/static/pwa/icon-512.png",
  "/static/pwa/icon-180.png",
  "/static/pwa/icon-512-maskable.png",
  /* Every weight and subset, not just the two the head preloads: a weight
   * first met while offline would otherwise fall back mid-page. */
  "/static/fonts/ibm-plex-mono-400-latin.woff2",
  "/static/fonts/ibm-plex-mono-400-latin-ext.woff2",
  "/static/fonts/ibm-plex-mono-500-latin.woff2",
  "/static/fonts/ibm-plex-mono-500-latin-ext.woff2",
  "/static/fonts/ibm-plex-mono-600-latin.woff2",
  "/static/fonts/ibm-plex-mono-600-latin-ext.woff2",
  "/static/fonts/ibm-plex-mono-700-latin.woff2",
  "/static/fonts/ibm-plex-mono-700-latin-ext.woff2",
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

/* Shown when a form is sent with no network: the browser's own error page
 * would lose the app and say nothing about the change. */
const NOT_SAVED = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
<title>Not saved · Bring Fast</title>
<style>
  html,body{margin:0;background:#fff;color:#111;font-family:ui-monospace,Menlo,Consolas,monospace}
  @media (prefers-color-scheme:dark){html,body{background:#0a0a0a;color:#f5f5f5}.b{border-color:#f5f5f5!important}}
  .w{max-width:520px;margin:0 auto;padding:14vh 16px 40px}
  h1{font-size:24px;margin:0 0 10px;text-transform:uppercase;letter-spacing:.02em}
  p{line-height:1.5;margin:0 0 16px}
  .b{border:1px solid #111;padding:12px 16px;font:inherit;font-weight:700;cursor:pointer;background:transparent;color:inherit;touch-action:manipulation}
  @media (display-mode:standalone),(display-mode:fullscreen){html,body{touch-action:pan-x pan-y}}
  :root.installed,:root.installed body{touch-action:pan-x pan-y}
</style></head>
<body><div class="w">
<h1>Not saved</h1>
<p>Bring Fast could not be reached, so this change was not stored. Nothing was
half-written — go back and send it again once the connection is up.</p>
<button class="b" type="button" onclick="history.back()">Go back</button>
</div>
<script>
(function(){
  function mode(q){ return window.matchMedia ? matchMedia("(display-mode: "+q+")").matches : false; }
  if(navigator.standalone!==true && !mode("standalone") && !mode("fullscreen") && !mode("minimal-ui")) return;
  document.documentElement.classList.add("installed");
  var v=document.querySelector('meta[name="viewport"]');
  if(v) v.setAttribute("content","width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover");
  ["gesturestart","gesturechange","gestureend"].forEach(function(name){
    document.addEventListener(name, function(e){ e.preventDefault(); }, {passive:false});
  });
})();
</script>
</body></html>`;

let lastRefresh = 0;
let lastWarm = 0;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  const keep = [SHELL, PAGES, ASSETS, IMAGES];
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

/* `destination` is the reliable signal in both engines; Accept covers the
 * few requests that arrive without one. */
function isImage(req) {
  if (req.destination === "image") return true;
  const accept = req.headers.get("accept") || "";
  return accept.indexOf("image/") === 0 || accept.indexOf("image/webp") !== -1;
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

/* A sign-in page reached by following a redirect is not the page that was
 * asked for, and saving it there would show a login form named "Dashboard". */
function samePage(res) {
  return cacheable(res) && !res.redirected;
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
    if (samePage(res)) event.waitUntil(savePage(req, res.clone()));
    return res;
  } catch (e) {
    if (saved) return saved;
    const fallback = await caches.match(OFFLINE_URL);
    if (fallback) return fallback;
    return new Response("Offline", { status: 503, headers: { "Content-Type": "text/plain" } });
  }
}

/* Sending a form is never answered from a cache — it either reaches the
 * server or the page says plainly that nothing was stored. */
async function sent(event) {
  try {
    return await fetch(event.request);
  } catch (e) {
    return new Response(NOT_SAVED, {
      status: 503,
      headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
    });
  }
}

/* A few at a time, never all at once: warming in the background must leave the
 * wire free for whatever the person is actually doing. */
async function pool(items, width, run) {
  let at = 0;
  const hands = [];
  for (let i = 0; i < Math.min(width, items.length); i++) {
    hands.push(
      (async () => {
        while (at < items.length) await run(items[at++]);
      })()
    );
  }
  await Promise.all(hands);
}

async function trim(cache, cap) {
  const keys = await cache.keys();
  if (keys.length <= cap) return;
  await Promise.all(keys.slice(0, keys.length - cap).map((req) => cache.delete(req)));
}

function keepable(res) {
  return res && (res.ok || res.type === "opaque");
}

async function store(event, name, cap) {
  const req = event.request;
  const cache = await caches.open(name);
  const hit = await cache.match(req);
  if (hit) {
    /* Refresh in the background so an updated build lands on the next load. */
    event.waitUntil(
      fetch(req)
        .then((res) => (keepable(res) ? cache.put(req, res.clone()) : null))
        .catch(() => {})
    );
    return hit;
  }
  try {
    const res = await fetch(req);
    if (keepable(res)) {
      event.waitUntil(
        cache
          .put(req, res.clone())
          .then(() => (cap ? trim(cache, cap) : null))
          .catch(() => {})
      );
    }
    return res;
  } catch (e) {
    return new Response("", { status: 504 });
  }
}

function asset(event) {
  return store(event, ASSETS, 0);
}

/* Product shots live on the shops' own domains: they are saved opaque so a
 * saved page still shows its shelf with no network. */
function remote(event) {
  return store(event, IMAGES, IMAGE_CAP);
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  const sameOrigin = url.origin === self.location.origin;

  if (req.method !== "GET") {
    if (sameOrigin && req.mode === "navigate") event.respondWith(sent(event));
    return;
  }
  if (!sameOrigin) {
    if (isImage(req)) event.respondWith(remote(event));
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
  if (
    url.pathname.startsWith("/static/") ||
    url.pathname.startsWith("/receipts/") ||
    url.pathname === "/favicon.ico" ||
    url.pathname === "/apple-touch-icon.png" ||
    isImage(req)
  ) {
    event.respondWith(asset(event));
  }
});

/* A saved page is only readable if the pictures in it were saved too. Warming
 * a page fetches the page alone, so the store logos and product shots it names
 * are collected here — the ones already held are skipped, so the ten minute
 * cadence costs a cache lookup each and nothing more. */
async function warmShots(html, base) {
  const tag = /<img\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']/gi;
  const urls = [];
  let found;
  while ((found = tag.exec(html)) !== null && urls.length < SHOT_WARM) {
    if (!found[1] || found[1].indexOf("data:") === 0) continue;
    let url;
    try {
      url = new URL(found[1], base);
    } catch (e) {
      continue;
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") continue;
    if (urls.indexOf(url.href) === -1) urls.push(url.href);
  }
  if (!urls.length) return 0;
  const here = self.location.origin + "/";
  const local = await caches.open(ASSETS);
  const shops = await caches.open(IMAGES);
  let saved = 0;
  await pool(urls, WARM_AT_ONCE, async (href) => {
    const mine = href.indexOf(here) === 0;
    const cache = mine ? local : shops;
    try {
      if (await cache.match(href)) return;
      const res = await fetch(href, mine ? { credentials: "same-origin" } : { mode: "no-cors" });
      if (!keepable(res)) return;
      await cache.put(href, res);
      saved += 1;
    } catch (e) {}
  });
  if (saved) await trim(shops, IMAGE_CAP);
  return saved;
}

async function savedWith(cache, url, res) {
  const copy = res.clone();
  await cache.put(new Request(url, { credentials: "same-origin" }), await stamped(res));
  try {
    await warmShots(await copy.text(), new URL(url, self.location.origin).href);
  } catch (e) {}
}

/* Re-fetch everything saved so the next offline read is as fresh as the
 * cadence allows: at once while online, every ten minutes while not. */
async function refreshPages(force) {
  if (navigator.onLine === false) return { refreshed: 0, offline: true };
  const now = Date.now();
  if (!force && now - lastRefresh < REFRESH_FLOOR_MS) return { refreshed: 0, throttled: true };
  lastRefresh = now;
  const pages = await caches.open(PAGES);
  const keys = await pages.keys();
  let refreshed = 0;
  await Promise.all(
    keys.map((req) =>
      fetch(req.url, { credentials: "same-origin" })
        .then(async (res) => {
          if (!samePage(res)) return;
          await savedWith(pages, req.url, res);
          refreshed += 1;
        })
        .catch(() => {})
    )
  );
  /* The offline screen, icons and fonts are only fetched when this worker
   * installs, so an app update would otherwise leave an old copy behind. */
  const shell = await caches.open(SHELL);
  await Promise.all(
    PRECACHE.map((url) =>
      fetch(url, { cache: "reload" })
        .then((res) => (res && res.ok ? shell.put(url, res) : null))
        .catch(() => {})
    )
  );
  return { refreshed: refreshed, offline: false };
}

/* Saves the tabs the app can reach before anyone opens them, so the first
 * time the network drops there is already something to read. */
async function warmPages(urls, force) {
  if (navigator.onLine === false) return { warmed: 0, offline: true };
  const now = Date.now();
  if (!force && now - lastWarm < WARM_FLOOR_MS) return { warmed: 0, throttled: true };
  lastWarm = now;
  const pages = await caches.open(PAGES);
  let warmed = 0;
  await Promise.all(
    (urls || []).map((url) =>
      fetch(url, { credentials: "same-origin" })
        .then(async (res) => {
          if (!samePage(res)) return;
          await savedWith(pages, url, res);
          warmed += 1;
        })
        .catch(() => {})
    )
  );
  return { warmed: warmed, offline: false };
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
  if (data.type === "bf-warm") {
    event.waitUntil(warmPages(data.urls, !!data.force).then(reply));
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
