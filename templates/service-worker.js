const CACHE_NAME = "manual-portal-static-v34";

const STATIC_ASSETS = [
    "/static/manifest.webmanifest",
    "/static/icons/app-icon.svg",
    "/static/icons/app-icon-192.png",
    "/static/icons/app-icon-512.png",
    "/static/icons/apple-touch-icon.png",
    "/static/css/base.css",
    "/static/css/calculator.css",
    "/static/css/delete.css",
    "/static/css/dispatch.css",
    "/static/css/form.css",
    "/static/css/home.css",
    "/static/css/list.css",
    "/static/css/manual_detail.css",
    "/static/css/manual_pdf_viewer.css",
    "/static/css/manual_search.css",
    "/static/css/portal_components.css",
    "/static/css/mobile_override.css",
    "/static/js/base.js",
];

self.addEventListener("install", function (event) {
    event.waitUntil(
        caches.open(CACHE_NAME).then(function (cache) {
            return cache.addAll(STATIC_ASSETS);
        })
    );

    self.skipWaiting();
});

self.addEventListener("activate", function (event) {
    event.waitUntil(
        caches.keys().then(function (keys) {
            return Promise.all(
                keys
                    .filter(function (key) {
                        return key !== CACHE_NAME;
                    })
                    .map(function (key) {
                        return caches.delete(key);
                    })
            );
        })
    );

    self.clients.claim();
});

self.addEventListener("fetch", function (event) {
    const request = event.request;

    if (request.method !== "GET") {
        return;
    }

    const url = new URL(request.url);

    if (url.origin !== self.location.origin) {
        return;
    }

    if (!url.pathname.startsWith("/static/")) {
        return;
    }

    event.respondWith(
        fetch(new Request(request, { cache: "reload" }))
            .then(function (response) {
                if (!response || response.status !== 200) {
                    return response;
                }

                const responseCopy = response.clone();

                caches.open(CACHE_NAME).then(function (cache) {
                    cache.put(request, responseCopy);
                });

                return response;
            })
            .catch(function () {
                return caches.match(request);
            })
    );
});
