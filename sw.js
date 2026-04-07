const CACHE_NAME = 'accessible-camera-v1';
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/manifest.json'
];

// Install: cache static assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(STATIC_ASSETS);
    })
  );
  self.skipWaiting();
});

// Activate: clean up old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
  self.clients.claim();
});

// Fetch: serve from cache, fall back to network
self.addEventListener('fetch', event => {
  const { request } = event;
  
  // Skip non-GET requests
  if (request.method !== 'GET') {
    return;
  }
  
  // For stream and API, always use network
  if (request.url.includes('/stream.mjpg') || request.url.includes('/api/')) {
    return event.respondWith(
      fetch(request)
        .catch(() => {
          // If offline, return a placeholder response
          return new Response('Connection lost', {
            status: 503,
            statusText: 'Service Unavailable'
          });
        })
    );
  }
  
  // For static assets, cache first then network
  event.respondWith(
    caches.match(request).then(response => {
      if (response) {
        return response;
      }
      return fetch(request).then(response => {
        // Don't cache non-successful responses
        if (!response || response.status !== 200) {
          return response;
        }
        // Cache successful responses
        const responseToCache = response.clone();
        caches.open(CACHE_NAME).then(cache => {
          cache.put(request, responseToCache);
        });
        return response;
      });
    })
  );
});
