const CACHE_NAME = 'news-aggregator-v3';
const STATIC_ASSETS = [
  '/news/',
  '/news/health/',
  '/news/tags/',
  '/news/rss/',
  '/news/styles.css',
  '/news/app.js',
  '/news/feeds.js',
  '/news/health.js',
  '/news/tags.js',
  '/news/db/',
  '/news/db/db.js',
  '/news/article/',
  '/news/article/article.js',
  '/news/videos/',
  '/news/videos/videos.js'
];

// Install event - cache static assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        return cache.addAll(STATIC_ASSETS);
      })
      .catch((err) => {
        console.log('Cache install failed:', err);
      })
  );
  self.skipWaiting();
});

// Activate event - clean up old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
  self.clients.claim();
});

// Fetch event - serve from cache, fallback to network
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests
  if (request.method !== 'GET') {
    return;
  }

  // Skip external requests
  if (!url.pathname.startsWith('/news/')) {
    return;
  }

  event.respondWith(
    caches.match(request).then((cachedResponse) => {
      // Return cached version if available
      if (cachedResponse) {
        // For data files, still fetch in background to update cache
        if (url.pathname.includes('/data/')) {
          fetch(request)
            .then((networkResponse) => {
              if (networkResponse.ok) {
                caches.open(CACHE_NAME).then((cache) => {
                  cache.put(request, networkResponse);
                });
              }
            })
            .catch(() => {});
        }
        return cachedResponse;
      }

      // Otherwise fetch from network
      return fetch(request)
        .then((networkResponse) => {
          // Cache successful responses
          if (networkResponse.ok) {
            const responseToCache = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(request, responseToCache);
            });
          }
          return networkResponse;
        })
        .catch(() => {
          // Return offline fallback for navigation requests
          if (request.mode === 'navigate') {
            return caches.match('/news/');
          }
          return new Response('Offline', { status: 503 });
        });
    })
  );
});

// Handle background sync for offline form submissions (if needed later)
self.addEventListener('sync', (event) => {
  if (event.tag === 'sync-feeds') {
    event.waitUntil(syncFeeds());
  }
});

// Handle push notifications (if needed later)
self.addEventListener('push', (event) => {
  if (event.data) {
    const data = event.data.json();
    event.waitUntil(
      self.registration.showNotification(data.title, {
        body: data.body,
        icon: '/news/icons/icon-192x192.png',
        badge: '/news/icons/icon-72x72.png',
        data: data.url
      })
    );
  }
});

// Handle notification clicks
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  if (event.notification.data) {
    event.waitUntil(
      clients.openWindow(event.notification.data)
    );
  }
});
