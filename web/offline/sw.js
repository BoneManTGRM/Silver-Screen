const cacheName = "silver-screen-shell-v2";
const assets = [
  "index.html", "style.css", "app.mjs", "render.mjs", "gpu.mjs",
  "manifest.webmanifest", "icon-192.png", "icon-512.png",
];
const scope = new URL(self.registration.scope);
const assetURLs = assets.map(path => new URL(path, scope).href);
const entry = assetURLs[0];

self.addEventListener("install", event => {
  event.waitUntil((async () => {
    const cache = await caches.open(cacheName);
    const responses = await Promise.all(assetURLs.map(async url => {
      const response = await fetch(url, { cache: "reload", credentials: "same-origin" });
      if (!response.ok || response.redirected) throw new Error("Could not cache the offline app.");
      return response;
    }));
    await Promise.all(responses.map((response, index) => cache.put(assetURLs[index], response)));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names
      .filter(name => name.startsWith("silver-screen-shell-") && name !== cacheName)
      .map(name => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== scope.origin) return;
  const navigation = event.request.mode === "navigate" &&
    (url.pathname === scope.pathname || url.pathname === new URL(entry).pathname);
  const key = navigation ? entry : url.href;
  if (!assetURLs.includes(key)) return;
  event.respondWith((async () => {
    const cache = await caches.open(cacheName);
    return (await cache.match(key)) || fetch(event.request);
  })());
});
