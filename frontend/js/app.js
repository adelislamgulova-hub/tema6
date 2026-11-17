import { t, esc, $, money, loadI18n, makeApi, toast, sheet, fmtDT, isoUtc, zonedToDate, dateToZoned, nextSlot } from "./core.js";
import { createMap } from "./map.js";
import { owner } from "./owner.js";

const api = makeApi("pc_token");
const S = { cfg: null, user: null, tz: "Europe/Moscow", time: null, filters: {}, q: "", me: null, map: null, places: [] };
const screen = $("#screen");

// ---------- старт и маршрутизация ----------
async function boot() {
  await loadI18n();
  $("#t-map").textContent = t("tab.map"); $("#t-bookings").textContent = t("tab.bookings"); $("#t-profile").textContent = t("tab.profile");
  S.cfg = await api.get("/config");
  S.tz = S.cfg.cities[0].timezone;
  await refreshUser();
  window.addEventListener("hashchange", route);
  route();
}
async function refreshUser() { S.user = api.token() ? await api.get("/me").catch(() => null) : null; }

const routes = [
  [/^#\/map$/, mapScreen], [/^#\/bookings$/, bookingsScreen], [/^#\/booking\/(\d+)$/, (id) => bookingScreen(id)],
  [/^#\/pay\/(\d+)$/, (id) => payScreen(id)], [/^#\/profile$/, profileScreen],
  [/^#\/owner(\/.*)?$/, (sub) => owner(ctx(), sub || "")],
];
function ctx() { return { api, S, screen, rerender: route, login: requireLogin, go: (h) => (location.hash = h), refreshUser, mapFactory: createMap }; }
async function route() {
  const h = location.hash || "#/map";
  document.querySelectorAll("#tabs a").forEach((a) => a.classList.toggle("on", h.startsWith("#/" + a.dataset.tab) || (a.dataset.tab === "profile" && h.startsWith("#/owner"))));
  const m = routes.map(([re, fn]) => [re.exec(h), fn]).find(([r]) => r);
  if (!m) return (location.hash = "#/map");
  screen.className = "";
  clearInterval(payTimer);
  try { await m[1](...m[0].slice(1)); } catch (e) { handleErr(e); }
}
function handleErr(e) {
  if (e.status === 401) { S.user = null; return requireLogin().then(route); }
  if (e.code === "update_required") { screen.innerHTML = `<div class="pad"><h2>${esc(e.message)}</h2></div>`; return; }
  toast(e.message || t("common.error"));
}

// ---------- вход по коду ----------
function requireLogin() {
  return new Promise((resolve, reject) => {
    if (S.user) return resolve(S.user);
    const sh = sheet(`
      <h3>${t("login.title")}</h3><p class="muted">${t("login.hint")}</p>
      <input id="l-id" autocomplete="username" placeholder="${t("login.identifier")}" inputmode="email">
      <div id="l-step2" hidden><input id="l-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="${t("login.code")}"><p id="l-dev" class="muted"></p></div>
      <button class="btn" id="l-go">${t("login.get_code")}</button>`, { onClose: () => { if (!done) reject(new Error("closed")); } });
    let step = 1, done = false;
    $("#l-go", sh.el).onclick = async () => {
      try {
        const ident = $("#l-id", sh.el).value;
        if (step === 1) {
          const r = await api.post("/auth/request-code", { identifier: ident });
          step = 2; $("#l-step2", sh.el).hidden = false; $("#l-go", sh.el).textContent = t("login.enter"); $("#l-code", sh.el).focus();
          if (r.dev_code) $("#l-dev", sh.el).textContent = t("login.dev_code", { code: r.dev_code });
        } else {
          const r = await api.post("/auth/verify", { identifier: ident, code: $("#l-code", sh.el).value });
          api.setToken(r.token); await refreshUser(); done = true; sh.close(); resolve(S.user);
        }
      } catch (e) { toast(e.message); }
    };
  });
}

// ---------- карта ----------
async function mapScreen() {
  screen.className = "map-screen";
  const c0 = S.cfg.cities[0];
  screen.innerHTML = `
    <div id="map"></div>
    <div class="topbar">
      <input id="q" type="search" placeholder="${t("map.search")}" value="${esc(S.q)}">
      <button class="chip" id="when"></button>
    </div>
    <div class="side"><button class="fab" id="filters">⚙<small>${t("map.filters")}</small></button><button class="fab" id="locate">◎<small>${t("map.me")}</small></button></div>`;
  S.map = await createMap($("#map"), S.me || [c0.lat, c0.lon]);
  if (S.me) S.map.setMe(S.me);
  const label = () => ($("#when").textContent = S.time ? `${fmtDT(isoUtc(S.time.start), S.tz)} · ${durLabel(S.time.minutes)}` : t("map.now"));
  label();
  let timer; $("#q").oninput = (e) => { S.q = e.target.value; clearTimeout(timer); timer = setTimeout(load, 350); };
  $("#when").onclick = () => timeSheet(() => { label(); load(); });
  $("#filters").onclick = () => filterSheet(load);
  $("#locate").onclick = () => locate(false);
  async function load() {
    const p = new URLSearchParams({ city_id: c0.id });
    const f = S.filters;
    if (S.me) { p.set("lat", S.me[0]); p.set("lon", S.me[1]); }
    if (S.q) p.set("q", S.q);
    if (f.price) p.set("max_price", f.price);
    if (f.radius && S.me) p.set("radius_m", f.radius);
    if (f.type) p.set("type_id", f.type);
    if (f.free) p.set("only_free", "1");
    if (S.time) { p.set("from", isoUtc(S.time.start)); p.set("to", isoUtc(new Date(S.time.start.getTime() + S.time.minutes * 60000))); }
    S.places = await api.get("/places?" + p);
    S.map.setMarkers(S.places, openPlace);
    if (S.q && S.places[0]) S.map.setCenter([S.places[0].lat, S.places[0].lon]);
  }
  function locate(silent) {
    navigator.geolocation?.getCurrentPosition((pos) => { S.me = [pos.coords.latitude, pos.coords.longitude]; S.map.setMe(S.me); S.map.setCenter(S.me, 16); load(); },
      () => silent || toast(t("map.locate_fail")), { enableHighAccuracy: true, timeout: 8000 });
  }
  if (!S.me) locate(true);   // сразу показываем «себя» и ближайшие парковки
  await load();
}
const durLabel = (m) => (m % 60 ? `${Math.floor(m / 60) ? Math.floor(m / 60) + " " + t("common.hour") + " " : ""}${m % 60} ${t("common.min")}` : `${m / 60} ${t("common.hour")}`);
const durOptions = (sel) => Array.from({ length: 48 }, (_, i) => (i + 1) * 30).map((m) => `<option value="${m}" ${m === sel ? "selected" : ""}>${durLabel(m)}</option>`).join("");

function timeSheet(done) {
  const cur = S.time || { start: nextSlot(S.tz), minutes: 60 };
  const sh = sheet(`<h3>${t("time.title")}</h3>
    <label>${t("time.start")}<input id="t-start" type="datetime-local" step="1800" value="${dateToZoned(cur.start, S.tz)}"></label>
    <label>${t("time.duration")}<select id="t-dur">${durOptions(cur.minutes)}</select></label>
    <button class="btn" id="t-ok">${t("time.apply")}</button><button class="btn ghost" id="t-reset">${t("time.reset")}</button>`);
  $("#t-ok", sh.el).onclick = () => { S.time = { start: zonedToDate($("#t-start", sh.el).value, S.tz), minutes: +$("#t-dur", sh.el).value }; sh.close(); done(); };
  $("#t-reset", sh.el).onclick = () => { S.time = null; sh.close(); done(); };
}
function filterSheet(done) {
  const f = S.filters;
  const sh = sheet(`<h3>${t("filter.title")}</h3>
    <label>${t("filter.price")}<input id="f-price" type="number" min="0" value="${f.price || ""}"></label>
    <label>${t("filter.radius")}<input id="f-radius" type="number" min="0" step="100" value="${f.radius || ""}"></label>
    <label>${t("filter.type")}<select id="f-type"><option value="">${t("filter.any")}</option>${S.cfg.parking_types.map((x) => `<option value="${x.id}" ${f.type == x.id ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></label>
    <label class="row"><input id="f-free" type="checkbox" ${f.free ? "checked" : ""}> ${t("filter.only_free")}</label>
    <button class="btn" id="f-ok">${t("filter.apply")}</button><button class="btn ghost" id="f-reset">${t("filter.reset")}</button>`);
  $("#f-ok", sh.el).onclick = () => { S.filters = { price: $("#f-price", sh.el).value, radius: $("#f-radius", sh.el).value, type: $("#f-type", sh.el).value, free: $("#f-free", sh.el).checked }; sh.close(); done(); };
  $("#f-reset", sh.el).onclick = () => { S.filters = {}; sh.close(); done(); };
}

// карточка места выезжает снизу: метка -> «Забронировать» -> «Оплатить»
async function openPlace(p) {
  let time = S.time || { start: nextSlot(S.tz), minutes: 60 };
  const sh = sheet("");
  const render = async () => {
    const end = new Date(time.start.getTime() + time.minutes * 60000);
    const q = await api.get(`/places/${p.id}/quote?from=${isoUtc(time.start)}&to=${isoUtc(end)}`).catch((e) => ({ error: e }));
    const days = [0, 1, 2, 3, 4, 5, 6].map((d) => `<tr><td>${t("day." + d)}</td><td>${(p.schedule[d] || []).map((w) => w.join("–")).join(", ") || t("day.closed")}</td></tr>`).join("");
    const needConsent = !S.user || !S.user.consent;
    sh.el.innerHTML = `
      ${p.photos.length ? `<div class="photos">${p.photos.map((u) => `<img src="${esc(u)}" alt="">`).join("")}</div>` : ""}
      <div class="row between"><h3>${t("place.price_h", { price: p.price_per_hour_kop / 100 })}</h3><span class="badge ${p.status}">${t("status." + p.status)}</span></div>
      <p><b>${esc(p.address)}</b><br><span class="muted">${esc(p.type || "")}${p.distance_m != null ? " · " + t("place.distance", { m: p.distance_m }) : ""}</span></p>
      ${p.description ? `<p>${esc(p.description)}</p>` : ""}
      ${p.entry_rules ? `<p class="muted"><b>${t("place.rules")}:</b> ${esc(p.entry_rules)}</p>` : ""}
      <details><summary>${t("place.schedule")}</summary><table class="sched">${days}</table></details>
      <a class="link" target="_blank" rel="noopener" href="https://yandex.ru/maps/?rtext=~${p.lat},${p.lon}&rtt=auto">🧭 ${t("place.route")}</a>
      <hr>
      <div class="row between"><span>${fmtDT(isoUtc(time.start), S.tz)} · ${durLabel(time.minutes)}</span><button class="link" id="chg">${t("place.change_time")}</button></div>
      ${q.error ? `<p class="err">${esc(q.error.message)}</p>` : !q.available ? `<p class="err">${t("place.unavailable_now")}</p>` : `<p class="muted small">${t("place.refund_hint")}</p>`}
      ${needConsent && q.available ? `<label class="row small"><input type="checkbox" id="consent"> ${t("place.consent")}</label>` : ""}
      <div class="row between"><span class="total">${t("place.total")}: ${q.total_price_kop ? money(q.total_price_kop) : "—"}</span>
      <button class="btn" id="book" ${q.available ? "" : "disabled"}>${t("place.book")}</button></div>`;
    $("#chg", sh.el).onclick = () => {
      const s2 = sheet(`<h3>${t("time.title")}</h3><label>${t("time.start")}<input id="x-s" type="datetime-local" step="1800" value="${dateToZoned(time.start, S.tz)}"></label>
        <label>${t("time.duration")}<select id="x-d">${durOptions(time.minutes)}</select></label><button class="btn" id="x-ok">${t("time.apply")}</button>`);
      $("#x-ok", s2.el).onclick = () => { time = { start: zonedToDate($("#x-s", s2.el).value, S.tz), minutes: +$("#x-d", s2.el).value }; s2.close(); render(); };
    };
    $("#book", sh.el).onclick = async () => {
      try {
        if (needConsent && !$("#consent", sh.el)?.checked) return toast(t("place.consent"));
        const consent = needConsent;
        if (!S.user) { await requireLogin(); }
        const b = await api.post("/bookings", { place_id: p.id, start: isoUtc(time.start), end: isoUtc(end), consent: consent || undefined });
        sh.close(); location.hash = `#/pay/${b.id}`;
      } catch (e) { if (e.message !== "closed") toast(e.message); }
    };
  };
  await render();
}

// ---------- оплата (форму карты не делаем: платит провайдер) ----------
let payTimer;
async function payScreen(id) {
  const b = await api.get(`/bookings/${id}`);
  if (b.status !== "pending_payment") return (location.hash = `#/booking/${id}`);
  const tzone = S.tz;
  screen.innerHTML = `<div class="pad"><h2>${t("pay.title")}</h2>
    <p><b>${esc(b.address)}</b><br>${fmtDT(b.start, tzone)} – ${fmtDT(b.end, tzone)}</p>
    <p class="timer" id="timer"></p>
    <div class="paybar"><button class="btn big" id="pay">${t("pay.button", { price: money(b.total_price_kop) })}</button></div>
    <button class="btn ghost" id="cancel">${t("pay.cancel")}</button></div>`;
  clearInterval(payTimer);
  const tick = () => {
    if (!$("#timer")) return clearInterval(payTimer);
    const left = Math.floor((new Date(b.expires_at) - Date.now()) / 1000);
    if (left <= 0) { clearInterval(payTimer); $("#timer").textContent = t("pay.expired"); $("#pay").disabled = true; return; }
    $("#timer").textContent = t("pay.timer", { time: `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` });
  };
  tick(); payTimer = setInterval(tick, 1000);
  $("#pay").onclick = async () => {
    try {
      if (S.cfg.payment_provider === "mock") { await api.post(`/payments/${b.payment_id}/mock-pay`); location.hash = `#/booking/${id}`; }
      else { toast(t("pay.redirect")); location.href = b.payment_url; }
    } catch (e) { toast(e.message); }
  };
  $("#cancel").onclick = async () => { await api.post(`/bookings/${id}/cancel`); location.hash = "#/map"; };
}

// ---------- мои брони ----------
let bTab = "upcoming";
async function bookingsScreen() {
  if (!S.user) { screen.innerHTML = `<div class="pad"><p>${t("profile.login_prompt")}</p><button class="btn" id="li">${t("login.title")}</button></div>`; $("#li").onclick = () => requireLogin().then(route); return; }
  const list = await api.get(`/bookings?scope=${bTab}`);
  screen.innerHTML = `<div class="pad"><h2>${t("bookings.title")}</h2>
    <div class="seg"><button data-s="upcoming" class="${bTab === "upcoming" ? "on" : ""}">${t("bookings.upcoming")}</button><button data-s="past" class="${bTab === "past" ? "on" : ""}">${t("bookings.past")}</button></div>
    ${list.length ? list.map((b) => bookingCard(b)).join("") : `<p class="muted">${t("bookings.empty")}</p>`}</div>`;
  screen.querySelectorAll(".seg button").forEach((x) => (x.onclick = () => { bTab = x.dataset.s; bookingsScreen(); }));
}
const bookingCard = (b, href = `#/booking/${b.id}`) => `<a class="card" href="${href}"><div class="row between"><b>${esc(b.address)}</b><span class="badge b-${b.status}">${t("bstatus." + b.status)}</span></div>
  <div class="muted">${fmtDT(b.start, S.tz)} – ${fmtDT(b.end, S.tz)} · ${money(b.total_price_kop)}</div></a>`;

async function bookingScreen(id) {
  const b = await api.get(`/bookings/${id}`);
  const live = ["pending_payment", "confirmed", "active"].includes(b.status);
  const pv = b.cancel_preview;
  screen.innerHTML = `<div class="pad"><a class="link" href="#/bookings">← ${t("common.back")}</a><h2>${t("booking.title", { id: b.id })}</h2>
    <span class="badge b-${b.status}">${t("bstatus." + b.status)}</span>
    <p><b>${esc(b.address)}</b><br>${fmtDT(b.start, S.tz)} – ${fmtDT(b.end, S.tz)}<br>${money(b.total_price_kop)}</p>
    ${b.refunded_kop ? `<p>${t("booking.refunded", { sum: money(b.refunded_kop) })}</p>` : ""}
    ${b.cancelled_by === "owner" ? `<p class="err">${t("booking.cancelled_by_owner", { reason: esc(b.cancel_reason) })}</p>` : ""}
    ${b.status === "pending_payment" ? `<a class="btn" href="#/pay/${b.id}">${t("pay.button", { price: money(b.total_price_kop) })}</a>` : ""}
    ${b.entry_rules && b.paid_kop ? `<p><b>${t("booking.entry")}:</b> ${esc(b.entry_rules)}</p>` : ""}
    <a class="link" target="_blank" rel="noopener" href="https://yandex.ru/maps/?rtext=~${b.lat},${b.lon}&rtt=auto">🧭 ${t("place.route")}</a>
    <div class="stack">
      ${b.status === "confirmed" ? `<button class="btn ghost" id="shift">${t("booking.shift")}</button>` : ""}
      ${live && b.status !== "pending_payment" ? `<button class="btn danger" id="cancel">${t("booking.cancel")}</button>` : ""}
      ${b.paid_kop ? `<button class="btn ghost" id="receipts">${t("booking.receipts")}</button><button class="btn ghost" id="complain">${t("booking.complaint")}</button>` : ""}
    </div></div>`;
  $("#cancel") && ($("#cancel").onclick = async () => {
    if (!confirm(pv.refund_kop ? t("booking.cancel_confirm", { sum: money(pv.refund_kop) }) : t("booking.cancel_confirm_zero"))) return;
    await api.post(`/bookings/${id}/cancel`).catch((e) => toast(e.message)); route();
  });
  $("#shift") && ($("#shift").onclick = () => {
    const sh = sheet(`<h3>${t("booking.shift")}</h3><label>${t("booking.shift_to")}<input id="s-start" type="datetime-local" step="1800" value="${dateToZoned(new Date(b.start), S.tz)}"></label><button class="btn" id="s-ok">${t("common.save")}</button>`);
    $("#s-ok", sh.el).onclick = async () => { try { await api.patch(`/bookings/${id}`, { start: isoUtc(zonedToDate($("#s-start", sh.el).value, S.tz)) }); sh.close(); route(); } catch (e) { toast(e.message); } };
  });
  $("#receipts") && ($("#receipts").onclick = async () => showReceipts(await api.get(`/bookings/${id}/receipts`)));
  $("#complain") && ($("#complain").onclick = () => {
    const sh = sheet(`<h3>${t("booking.complaint")}</h3><select id="c-kind">${["occupied", "mismatch", "other"].map((k) => `<option value="${k}">${t("complaint." + k)}</option>`).join("")}</select>
      <textarea id="c-text" placeholder="${t("complaint.text")}"></textarea><button class="btn" id="c-ok">${t("complaint.send")}</button>`);
    $("#c-ok", sh.el).onclick = async () => { try { await api.post(`/bookings/${id}/complaint`, { kind: $("#c-kind", sh.el).value, description: $("#c-text", sh.el).value }); sh.close(); toast(t("booking.complaint_sent")); } catch (e) { toast(e.message); } };
  });
}
function showReceipts(list) {
  const sh = sheet(`<h3>${t("booking.receipts")}</h3>${list.map((r, i) => `<div class="card"><b>${r.kind === "refund" ? "↩ " : ""}${t("booking.receipt", { number: esc(r.number) })}</b><br>${esc(r.seller)}<br>${esc(r.address)}<br>${money(r.amount_kop)} · ${fmtDT(r.created_at, S.tz)}</div>`).join("")}
    <button class="btn ghost" onclick="window.print()">🖨</button>`);
}

// ---------- профиль ----------
async function profileScreen() {
  if (!S.user) { screen.innerHTML = `<div class="pad"><h2>${t("profile.title")}</h2><p>${t("profile.login_prompt")}</p><button class="btn" id="li">${t("login.title")}</button></div>`; $("#li").onclick = () => requireLogin().then(route); return; }
  const notes = await api.get("/notifications").catch(() => []);
  screen.innerHTML = `<div class="pad"><h2>${t("profile.title")}</h2><p class="muted">${esc(S.user.phone || S.user.email || "")}</p>
    <label>${t("profile.name")}<input id="p-name" value="${esc(S.user.name || "")}"></label>
    <button class="btn" id="p-save">${t("common.save")}</button>
    <a class="btn ghost" href="#/owner">${t("profile.owner")}</a>
    <h3>${t("profile.notifications")}</h3>${notes.map((n) => `<div class="note">${esc(n.text)}<small class="muted"> ${new Date(n.created_at).toLocaleString("ru-RU")}</small></div>`).join("")}
    <div class="stack"><button class="btn ghost" id="out">${t("profile.logout")}</button><button class="btn danger" id="del">${t("profile.delete")}</button></div></div>`;
  $("#p-save").onclick = async () => { S.user = { ...S.user, ...(await api.patch("/me", { name: $("#p-name").value })) }; toast(t("profile.saved")); };
  $("#out").onclick = async () => { await api.post("/auth/logout").catch(() => {}); api.setToken(null); S.user = null; route(); };
  $("#del").onclick = async () => { if (!confirm(t("profile.delete_confirm"))) return; try { await api.del("/me"); api.setToken(null); S.user = null; location.hash = "#/map"; } catch (e) { toast(e.message); } };
}

boot();
