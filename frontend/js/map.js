// Адаптер карты: Яндекс Карты (если задан ключ), иначе Leaflet + OpenStreetMap.
import { cfg } from "./core.js";

const COLORS = { free: "#1f9d55", booked: "#d99a00", occupied: "#d64545", unavailable: "#8a94a0" };
const GLYPH = { free: "✓", booked: "◷", occupied: "✕", unavailable: "⏸" };   // статус виден и по цвету, и по значку
const YPRESET = { free: "green", booked: "yellow", occupied: "red", unavailable: "gray" };

function loadScript(src) { return new Promise((ok, no) => { const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = no; document.head.append(s); }); }
function loadCss(href) { const l = document.createElement("link"); l.rel = "stylesheet"; l.href = href; document.head.append(l); }

export async function createMap(el, center, { onTap } = {}) {
  if (cfg.YANDEX_MAPS_API_KEY) {
    await loadScript(`https://api-maps.yandex.ru/2.1/?apikey=${encodeURIComponent(cfg.YANDEX_MAPS_API_KEY)}&lang=ru_RU`);
    await new Promise((r) => ymaps.ready(r));
    const map = new ymaps.Map(el, { center, zoom: 15, controls: ["zoomControl"] });
    let marks = [], me = null;
    if (onTap) map.events.add("click", (e) => onTap(e.get("coords")));
    return {
      setCenter: (c, z) => map.setCenter(c, z || map.getZoom()),
      getCenter: () => map.getCenter(),
      setMarkers(places, onClick) {
        marks.forEach((m) => map.geoObjects.remove(m)); marks = [];
        places.forEach((p) => {
          const m = new ymaps.Placemark([p.lat, p.lon], { iconContent: `${GLYPH[p.status]} ${Math.round(p.price_per_hour_kop / 100)}₽` },
            { preset: `islands#${YPRESET[p.status]}StretchyIcon` });
          m.events.add("click", () => onClick(p)); map.geoObjects.add(m); marks.push(m);
        });
      },
      setMe(c) { if (me) map.geoObjects.remove(me); me = new ymaps.Placemark(c, {}, { preset: "islands#blueCircleDotIcon" }); map.geoObjects.add(me); },
      setPin(c) { if (this._pin) map.geoObjects.remove(this._pin); this._pin = new ymaps.Placemark(c); map.geoObjects.add(this._pin); },
    };
  }
  loadCss("https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css");
  await loadScript("https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js");
  const map = L.map(el, { zoomControl: false }).setView(center, 15);
  L.control.zoom({ position: "bottomright" }).addTo(map);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(map);
  let layer = L.layerGroup().addTo(map), me = null, pin = null;
  if (onTap) map.on("click", (e) => onTap([e.latlng.lat, e.latlng.lng]));
  return {
    setCenter: (c, z) => map.setView(c, z || map.getZoom()),
    getCenter: () => { const c = map.getCenter(); return [c.lat, c.lng]; },
    setMarkers(places, onClick) {
      layer.clearLayers();
      places.forEach((p) => {
        const icon = L.divIcon({ className: "", iconSize: null,
          html: `<div class="pin" style="background:${COLORS[p.status]}">${GLYPH[p.status]} ${Math.round(p.price_per_hour_kop / 100)}₽</div>` });
        L.marker([p.lat, p.lon], { icon }).on("click", () => onClick(p)).addTo(layer);
      });
    },
    setMe(c) { me && me.remove(); me = L.circleMarker(c, { radius: 8, color: "#fff", weight: 3, fillColor: "#2563eb", fillOpacity: 1 }).addTo(map); },
    setPin(c) { pin && pin.remove(); pin = L.marker(c).addTo(map); },
  };
}
