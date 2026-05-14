const STATIC_CACHE = 'tfda-static-v2';
const DATA_CACHE   = 'tfda-data-v2';
const FONT_CACHE   = 'tfda-fonts-v2';
const ALL_CACHES   = [STATIC_CACHE, DATA_CACHE, FONT_CACHE];

// ── Install：只快取輕量靜態資源，不預取 46MB JSON ──────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then(cache => cache.addAll(['./index.html', './manifest.json']))
      .then(() => self.skipWaiting())
  );
});

// ── Activate：清除舊版 cache ────────────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => !ALL_CACHES.includes(k)).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

function notifyClients(type) {
  self.clients.matchAll().then(clients =>
    clients.forEach(c => c.postMessage({ type }))
  );
}

// ── Fetch ───────────────────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Google Fonts：cache-first（字型不常變動，離線可用）
  if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(response => {
          if (response.ok) {
            caches.open(FONT_CACHE).then(c => c.put(event.request, response.clone()));
          }
          return response;
        }).catch(() => new Response('', { status: 503 }));
      })
    );
    return;
  }

  // drugs_data.json：network-first + cache fallback
  // 每次優先從網路取最新資料並更新快取；離線時回傳快取並通知頁面
  if (url.pathname.endsWith('drugs_data.json')) {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          if (response.ok) {
            caches.open(DATA_CACHE).then(c => c.put(event.request, response.clone()));
          }
          return response;
        })
        .catch(async () => {
          const cached = await caches.match(event.request);
          if (cached) {
            notifyClients('OFFLINE_MODE');
            return cached;
          }
          return new Response(
            JSON.stringify({ error: '無快取資料，請連線後重試' }),
            { status: 503, headers: { 'Content-Type': 'application/json' } }
          );
        })
    );
    return;
  }

  // 同源靜態資源：network-first，失敗回傳快取
  if (url.origin === self.location.origin) {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          if (response.ok) {
            caches.open(STATIC_CACHE).then(c => c.put(event.request, response.clone()));
          }
          return response;
        })
        .catch(() => caches.match(event.request))
    );
  }
});
