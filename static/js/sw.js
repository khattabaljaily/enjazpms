/* EnjazIMS Service Worker — app-shell only, no data caching */
const CACHE = 'enjaz-shell-v1';
const SHELL = [
  '/static/css/main.css',
  '/static/css/layout.css',
  '/static/css/components.css',
  '/static/css/auth.css',
  '/static/js/main.js',
  '/static/img/logo/logo-6547bd.png',
  '/static/img/logo/logo-ffffff.png',
  '/static/img/icons/icon-192x192.png',
  '/static/fonts/cairo/Cairo-Regular.ttf',
  '/static/fonts/cairo/Cairo-Bold.ttf',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const { request } = e;
  const url = new URL(request.url);

  /* Only handle same-origin GET requests */
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;

  /* Static assets — cache first */
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(request).then(cached => cached || fetch(request).then(res => {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(request, clone));
        return res;
      }))
    );
    return;
  }

  /* HTML pages — network first, fall back to cache */
  e.respondWith(
    fetch(request).catch(() => caches.match(request))
  );
});
