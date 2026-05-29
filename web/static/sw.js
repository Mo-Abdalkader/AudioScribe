// AudioScribe Service Worker
// Provides offline splash page and caches static assets

const CACHE_NAME = 'audioscribe-v1';
const OFFLINE_URL = '/offline.html';

// Static assets to cache on install
const PRECACHE_ASSETS = [
  '/',
  '/static/css/style.css',
  '/static/js/app.js',
  OFFLINE_URL,
];

// ── Install: precache static assets ─────────────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_ASSETS))
  );
  self.skipWaiting();
});

// ── Activate: clean up old caches ────────────────────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ── Fetch: network-first strategy ────────────────────────────────────────────
// For API calls: always network, never cache.
// For static assets: try network, fall back to cache.
// For navigation: show offline page if network fails.
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Never intercept API requests or Telegram webhook
  if (url.pathname.startsWith('/api/') || url.pathname === '/webhook') {
    return;
  }

  // Navigation requests — network first, offline fallback
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() =>
        caches.open(CACHE_NAME).then((cache) => cache.match(OFFLINE_URL))
      )
    );
    return;
  }

  // Static assets — cache first, then network
  if (
    url.pathname.startsWith('/static/') ||
    url.pathname === '/manifest.json'
  ) {
    event.respondWith(
      caches.match(request).then((cached) =>
        cached || fetch(request).then((response) => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          return response;
        })
      )
    );
    return;
  }

  // Everything else: network only
  event.respondWith(fetch(request));
});
