/* RePlate: Leaflet map for verified donation destinations in the Next steps sheet.
   OpenStreetMap supplies geography only; it is never treated as acceptance evidence. */
import * as L from "leaflet";
import "leaflet/dist/leaflet.css";

const escHtml = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hasPoint = o => o && Number.isFinite(o.latitude) && Number.isFinite(o.longitude);

/* Returns a controller { select(id, pan), destroy() } or null when nothing can be plotted. */
export function createDonationMap(container, destinations, origin, { onSelect, onTilesUnavailable } = {}) {
  if (!container || container._replateMap) return container?._replateMap || null;
  const plotted = (destinations || []).filter(hasPoint);
  if (!plotted.length && !hasPoint(origin)) return null;
  const map = L.map(container, { scrollWheelZoom: false, zoomControl: true, keyboard: true });
  const tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>' });
  let tileWarned = false;
  tiles.on("tileerror", () => { if (!tileWarned) { tileWarned = true; onTilesUnavailable?.(); } });
  tiles.addTo(map);
  const markers = new Map(), points = [];
  plotted.forEach((o, i) => {
    const label = `${o.name}${Number.isFinite(o.drive_minutes) ? `, ${o.drive_minutes} minutes by car` : ""}`;
    const icon = L.divIcon({ className: "rp-pin-wrap", html: `<span class="rp-pin"><span>${i + 1}</span></span>`, iconSize: [30, 38], iconAnchor: [15, 36], popupAnchor: [0, -32] });
    const m = L.marker([o.latitude, o.longitude], { icon, title: label, alt: label, keyboard: true, riseOnHover: true }).addTo(map)
      .bindPopup(`<strong>${escHtml(o.name)}</strong><br>${escHtml(o.address || o.location || "")}${Number.isFinite(o.drive_minutes) ? `<br>${escHtml(o.drive_minutes)} min · ${escHtml(o.drive_miles)} mi by car` : ""}`);
    m.on("click", () => onSelect?.(o.id, "map"));
    const el = m.getElement();
    if (el) {
      el.setAttribute("role", "button"); el.setAttribute("aria-label", `Select ${label}`);
      el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect?.(o.id, "map"); m.openPopup(); } });
    }
    markers.set(String(o.id), m); points.push([o.latitude, o.longitude]);
  });
  if (hasPoint(origin)) {
    L.circleMarker([origin.latitude, origin.longitude], { radius: 7, color: "#315F45", fillColor: "#FBFCF8", fillOpacity: 1, weight: 3 })
      .addTo(map).bindPopup(`Search area: ${escHtml(origin.label || "")}`);
    points.push([origin.latitude, origin.longitude]);
  }
  if (points.length === 1) map.setView(points[0], 12); else map.fitBounds(points, { padding: [28, 28], maxZoom: 13 });
  let alive = true;
  const ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => { if (alive) map.invalidateSize(); }) : null;
  ro?.observe(container);
  setTimeout(() => { if (alive) map.invalidateSize(); }, 60);
  const ctrl = {
    select(id, pan = true) {
      if (!alive) return;
      markers.forEach((m, k) => m.getElement()?.classList.toggle("selected", k === String(id)));
      const m = markers.get(String(id));
      if (m && pan) { map.panTo(m.getLatLng()); m.openPopup(); }
    },
    destroy() {
      if (!alive) return; alive = false; ro?.disconnect();
      try { map.remove(); } catch {}
      markers.clear(); delete container._replateMap;
    }
  };
  container._replateMap = ctrl;
  return ctrl;
}
