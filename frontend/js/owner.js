// Кабинет владельца: места, добавление по шагам, брони, доходы.
import { t, esc, $, money, toast, sheet, fmtDT } from "./core.js";

export async function owner(c, sub) {
  const { api, S, screen } = c;
  if (!(await c.login().catch(() => null))) return (location.hash = "#/profile");
  const nav = (on) => `<div class="pad"><a class="link" href="#/profile">← ${t("common.back")}</a><h2>${t("owner.title")}</h2>
    <div class="seg"><a class="${on === "places" ? "on" : ""}" href="#/owner">${t("owner.places")}</a><a class="${on === "bookings" ? "on" : ""}" href="#/owner/bookings">${t("owner.bookings")}</a><a class="${on === "earnings" ? "on" : ""}" href="#/owner/earnings">${t("owner.earnings")}</a></div>`;

  if (sub === "/add") return wizard(c);
  if (sub === "/bookings") {
    const list = await api.get("/owner/bookings");
    screen.innerHTML = nav("bookings") + (list.length ? list.map((b) => `<div class="card"><div class="row between"><b>${esc(b.address)}</b><span class="badge b-${b.status}">${t("bstatus." + b.status)}</span></div>
      <div class="muted">${fmtDT(b.start, S.tz)} – ${fmtDT(b.end, S.tz)} · ${money(b.total_price_kop)}</div>
      ${b.driver ? `<div class="muted small">${t("booking.driver")}: ${esc(b.driver.name || b.driver.phone || "")}</div>` : ""}
      ${["confirmed", "active"].includes(b.status) ? `<button class="btn ghost small" data-cancel="${b.id}">${t("owner.cancel_booking")}</button>` : ""}</div>`).join("") : `<p class="muted">${t("bookings.empty")}</p>`) + "</div>";
    screen.querySelectorAll("[data-cancel]").forEach((btn) => (btn.onclick = () => {
      const reason = prompt(t("owner.cancel_reason"));
      if (reason) api.post(`/owner/bookings/${btn.dataset.cancel}/cancel`, { reason }).then(c.rerender).catch((e) => toast(e.message));
    }));
    return;
  }
  if (sub === "/earnings") {
    const e = await api.get("/owner/earnings");
    screen.innerHTML = nav("earnings") + `<div class="card"><div class="row between"><span>${t("owner.gross")}</span><b>${money(e.accrued_gross_kop)}</b></div>
      <div class="row between"><span>${t("owner.commission")}</span><b>−${money(e.accrued_commission_kop)}</b></div>
      <div class="row between"><span>${t("owner.accrued")}</span><b>${money(e.accrued_net_kop)}</b></div>
      <p class="muted small">${t("owner.payouts_hint", { pct: c.S.cfg.rules.commission_percent })}</p></div>
      <label>${t("owner.payout_details")}<textarea id="pd">${esc(e.payout_details || "")}</textarea></label><button class="btn" id="pd-save">${t("common.save")}</button>
      <h3>${t("owner.payouts")}</h3>${e.payouts.map((p) => `<div class="card row between"><span>${new Date(p.period_end).toLocaleDateString("ru-RU")}</span><b>${money(p.amount_kop)}</b></div>`).join("")}</div>`;
    $("#pd-save").onclick = () => api.patch("/me", { payout_details: $("#pd").value }).then(() => toast(t("profile.saved")));
    return;
  }
  const places = await api.get("/owner/places");
  screen.innerHTML = nav("places") + `<a class="btn" href="#/owner/add">＋ ${t("owner.add")}</a>` +
    (places.length ? places.map((p) => `<div class="card"><div class="row between"><b>${esc(p.address)}</b><span class="badge m-${p.moderation_status}">${modLabel(p)}</span></div>
      <div class="muted">${money(p.price_per_hour_kop)}/${t("common.hour")}${p.is_closed ? " · " + t("owner.closed") : ""}</div>
      <div class="row gap"><button class="btn ghost small" data-edit="${p.id}">${t("owner.edit")}</button>
      <button class="btn ghost small" data-toggle="${p.id}" data-closed="${p.is_closed ? 1 : 0}">${p.is_closed ? t("owner.open") : t("owner.close")}</button></div></div>`).join("") : `<p class="muted">${t("owner.no_places")}</p>`) + "</div>";
  screen.querySelectorAll("[data-toggle]").forEach((b) => (b.onclick = async () => {
    const id = b.dataset.toggle;
    if (b.dataset.closed === "1") await api.post(`/owner/places/${id}/open`);
    else { const reason = prompt(t("owner.close_reason")); if (reason === null) return; await api.post(`/owner/places/${id}/close`, { reason }); }
    c.rerender();
  }));
  screen.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => editSheet(c, places.find((p) => p.id == b.dataset.edit))));
}
const modLabel = (p) => t("owner.mod." + p.moderation_status, { reason: esc(p.reject_reason || "") });

// ---- расписание: по строке на день недели ----
const scheduleEditor = (sched = {}) => `<div id="sch">${[0, 1, 2, 3, 4, 5, 6].map((d) => {
  const w = (sched[d] || [])[0] || ["08:00", "20:00"];
  return `<div class="row gap sch-row" data-d="${d}"><label class="row"><input type="checkbox" ${sched[d] ? "checked" : ""}> ${t("day." + d)}</label>
    <input type="time" value="${w[0]}"> <span>–</span> <input type="time" value="${w[1] === "24:00" ? "23:59" : w[1]}"></div>`;
}).join("")}</div>`;
function readSchedule(root) {
  const out = {};
  root.querySelectorAll(".sch-row").forEach((r) => { if ($("input[type=checkbox]", r).checked) { const [a, b] = r.querySelectorAll("input[type=time]"); out[r.dataset.d] = [[a.value, b.value === "23:59" ? "24:00" : b.value]]; } });
  return out;
}

function editSheet(c, p) {
  const sh = sheet(`<h3>${esc(p.address)}</h3>
    <label>${t("wizard.price")}<input id="e-price" type="number" min="1" value="${p.price_per_hour_kop / 100}"></label>
    <label>${t("wizard.description")}<textarea id="e-desc">${esc(p.description || "")}</textarea></label>
    <label>${t("wizard.entry")}<textarea id="e-rules">${esc(p.entry_rules || "")}</textarea></label>${scheduleEditor(p.schedule)}
    <button class="btn" id="e-ok">${t("common.save")}</button>`);
  $("#e-ok", sh.el).onclick = async () => {
    try {
      await c.api.patch(`/owner/places/${p.id}`, { price_per_hour_kop: Math.round($("#e-price", sh.el).value * 100), description: $("#e-desc", sh.el).value, entry_rules: $("#e-rules", sh.el).value, schedule: readSchedule(sh.el) });
      sh.close(); c.rerender();
    } catch (e) { toast(e.message); }
  };
}

// ---- добавление места по шагам: адрес -> описание и фото -> цена и расписание ----
async function wizard(c) {
  const { api, S, screen } = c;
  const d = { address: "", district: "", lat: null, lon: null, type_id: S.cfg.parking_types[0].id, description: "", entry_rules: "", price: "", files: [] };
  let step = 1, map = null;
  const head = () => `<div class="pad"><a class="link" href="#/owner">← ${t("common.back")}</a><h2>${t("owner.add")}</h2>
    <div class="steps">${[1, 2, 3].map((i) => `<span class="${i === step ? "on" : i < step ? "done" : ""}">${i}. ${t("wizard.step" + i)}</span>`).join("")}</div>`;
  async function render() {
    if (step === 1) {
      screen.innerHTML = head() + `<label>${t("wizard.address")}<input id="w-addr" value="${esc(d.address)}"></label><label>${t("wizard.district")}<input id="w-dist" value="${esc(d.district)}"></label>
        <p class="muted" id="w-hint">${d.lat ? t("wizard.point_set") : t("wizard.pick")}</p><div id="pick" style="height:280px;border-radius:12px"></div><button class="btn" id="w-next">${t("common.next")}</button></div>`;
      const c0 = S.cfg.cities[0];
      map = await c.mapFactory($("#pick"), d.lat ? [d.lat, d.lon] : [c0.lat, c0.lon], { onTap: ([la, lo]) => { d.lat = la; d.lon = lo; map.setPin([la, lo]); $("#w-hint").textContent = t("wizard.point_set"); } });
      if (d.lat) map.setPin([d.lat, d.lon]);
      $("#w-next").onclick = () => {
        d.address = $("#w-addr").value.trim(); d.district = $("#w-dist").value.trim();
        if (!d.address) return toast(t("wizard.address")); if (d.lat == null) return toast(t("wizard.need_point"));
        step = 2; render();
      };
    } else if (step === 2) {
      screen.innerHTML = head() + `<label>${t("wizard.type")}<select id="w-type">${S.cfg.parking_types.map((x) => `<option value="${x.id}" ${x.id == d.type_id ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></label>
        <label>${t("wizard.description")}<textarea id="w-desc">${esc(d.description)}</textarea></label><label>${t("wizard.entry")}<textarea id="w-rules">${esc(d.entry_rules)}</textarea></label>
        <label>${t("wizard.photos")}<input id="w-files" type="file" accept="image/jpeg,image/png,image/webp" multiple></label>
        <div class="row gap"><button class="btn ghost" id="w-back">${t("common.back")}</button><button class="btn" id="w-next">${t("common.next")}</button></div></div>`;
      const keep = () => { d.type_id = +$("#w-type").value; d.description = $("#w-desc").value; d.entry_rules = $("#w-rules").value; d.files = [...$("#w-files").files].length ? [...$("#w-files").files] : d.files; };
      $("#w-back").onclick = () => { keep(); step = 1; render(); }; $("#w-next").onclick = () => { keep(); step = 3; render(); };
    } else {
      screen.innerHTML = head() + `<label>${t("wizard.price")}<input id="w-price" type="number" min="1" value="${esc(d.price)}"></label>${scheduleEditor(d.schedule)}
        <div class="row gap"><button class="btn ghost" id="w-back">${t("common.back")}</button><button class="btn" id="w-send">${t("wizard.submit")}</button></div></div>`;
      $("#w-back").onclick = () => { d.price = $("#w-price").value; d.schedule = readSchedule(screen); step = 2; render(); };
      $("#w-send").onclick = async () => {
        try {
          const p = await api.post("/owner/places", { address: d.address, district: d.district, lat: d.lat, lon: d.lon, type_id: d.type_id, description: d.description,
            entry_rules: d.entry_rules, price_per_hour_kop: Math.round($("#w-price").value * 100), schedule: readSchedule(screen) });
          for (const f of d.files) { const fd = new FormData(); fd.append("photo", f); await api.upload(`/owner/places/${p.id}/photos`, fd); }
          toast(t("wizard.saved")); c.go("#/owner");
        } catch (e) { toast(e.message); }
      };
    }
  }
  render();
}
