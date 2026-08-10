const CACHE = "paperpush-v5";
self.addEventListener("install", event => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(
  caches.keys().then(keys => Promise.all(keys.map(k => caches.delete(k)))).then(() => self.clients.claim())
));
self.addEventListener("fetch", event => {
  const req = event.request;
  if (req.method !== "GET" || req.url.includes("/api/")) return; // 让所有 API 与写操作直连网络
  // HTML 文档始终走网络优先，避免旧界面被缓存导致按钮行为过期。
  if (req.mode === "navigate" || req.destination === "document") {
    event.respondWith(fetch(req).catch(() => caches.match(req)));
    return;
  }
  event.respondWith(
    fetch(req).then(resp => {
      const copy = resp.clone();
      caches.open(CACHE).then(c => c.put(req, copy));
      return resp;
    }).catch(() => caches.match(req))
  );
});
