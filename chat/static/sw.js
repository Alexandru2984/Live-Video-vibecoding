/* Minimal service worker: makes the app installable and shows a friendly
 * page when navigating while offline. Everything else goes to the network —
 * chat is live data, caching it would only serve stale rooms. */
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener('fetch', function (e) {
    if (e.request.mode !== 'navigate') return;  // pass through
    e.respondWith(fetch(e.request).catch(function () {
        return new Response(
            '<!doctype html><html lang="ro"><meta charset="utf-8">' +
            '<meta name="viewport" content="width=device-width, initial-scale=1">' +
            '<title>Offline</title>' +
            '<body style="font-family:system-ui;display:grid;place-items:center;min-height:100vh;margin:0;background:#0e1018;color:#e7e9f3;text-align:center">' +
            '<div><h1>Ești offline</h1><p>Reîncearcă atunci când ai din nou conexiune.</p></div></body></html>',
            { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
        );
    }));
});
