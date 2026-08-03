const CACHE_NAME = 'hanstock-v3-3-preview-shell-v1';
const SHELL = [
  './index.html','./manifest.webmanifest','./ui/design-system.css','./ui/config.js','./ui/mock-data.js','./ui/states.js','./ui/api-client.js','./ui/app-shell.js','./ui/page-controller.js',
  './ui/01-realtime.html','./ui/02-groups.html','./ui/03-rule1.html','./ui/04-rule2.html','./ui/05-stock-detail.html','./ui/06-system.html',
  './icons/icon-192.png','./icons/icon-512.png'
];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener('activate', event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.pathname.startsWith('/api/')) return; // Never cache market/API responses.
  event.respondWith(fetch(request).then(response => {
    if (response.ok && url.origin === location.origin) caches.open(CACHE_NAME).then(cache => cache.put(request, response.clone()));
    return response;
  }).catch(() => caches.match(request).then(r => r || caches.match('./index.html'))));
});
