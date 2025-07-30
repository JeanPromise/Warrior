const CACHE_NAME = "tomorrow-v1";
const OFFLINE_URL = "/offline";

const urlsToCache = [
  "/",
  "/music",
  "/static/manifest.json",
  "/static/media/logo.png",
  "/static/tailwind.css", // Optional if you're serving your own CSS
];

// Install
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(urlsToCache);
    })
  );
  self.skipWaiting();
});

// Activate
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keyList) =>
      Promise.all(
        keyList.map((key) => {
          if (key !== CACHE_NAME) return caches.delete(key);
        })
      )
    )
  );
  self.clients.claim();
});

// Fetch
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;

  event.respondWith(
    fetch(event.request).catch(() =>
      caches.match(event.request).then((response) =>
        response || caches.match(OFFLINE_URL)
      )
    )
  );
});
