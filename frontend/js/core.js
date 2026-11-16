// Общие помощники: тексты, API, экранирование, деньги, время.
export const cfg = window.PARKCITY;
let dict = {};
export async function loadI18n() { dict = await (await fetch("/i18n/ru.json")).json(); }
export const t = (k, v = {}) => (dict[k] ?? k).replace(/\{(\w+)\}/g, (_, n) => v[n] ?? "");

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const $ = (sel, el = document) => el.querySelector(sel);
export const money = (kop) => `${(kop / 100).toLocaleString("ru-RU", { minimumFractionDigits: kop % 100 ? 2 : 0 })} ₽`;

export function makeApi(tokenKey) {
  const get = () => localStorage.getItem(tokenKey);
  async function call(method, path, body, isForm = false) {
    const headers = { "X-Client-Version": cfg.CLIENT_VERSION };
    if (get()) headers.Authorization = "Bearer " + get();
    if (body && !isForm) headers["Content-Type"] = "application/json";
    const res = await fetch(cfg.API + path, { method, headers, body: body ? (isForm ? body : JSON.stringify(body)) : undefined });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (res.status === 401) localStorage.removeItem(tokenKey);
      const e = new Error(data.error?.message || t("common.error"));
      e.code = data.error?.code; e.status = res.status;
      throw e;
    }
    return data;
  }
  return {
    get: (p) => call("GET", p), post: (p, b) => call("POST", p, b || {}), patch: (p, b) => call("PATCH", p, b), del: (p) => call("DELETE", p),
    upload: (p, form) => call("POST", p, form, true),
    token: get, setToken: (v) => (v ? localStorage.setItem(tokenKey, v) : localStorage.removeItem(tokenKey)),
  };
}

// --- время в часовом поясе города (расписание и брони считаются по нему) ---
function parts(ts, tz) {
  const f = new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return Object.fromEntries(f.formatToParts(new Date(ts)).map((p) => [p.type, p.value]));
}
const offsetMs = (ts, tz) => { const p = parts(ts, tz); return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) - Math.floor(ts / 1000) * 1000; };
/** "2026-12-01T09:30" в часовом поясе tz -> Date */
export function zonedToDate(s, tz) {
  const [d, tm] = s.split("T"); const [Y, M, D] = d.split("-").map(Number); const [h, m] = tm.split(":").map(Number);
  const naive = Date.UTC(Y, M - 1, D, h, m);
  let ts = naive - offsetMs(naive, tz);
  ts = naive - offsetMs(ts, tz);
  return new Date(ts);
}
export function dateToZoned(date, tz) { const p = parts(date.getTime(), tz); return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`; }
export const isoUtc = (d) => d.toISOString().replace(/\.\d{3}Z$/, "Z");
export function fmtDT(iso, tz) {
  return new Date(iso).toLocaleString("ru-RU", { timeZone: tz, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
export function nextSlot(tz, plusMin = 0) {
  const d = new Date(Date.now() + 60_000 + plusMin * 60_000);
  const z = dateToZoned(d, tz); const [date, tm] = z.split("T"); let [h, m] = tm.split(":").map(Number);
  m = m <= 30 ? (m === 0 ? 0 : 30) : 60; if (m === 60) { h += 1; m = 0; }
  const local = zonedToDate(`${date}T00:00`, tz).getTime() + (h * 60 + m) * 60_000;
  return new Date(local);
}
export const toast = (msg) => {
  const el = document.createElement("div"); el.className = "toast"; el.textContent = msg; document.body.append(el); setTimeout(() => el.remove(), 3500);
};
export function sheet(html, { onClose } = {}) {
  const wrap = document.createElement("div"); wrap.className = "sheet-wrap";
  wrap.innerHTML = `<div class="sheet-bg"></div><div class="sheet" role="dialog">${html}</div>`;
  const close = () => { wrap.remove(); onClose && onClose(); };
  wrap.querySelector(".sheet-bg").onclick = close;
  document.body.append(wrap);
  return { el: wrap.querySelector(".sheet"), close };
}
