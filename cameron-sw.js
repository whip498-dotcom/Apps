// Catalyst Service Worker — makes the app work offline.
// Bump this version string when you push a new cameron_app.html so clients pull fresh code.
const CACHE = 'cameron-v2';
const FILES = ['./cameron_app.html'];

self.addEventListener('install', (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES)));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  // Only handle GET requests for same-origin HTML/JS/CSS/images.
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.origin !== self.location.origin) return;
  // Don't cache Anthropic API calls or any external
  e.respondWith(
    caches.match(e.request).then(cached => {
      // Network-first for the HTML (so updates propagate), cache fallback if offline
      if (e.request.destination === 'document' || url.pathname.endsWith('.html')) {
        return fetch(e.request)
          .then(resp => {
            const copy = resp.clone();
            caches.open(CACHE).then(c => c.put(e.request, copy));
            return resp;
          })
          .catch(() => cached || caches.match('./cameron_app.html'));
      }
      // Cache-first for everything else
      return cached || fetch(e.request).then(resp => {
        const copy = resp.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy));
        return resp;
      });
    })
  );
});
