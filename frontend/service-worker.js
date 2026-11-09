const CACHE_NAME = "dairyai-shell-v2";
const SHELL_ASSETS = [
  "/",
  "/app.js",
  "/i18n.js",
  "/styles.css",
  "/manifest.json",
  "/icons/icon-192.png",
  "/icons/icon-512.png"
];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL_ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(key => key.startsWith("dairyai-shell-") && key !== CACHE_NAME)
        .map(key => caches.delete(key))
    ))
  );
  self.clients.claim();
});

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);
  if (
    request.method !== "GET" ||
    url.origin !== self.location.origin ||
    !SHELL_ASSETS.includes(url.pathname) ||
    request.headers.has("Authorization")
  ) return;

  event.respondWith(
    fetch(request).then(response => {
      if (response.ok && response.type === "basic") {
        const copy = response.clone();
        event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.put(request, copy)));
      }
      return response;
    }).catch(async () => {
      const cached = await caches.match(request);
      if (cached) return cached;
      return new Response("DairyAI लोड करण्यासाठी इंटरनेटशी पुन्हा जोडणी करा.", {
        status: 503,
        headers: {"Content-Type": "text/plain; charset=utf-8"}
      });
    })
  );
});
