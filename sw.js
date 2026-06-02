/* סוד הדף — Service Worker להתראות פוש */
const WORKER_URL = "https://sod-hadaf-qa.24locksmith24.workers.dev";

self.addEventListener("install", (e) => { self.skipWaiting(); });
self.addEventListener("activate", (e) => { e.waitUntil(self.clients.claim()); });

/* קבלת פוש — מביא את ההודעה העדכנית ומציג התראה */
self.addEventListener("push", (event) => {
  event.waitUntil((async () => {
    let title = "סוֹד הַדַּף", body = "יש עדכון חדש 🕯️";
    // אם הגיע payload — השתמש בו
    try {
      if (event.data) {
        const d = event.data.json();
        if (d.title) title = d.title;
        if (d.body) body = d.body;
      }
    } catch (e) {}
    // אחרת — משוך את ההודעה העדכנית מהשרת
    if (body === "יש עדכון חדש 🕯️") {
      try {
        const r = await fetch(WORKER_URL + "/push-message", { cache: "no-store" });
        if (r.ok) {
          const m = await r.json();
          if (m.title) title = m.title;
          if (m.body) body = m.body;
        }
      } catch (e) {}
    }
    await self.registration.showNotification(title, {
      body,
      icon: "https://sodhadaf.com/icon-192.png",
      badge: "https://sodhadaf.com/icon-192.png",
      dir: "rtl",
      lang: "he",
      tag: "sodhadaf-alert",
      renotify: true,
      data: { url: "https://sodhadaf.com" },
    });
  })());
});

/* לחיצה על ההתראה — פתיחת האתר */
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const url = (event.notification.data && event.notification.data.url) || "https://sodhadaf.com";
    const all = await clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of all) { if (c.url.includes("sodhadaf") && "focus" in c) return c.focus(); }
    if (clients.openWindow) return clients.openWindow(url);
  })());
});
