/* 서비스워커 — 오프라인에서도 열리도록 핵심 파일을 캐시한다.
   마음돌봄 워크북과 동물 대결 게임 두 앱을 함께 담당한다.
   기록 데이터는 각 앱의 localStorage에 있으므로 여기서 다루지 않는다. */
const CACHE = "maeumdolbom-v2";
const ASSETS = [
  "./self-esteem.html",
  "./manifest.webmanifest",
  "./icon-192.png",
  "./icon-512.png",
  "./icon-maskable-512.png",
  "./apple-touch-icon.png",
  "./animal-battle.html",
  "./animal-battle.webmanifest",
  "./battle-icon-192.png",
  "./battle-icon-512.png",
  "./battle-icon-maskable-512.png",
  "./battle-apple-touch-icon.png"
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// 네트워크 우선, 실패 시 캐시 (항상 최신을 받되 오프라인이면 캐시로 폴백)
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        return res;
      })
      .catch(() =>
        caches.match(e.request).then((r) => {
          if (r) return r;
          // 오프라인에서 주소만 열었을 때 알맞은 앱 화면으로 되돌려 준다
          const path = new URL(e.request.url).pathname;
          return caches.match(path.includes("animal-battle") ? "./animal-battle.html" : "./self-esteem.html");
        })
      )
  );
});
