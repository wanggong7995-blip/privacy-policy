/* 사이트 전체 서비스워커 — 이 저장소의 웹앱들을 하나가 담당한다.

   서비스워커는 같은 범위(scope)에 하나만 살아남는다. 예전처럼 앱마다 서비스워커
   파일을 따로 두면, 나중에 연 앱의 등록이 앞선 등록을 대체하고 activate 에서
   다른 캐시를 지워 버려서 두 앱이 서로의 오프라인 캐시를 무너뜨린다.
   그래서 파일 하나로 합쳤다.

   - 앱 셸과 아이콘: 설치할 때 미리 캐시 (오프라인에서 바로 열리도록)
   - 그 밖의 같은 출처 파일: 한 번 열어 본 것만 런타임 캐시 (예: summaries/*.md)
   - 다른 출처(시세·뉴스·AI API 등): 가로채지 않고 그대로 통과
   - 각 앱의 기록은 localStorage 에 있으므로 여기서 다루지 않는다. */

const CACHE = "doit-site-v1";

/* 오프라인에서 주소만 열었을 때 되돌려 줄 각 앱의 첫 화면 */
const APP_SHELLS = [
  "./index.html",
  "./self-esteem.html",
  "./animal-battle.html",
  "./stock-assistant.html",
  "./youtube-daily.html"
];

const ASSETS = APP_SHELLS.concat([
  "./manifest.webmanifest",
  "./animal-battle.webmanifest",
  "./stock-assistant-manifest.webmanifest",
  "./youtube-daily-manifest.webmanifest",
  "./favicon-64.png",
  "./apple-touch-icon.png",
  "./icon-192.png",
  "./icon-512.png",
  "./icon-maskable-512.png",
  "./battle-apple-touch-icon.png",
  "./battle-icon-192.png",
  "./battle-icon-512.png",
  "./battle-icon-maskable-512.png"
]);

/* 주소에 담긴 앱 이름으로 알맞은 첫 화면을 고른다 */
function shellFor(pathname) {
  const hit = APP_SHELLS.find((f) => pathname.includes(f.slice(2, -5)));
  return hit || "./index.html";
}

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches
      .open(CACHE)
      // 파일 하나가 없어도 설치가 통째로 실패하지 않도록 하나씩 담는다
      .then((c) => Promise.all(ASSETS.map((u) => c.add(u).catch(() => {}))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// 네트워크 우선, 실패하면 캐시 (항상 최신을 받되 오프라인이면 캐시로 폴백)
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;

  const url = new URL(e.request.url);
  if (url.origin !== self.location.origin) return; // 외부 API는 그대로 통과

  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() =>
        caches.match(e.request).then((cached) => {
          if (cached) return cached;
          if (e.request.mode === "navigate") return caches.match(shellFor(url.pathname));
          return new Response("", { status: 504, statusText: "오프라인" });
        })
      )
  );
});
