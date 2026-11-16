import { t, esc, $, money, loadI18n, makeApi, toast, fmtDT } from "./core.js";

const api = makeApi("pc_staff_token");
const root = $("#root");
const TZ = "Europe/Moscow";
let me = null, tab = null;
const TABS = [  // вкладка -> нужное право
  ["places", "moderate"], ["bookings", "all_bookings"], ["complaints", "complaints"], ["payments", "refunds"],
  ["users", "users"], ["stats", "stats"], ["employees", "staff"], ["log", "staff"],
];

async function boot() {
  await loadI18n();
  document.title = t("admin.title");
  $("#mobile-stop").textContent = t("admin.desktop_only");
  me = api.token() ? await api.get("/staff/me").catch(() => null) : null;
  me ? shell() : login();
}

function login() {
  root.innerHTML = `<form class="login card" id="f"><h2>${t("admin.title")}</h2>
    <label>${t("admin.email")}<input name="email" type="email" required autocomplete="username"></label>
    <label>${t("admin.password")}<input name="password" type="password" required autocomplete="current-password"></label>
    <label>${t("admin.code")}<input name="code" inputmode="numeric" maxlength="6" required autocomplete="one-time-code"></label>
    <button class="btn">${t("admin.login")}</button></form>`;
  $("#f").onsubmit = async (e) => {
    e.preventDefault();
    try { const r = await api.post("/staff/login", Object.fromEntries(new FormData(e.target))); api.setToken(r.token); me = r.staff; shell(); } catch (err) { toast(err.message); }
  };
}

function shell() {
  const mine = TABS.filter(([, p]) => me.permissions.includes(p));
  tab = tab && mine.some(([n]) => n === tab) ? tab : mine[0]?.[0];
  root.innerHTML = `<div class="layout"><aside class="nav"><b>ParkCity</b><p class="muted small">${esc(me.name)} · ${t("role." + me.role)}</p>
    ${mine.map(([n]) => `<button data-t="${n}" class="${n === tab ? "on" : ""}">${t("admin.tab." + n)}</button>`).join("")}<hr><button id="out">${t("admin.logout")}</button></aside><section class="content" id="c"></section></div>`;
  root.querySelectorAll("[data-t]").forEach((b) => (b.onclick = () => { tab = b.dataset.t; shell(); }));
  $("#out").onclick = () => { api.setToken(null); me = null; login(); };
  if (tab) views[tab]($("#c")).catch(fail);
}
function fail(e) { if (e.status === 401) { api.setToken(null); return login(); } toast(e.message); }
const empty = () => `<p class="muted">${t("admin.empty")}</p>`;
const table = (heads, rows) => `<table class="t"><tr>${heads.map((h) => `<th>${h}</th>`).join("")}</tr>${rows.join("")}</table>`;
const act = (fn) => (e) => { Promise.resolve(fn(e)).then(() => shell()).catch(fail); };

const views = {
  async places(el) {
    const st = sessionStorage.pcPlaceStatus || "pending";
    const list = await api.get(`/staff/places?status=${st}`);
    el.innerHTML = `<h2>${t("admin.tab.places")}</h2><div class="tools"><select id="st">${["pending", "approved", "rejected", "blocked"].map((s) => `<option ${s === st ? "selected" : ""}>${s}</option>`).join("")}</select></div>` +
      (list.length ? table(["#", "Адрес", "Цена", "Описание", "Фото", ""], list.map((p) => `<tr><td>${p.id}</td><td>${esc(p.address)}<br><small class="muted">${esc(p.type)}, ${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}</small></td><td>${money(p.price_per_hour_kop)}</td>
        <td>${esc(p.description || "")}<br><small>${esc(p.entry_rules || "")}</small></td><td>${p.photos.map((u) => `<a href="${esc(u)}" target="_blank"><img src="${esc(u)}" height="48"></a>`).join(" ")}</td>
        <td>${p.moderation_status !== "approved" ? `<button class="btn s" data-a="approve" data-id="${p.id}">${t("admin.approve")}</button>` : ""}
        ${p.moderation_status !== "rejected" ? `<button class="btn s ghost" data-a="reject" data-id="${p.id}">${t("admin.reject")}</button>` : ""}
        ${p.moderation_status !== "blocked" ? `<button class="btn s danger" data-a="block" data-id="${p.id}">${t("admin.block")}</button>` : ""}</td></tr>`)) : empty());
    $("#st").onchange = (e) => { sessionStorage.pcPlaceStatus = e.target.value; shell(); };
    el.querySelectorAll("[data-a]").forEach((b) => (b.onclick = act(async () => {
      const a = b.dataset.a; const reason = a === "approve" ? "" : prompt(t("admin.reason"));
      if (a !== "approve" && !reason) throw new Error(t("admin.reason"));
      await api.post(`/staff/places/${b.dataset.id}/${a}`, { reason });
    })));
  },
  async bookings(el) {
    const q = sessionStorage.pcQ || "";
    const list = await api.get(`/staff/bookings?q=${encodeURIComponent(q)}`);
    const canRefund = me.permissions.includes("refunds");
    el.innerHTML = `<h2>${t("admin.tab.bookings")}</h2><div class="tools"><input id="q" placeholder="${t("admin.search")}" value="${esc(q)}"></div>` +
      (list.length ? table(["#", "Адрес", "Время", "Статус", "Оплачено", "Возвращено", "Водитель", ""], list.map((b) => `<tr><td>${b.id}</td><td>${esc(b.address)}</td><td>${fmtDT(b.start, TZ)} – ${fmtDT(b.end, TZ)}</td>
        <td>${t("bstatus." + b.status)}</td><td>${money(b.paid_kop)}</td><td>${money(b.refunded_kop)}</td><td>${esc(b.driver?.phone || b.driver?.name || "")}</td>
        <td>${canRefund && b.paid_kop - b.refunded_kop > 0 ? `<button class="btn s ghost" data-r="${b.id}" data-max="${b.paid_kop - b.refunded_kop}">${t("admin.refund")}</button>` : ""}</td></tr>`)) : empty());
    $("#q").onkeydown = (e) => { if (e.key === "Enter") { sessionStorage.pcQ = e.target.value; shell(); } };
    el.querySelectorAll("[data-r]").forEach((b) => (b.onclick = act(async () => {
      const sum = prompt(`${t("admin.refund_amount")} (≤ ${b.dataset.max / 100})`); if (!sum) throw new Error("—");
      const reason = prompt(t("admin.reason")); if (!reason) throw new Error(t("admin.reason"));
      await api.post(`/staff/bookings/${b.dataset.r}/refund`, { amount_kop: Math.round(parseFloat(sum.replace(",", ".")) * 100), reason });
    })));
  },
  async complaints(el) {
    const list = await api.get("/staff/complaints?status=open");
    el.innerHTML = `<h2>${t("admin.tab.complaints")}</h2>` + (list.length ? table(["#", "Бронь", "Место", "Тип", "Описание", ""], list.map((c) => `<tr><td>${c.id}</td><td>${c.booking_id}</td><td>${esc(c.address)}</td><td>${t("complaint." + c.kind)}</td><td>${esc(c.description)}</td>
      <td><button class="btn s" data-c="${c.id}" data-s="resolved">${t("admin.resolve")}</button><button class="btn s ghost" data-c="${c.id}" data-s="rejected">${t("admin.dismiss")}</button></td></tr>`)) : empty());
    el.querySelectorAll("[data-c]").forEach((b) => (b.onclick = act(async () => {
      const resolution = prompt(t("admin.resolution")); if (!resolution) throw new Error(t("admin.resolution"));
      await api.post(`/staff/complaints/${b.dataset.c}/resolve`, { status: b.dataset.s, resolution });
    })));
  },
  async payments(el) {
    const d = await api.get("/staff/payments");
    el.innerHTML = `<h2>${t("admin.tab.payments")}</h2><h3>Оплаты</h3>` + (d.payments.length ? table(["#", "Бронь", "Сумма", "Статус", "Способ", "Оплачено"], d.payments.map((p) => `<tr><td>${p.id}</td><td>${p.booking_id}</td><td>${money(p.amount_kop)}</td><td>${p.status}</td><td>${esc(p.method || "")}</td><td>${p.paid_at ? fmtDT(p.paid_at, TZ) : ""}</td></tr>`)) : empty()) +
      `<h3>Возвраты</h3>` + (d.refunds.length ? table(["#", "Бронь", "Сумма", "%", "Вручную", "Причина"], d.refunds.map((r) => `<tr><td>${r.id}</td><td>${r.booking_id}</td><td>${money(r.amount_kop)}</td><td>${r.percent ?? ""}</td><td>${r.is_manual ? "да" : ""}</td><td>${esc(r.reason || "")}</td></tr>`)) : empty()) +
      `<h3>${t("owner.payouts")}</h3>` + (d.payouts.length ? table(["#", "Владелец", "Период до", "Сумма", "Комиссия"], d.payouts.map((p) => `<tr><td>${p.id}</td><td>${p.owner_id}</td><td>${fmtDT(p.period_end, TZ)}</td><td>${money(p.amount_kop)}</td><td>${money(p.commission_kop)}</td></tr>`)) : empty());
  },
  async users(el) {
    const q = sessionStorage.pcU || "";
    const list = await api.get(`/staff/users?q=${encodeURIComponent(q)}`);
    el.innerHTML = `<h2>${t("admin.tab.users")}</h2><div class="tools"><input id="q" placeholder="телефон, почта, имя" value="${esc(q)}"></div>` +
      table(["#", "Имя", "Контакт", "Статус", ""], list.map((u) => `<tr><td>${u.id}</td><td>${esc(u.name || "")}</td><td>${esc(u.phone || u.email || "")}</td><td>${u.is_blocked ? "🚫" : ""}</td>
      <td><button class="btn s ${u.is_blocked ? "" : "danger"}" data-u="${u.id}" data-a="${u.is_blocked ? "unblock" : "block"}">${u.is_blocked ? t("admin.unblock") : t("admin.block")}</button></td></tr>`));
    $("#q").onkeydown = (e) => { if (e.key === "Enter") { sessionStorage.pcU = e.target.value; shell(); } };
    el.querySelectorAll("[data-u]").forEach((b) => (b.onclick = act(async () => {
      const reason = b.dataset.a === "block" ? prompt(t("admin.reason")) : "";
      if (b.dataset.a === "block" && !reason) throw new Error(t("admin.reason"));
      await api.post(`/staff/users/${b.dataset.u}/${b.dataset.a}`, { reason });
    })));
  },
  async stats(el) {
    const s = await api.get("/staff/stats");
    const k = (l, v) => `<div class="kpi">${l}<b>${v}</b></div>`;
    el.innerHTML = `<h2>${t("admin.tab.stats")}</h2><div class="kpis">${k(t("stat.users"), s.users)}${k(t("stat.places_approved"), s.places_approved)}${k(t("stat.places_pending"), s.places_pending)}
      ${k(t("stat.bookings_total"), s.bookings_total)}${k(t("stat.revenue"), money(s.revenue_gross_kop))}${k(t("stat.commission"), money(s.commission_kop))}${k(t("stat.occupancy"), s.occupancy_30d_percent + " %")}</div>`;
  },
  async employees(el) {
    const list = await api.get("/staff/employees");
    el.innerHTML = `<h2>${t("admin.tab.employees")}</h2>` + table(["#", t("admin.name"), t("admin.email"), t("admin.role"), ""], list.map((s) => `<tr><td>${s.id}</td><td>${esc(s.name)}</td><td>${esc(s.email)}</td>
      <td><select data-role="${s.id}">${["admin", "manager", "support", "finance", "marketer"].map((r) => `<option value="${r}" ${r === s.role ? "selected" : ""}>${t("role." + r)}</option>`).join("")}</select></td>
      <td><button class="btn s ghost" data-act="${s.id}" data-on="${s.is_active ? 0 : 1}">${s.is_active ? t("admin.deactivate") : t("admin.activate")}</button></td></tr>`)) +
      `<h3>${t("admin.add_employee")}</h3><form id="nf" class="tools"><input name="name" placeholder="${t("admin.name")}" required><input name="email" type="email" placeholder="${t("admin.email")}" required>
      <select name="role">${["manager", "support", "finance", "marketer", "admin"].map((r) => `<option value="${r}">${t("role." + r)}</option>`).join("")}</select><button class="btn s">＋</button></form>`;
    el.querySelectorAll("[data-role]").forEach((s) => (s.onchange = act(() => api.patch(`/staff/employees/${s.dataset.role}`, { role: s.value }))));
    el.querySelectorAll("[data-act]").forEach((b) => (b.onclick = act(() => api.patch(`/staff/employees/${b.dataset.act}`, { is_active: b.dataset.on === "1" }))));
    $("#nf").onsubmit = async (e) => {
      e.preventDefault();
      try { const r = await api.post("/staff/employees", Object.fromEntries(new FormData(e.target))); alert(`${t("admin.created")}\n\n${t("admin.password")}: ${r.password}\n${t("admin.secret")}: ${r.totp_secret}`); shell(); } catch (err) { toast(err.message); }
    };
  },
  async log(el) {
    const list = await api.get("/staff/log");
    el.innerHTML = `<h2>${t("admin.tab.log")}</h2>` + (list.length ? table(["Время", "Сотрудник", "Действие", "Объект", "Детали"], list.map((l) => `<tr><td>${fmtDT(l.created_at, TZ)}</td><td>${esc(l.staff_name || "")}</td><td>${esc(l.action)}</td><td>${esc(l.target || "")}</td><td>${esc(l.details || "")}</td></tr>`)) : empty());
  },
};
boot();
