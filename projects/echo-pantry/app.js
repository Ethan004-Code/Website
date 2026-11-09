import * as L from "leaflet";
import "leaflet/dist/leaflet.css";
import { foldName, parseQty, qtyError, pickupLimits, printedLimits, windowErrors, printedCheck, ITEM_PLACEHOLDER, CANON_UNIT, QTY_MIN, QTY_MAX, toLocalInput as localStr, SPOT_MAX, SPOT_PLACEHOLDER, SPOT_NOTE, spotError } from "./limits.js";
import { parseAllergens, allergenValue, toggleAllergen, allergenError, allergenPayload, allergenMarkup } from "./allergens.js";
import "./allergens.css";

const API = "/api";
const QTY_ATTRS = `type="number" min="${QTY_MIN}" max="${QTY_MAX}" step="1" inputmode="numeric" pattern="[0-9]*"`;
function setQtyAttrs(el) { Object.assign(el, {type:"number", min:String(QTY_MIN), max:String(QTY_MAX), step:"1", inputMode:"numeric"}); el.setAttribute("pattern", "[0-9]*"); }
function setRange(el, lo, hi) { if (el) { el.min = lo; el.max = hi; } }
function applyPickupLimits(root) { const lim = pickupLimits(); root.querySelectorAll('input[type="datetime-local"]').forEach(el => setRange(el, lim.minInput, lim.maxInput)); }
const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const $ = s => document.querySelector(s);
const listingsEl = $("#listings"), form = $("#listing-form"), notice = $("#form-notice"), offerArea = $("#offer-area"), toast = $("#toast");
let listings = [], myListings = [], myOffers = [], myRequests = [], sessionUser = null, authMode = "login", toastTimer, loaded = false;
let donationMap = null, donationMarkers = new Map();
const inedible = new Set();

function esc(v) { return String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
async function api(path, options = {}) {
  let res;
  try { res = await fetch(`${API}${path}`, {credentials:"include", headers:{"Content-Type":"application/json"}, ...options}); }
  catch (er) { if (er?.name === "AbortError") { const e = Error("The request timed out."); e.timeout = true; throw e; } throw Error("Cannot reach the RePlate server. Check your connection and try again."); }
  let data = {}; try { data = await res.json(); } catch {}
  if (!res.ok) { const e = Error(data.error || `Request failed (${res.status}).`); e.status = res.status; e.data = data; throw e; }
  return data;
}
const post = (p, body = {}) => api(p, {method:"POST", body:JSON.stringify(body)});
function showToast(m) { toast.textContent = m; toast.classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.classList.remove("show"), 3200); }
function toUtcIso(v) { if (!v) return null; const d = new Date(v); return Number.isNaN(d.getTime()) ? null : d.toISOString(); }
function fmt(v) { const d = new Date(v); return !v || Number.isNaN(d.getTime()) ? "Not set" : d.toLocaleString(undefined,{weekday:"short",month:"short",day:"numeric",hour:"numeric",minute:"2-digit"}); }
function windowText(a, b) { return a && b ? `${fmt(a)} to ${fmt(b)}` : "No window set"; }
function itemsOf(l) { if (Array.isArray(l?.item_details) && l.item_details.length) return l.item_details; if (Array.isArray(l?.items)) return l.items.map(x => typeof x === "string" ? {name:x, quantity:l.quantities?.[x] ?? null, unit:null} : x); return []; }
function itemText(i) { if (typeof i === "string") return esc(i); const q = i.quantity != null ? `${i.quantity} ` : ""; return esc(q + foldName(i.name, i.unit, i.quantity)) + (i.printed_date ? ` <small>(${esc(i.date_type || "date")}: ${esc(i.printed_date)})</small>` : ""); }
function itemList(arr) { return (arr || []).map(itemText).join(", ") || "None listed"; }
function confirmSwapAmounts(give, get, location, start, end) {
  if (![give, get].every(arr => Array.isArray(arr) && arr.length && arr.every(i => i && typeof i === "object" && i.quantity != null))) {
    showToast("The full swap amounts aren't available. Refresh and try again.");
    return false;
  }
  const amounts = arr => arr.map(i => `${i.quantity} ${foldName(i.name, i.unit, i.quantity)}`).join(", ");
  return confirm(`Please check the full amounts before agreeing to this swap:\n\nYou give: ${amounts(give)}\nYou get: ${amounts(get)}\nPickup: ${location} · ${windowText(start, end)}\n\nThe amounts do not need to be equal. Are you okay exchanging these full amounts? Nothing is reserved until both people accept.`);
}
function status(s) { return `<span class="status ${esc(s)}">${esc(String(s || "").replaceAll("_"," "))}</span>`; }
function isMine(listingId) { return myListings.some(l => l.id === listingId); }
function setNotice(el, msg, ok) { el.className = ok ? "notice success" : "notice"; el.textContent = msg; el.hidden = false; }

document.querySelectorAll("select[name=location]").forEach(select => {
  const label = select.closest("label"), input = document.createElement("input");
  input.name = "location"; input.type = "text"; input.maxLength = SPOT_MAX;
  input.placeholder = SPOT_PLACEHOLDER; input.required = true;
  input.readOnly = !!select.closest("#giveaway-form");
  select.replaceWith(input);
  label.firstChild.textContent = "Pickup spot";
  const note = document.createElement("small"), count = document.createElement("small");
  note.textContent = SPOT_NOTE;
  count.dataset.spotCount = ""; count.setAttribute("role", "status");
  label.append(note, count);
  input.addEventListener("input", () => { count.textContent = `${input.value.length}/${SPOT_MAX}`; });
  input.addEventListener("change", () => { count.textContent = `${input.value.length}/${SPOT_MAX}`; });
  input.form.addEventListener("reset", () => setTimeout(updateSpotCounters, 0));
  count.textContent = `0/${SPOT_MAX}`;
});
$("#location-filter")?.closest("label").remove();
function updateSpotCounters() {
  document.querySelectorAll("[data-spot-count]").forEach(counter => {
    counter.textContent = `${counter.closest("label").querySelector("input").value.length}/${SPOT_MAX}`;
  });
}

/* Health */
async function checkHealth() {
  const b = $("#setup-banner");
  try { const h = await api("/health"); const s = h.setup_required || [];
    b.hidden = !s.length; if (s.length) b.innerHTML = `<strong>Setup incomplete.</strong> Some features are unavailable:<ul>${s.map(x => `<li>${esc(x)}</li>`).join("")}</ul>`;
  } catch (e) { b.hidden = false; b.textContent = `Server health check failed: ${e.message}`; }
}

/* Listings */
function art(items) { const all = items.map(i => i.name || i).join(" ").toLowerCase(); const pkg = items.some(i => i.category === "shelf_stable"); return pkg ? `<span class="dot pkg"></span><span class="dot c"></span>` : /apple|tomato|pepper/.test(all) ? `<span class="dot b"></span><span class="dot"></span><span class="dot c"></span>` : `<span class="dot"></span><span class="dot c"></span>`; }
function renderListings() {
  const mode = $("#mode-filter").value, hours = Number($("#deadline-filter").value) || 0;
  const cutoff = hours ? Date.now() + hours * 3600000 : Infinity;
  const visible = listings.filter(x => x.status === "open" && (!mode || x.mode === mode) && new Date(x.deadline).getTime() <= cutoff);
  if (!visible.length) { listingsEl.innerHTML = `<div class="empty-listings">${loaded ? "No open listings match these filters. Be the first to share." : "Loading listings..."}</div>`; return; }
  listingsEl.innerHTML = visible.map(l => {
    const items = itemsOf(l), give = l.mode === "giveaway", owner = sessionUser && sessionUser.id === l.user_id;
    const wanted = l.wanted_food ? foldName(l.wanted_food, l.wanted_unit, l.wanted_quantity) : (l.wants || []).join(", ");
    return `<article class="listing-card"><div class="card-art ${give ? "" : "orange"}">${l.photo_id ? `<img class="card-photo" src="${API}/listing-photos/${encodeURIComponent(l.photo_id)}" alt="Photo of ${esc(items.map(i => i.name || i).join(", "))}" loading="lazy" onerror="this.remove()">` : ""}<span class="urgency ${give ? "gift-tag" : ""}">${give ? "Free giveaway" : "Exchange"}</span><span class="produce" aria-hidden="true">${art(items)}</span></div><div class="card-body"><div class="owner-row"><span class="owner"><span class="avatar" aria-hidden="true">${esc((l.name || "C").charAt(0).toUpperCase())}</span>${esc(l.name || "Community member")} &middot; ${esc(l.location)}</span><span class="rating">${l.rating ? Number(l.rating).toFixed(1) + " rating" : "New"}</span></div><div class="bundle-label">${items.length > 1 ? `BUNDLE &middot; ${items.length} ITEMS` : "SINGLE ITEM"}</div><div class="chips">${items.map(i => `<span class="chip">${itemText(i)}</span>`).join("")}</div>${!give && wanted ? `<div class="wants-label">HOPING TO FIND</div><div class="chips wants"><span class="chip">${esc(wanted)}</span></div>` : ""}<p class="deadline">Pickup ${esc(windowText(l.pickup_start, l.pickup_end))}</p>${l.allergens ? `<p class="deadline">Allergens: ${esc(l.allergens)}</p>` : ""}<div class="card-bottom"><span class="deadline">Until <strong>${esc(fmt(l.deadline))}</strong></span><button class="swap-button" data-act="${owner ? "manage" : give ? "giveaway" : "exchange"}" data-id="${esc(l.id)}">${owner ? "Next steps" : give ? "Request item" : "Match my listing"}</button></div>${!owner ? `<div class="trust-actions"><button data-report="${esc(l.user_id)}">Report</button><button data-block="${esc(l.user_id)}">Block user</button></div>` : ""}</div></article>`;
  }).join("");
}
listingsEl.addEventListener("click", async e => {
  const b = e.target.closest("button"); if (!b) return;
  if (!sessionUser) return openAuth();
  if (b.dataset.act === "manage") return openFallback(b.dataset.id);
  if (b.dataset.act === "giveaway") return openGiveawayDialog(b.dataset.id);
  if (b.dataset.act === "exchange") { const mine = myListings.filter(l => l.status === "open" && l.mode === "exchange"); if (!mine.length) { showToast("Create an exchange listing first; matches come from your own listing."); $("#create").scrollIntoView(); return; } showToast("Checking matches for your open exchange listings."); return openFallback(mine[0].id); }
  if (b.dataset.report) { const reason = prompt("Why are you reporting this user?"); if (!reason) return; try { await post(`/users/${encodeURIComponent(b.dataset.report)}/report`, {reason}); showToast("Report received."); } catch (er) { showToast(er.message); } }
  if (b.dataset.block) { if (!confirm("Block this user?")) return; try { await post(`/users/${encodeURIComponent(b.dataset.block)}/block`); showToast("User blocked."); await load(); } catch (er) { showToast(er.message); } }
});
document.querySelectorAll("#mode-filter,#deadline-filter").forEach(f => f.addEventListener("change", renderListings));

async function load() {
  try { const d = await api("/listings"); listings = d.listings || []; loaded = true; renderListings(); }
  catch (e) { listingsEl.innerHTML = `<div class="empty-listings">Could not load listings: ${esc(e.message)} <button class="mini-btn" id="retry-load">Retry</button></div>`; $("#retry-load").onclick = load; }
  refreshImpact();
  if (sessionUser) { refreshActivity(); refreshNotifications(); }
}

/* Impact */
let impactGeneration = 0;
async function refreshImpact() {
  const uid = sessionUser?.id, generation = ++impactGeneration, strip = $("#impact");
  $("#impact-detail").innerHTML = "";
  strip.setAttribute("aria-label", "Your impact");
  const title = `<div class="impact-title"><span class="impact-icon" aria-hidden="true"></span><div><strong>Your impact</strong><small>Your completed handoffs and reported drop-offs</small></div></div>`;
  if (!uid) {
    strip.innerHTML = title + `<p>Sign in to see your impact</p><button class="button primary" id="impact-signin" type="button">Sign in</button>`;
    $("#impact-signin").onclick = () => openAuth();
    return;
  }
  strip.innerHTML = title + `<p>Loading your impact…</p>`;
  try { const d = await api("/impact");
    if (sessionUser?.id !== uid || generation !== impactGeneration) return;
    strip.innerHTML = title + `<div><strong id="trades-done">0</strong><small>completed swaps</small></div><div><strong id="giveaways-done">0</strong><small>completed giveaways</small></div><div><strong id="donated-items">0</strong><small>donations you reported</small></div><div><strong id="compost-items">0</strong><small>compost drop-offs you reported</small></div>`;
    $("#trades-done").textContent = d.completed_trades ?? 0; $("#giveaways-done").textContent = d.completed_giveaways ?? 0;
    $("#donated-items").textContent = d.donations_reported ?? 0; $("#compost-items").textContent = d.compost_reported ?? 0;
    const q = d.quantities || [], food = q.filter(x => x.action !== "compost"), comp = q.filter(x => x.action === "compost");
    const li = arr => arr.map(x => `<li>${esc(x.quantity)} ${esc(foldName(x.name, x.unit, x.quantity))} <small>(${esc(String(x.action).replaceAll("_"," "))}${x.action === "donation" || x.action === "compost" ? ", user-reported" : ""})</small></li>`).join("");
    const empty = ![d.completed_trades, d.completed_giveaways, d.donations_reported, d.compost_reported].some(x => x > 0);
    $("#impact-detail").innerHTML = `${empty ? "<p>No completed handoffs yet. Share or pick up food to get started.</p>" : ""}${food.length ? `<strong>Food shared for people</strong><ul>${li(food)}</ul>` : ""}${comp.length ? `<strong>Composted (separate, user-reported)</strong><ul>${li(comp)}</ul>` : ""}${d.note ? `<p>${esc(d.note)}</p>` : ""}`;
  } catch {
    if (sessionUser?.id !== uid || generation !== impactGeneration) return;
    strip.innerHTML = title + `<p>Your impact is unavailable right now.</p>`;
  }
}

/* Activity */
async function refreshActivity() {
  if (!sessionUser) return;
  const [l, o, r] = await Promise.allSettled([api("/listings?mine=true"), api("/offers"), api("/requests")]);
  if (l.status === "fulfilled") myListings = (l.value.listings || []).filter(x => x.user_id === sessionUser?.id || x.owner_id === sessionUser?.id || x.user_id == null);
  if (o.status === "fulfilled") myOffers = o.value.offers || [];
  if (r.status === "fulfilled") myRequests = r.value.requests || [];
  renderActivity();
  if ([l, o, r].some(x => x.status === "rejected")) showToast("Some of your activity could not be refreshed.");
}
function renderActivity() {
  const signed = Boolean(sessionUser); $("#activity-grid").hidden = !signed; $("#activity-guest").hidden = signed;
  if (!signed) { $("#my-requests").innerHTML = `<p class="muted">Sign in to see your requests.</p>`; return; }
  $("#my-listings").innerHTML = myListings.length ? myListings.map(l => `<div class="record"><strong>${l.mode === "giveaway" ? "Giveaway" : "Exchange"}</strong>${status(l.status)}<p>${itemList(itemsOf(l))}</p><p>${esc(l.location)} &middot; ${esc(windowText(l.pickup_start, l.pickup_end))}</p><div class="row-actions">${["open","expired","withdrawn"].includes(l.status) ? `<button class="mini-btn" data-fallback="${esc(l.id)}">${l.status === "open" ? "Matches and next steps" : "Next steps"}</button>` : ""}${["open","expired"].includes(l.status) ? `<button class="mini-btn" data-edit="${esc(l.id)}">Edit details</button>` : ""}${l.status === "open" ? `<button class="mini-btn danger" data-withdraw="${esc(l.id)}">Withdraw</button>` : ""}</div></div>`).join("") : `<p class="muted">You have not listed anything yet.</p>`;
  const giveaways = myRequests.filter(r => r.requester_id || r.listing_id && r.owner_id);
  const standalone = myRequests.filter(r => !giveaways.includes(r));
  const offerHtml = myOffers.map(offerCard).join("");
  const gHtml = giveaways.map(giveawayCard).join("");
  $("#my-handoffs").innerHTML = offerHtml + gHtml || `<p class="muted">No handoffs yet.</p>`;
  $("#my-requests").innerHTML = standalone.length ? standalone.map(r => `<div class="record"><strong>${esc(r.quantity ?? "")} ${esc(foldName((r.items || []).join(", ") || r.item || "", r.unit, r.quantity))}${r.quantity == null ? " · Any amount" : ""}</strong>${status(r.status || "open")}<p>${esc(r.location)} &middot; ${esc(windowText(r.pickup_start, r.pickup_end))}</p><p>Needed by ${esc(fmt(r.deadline))}</p></div>`).join("") : `<p class="muted">No requests posted.</p>`;
}
function uid(x) { return x?.user_id || x?.id || x; }
function offerCard(o) {
  const me = sessionUser.id, acc = (o.accepted_by || []).map(uid), done = (o.completed_by || []).map(uid);
  const s = o.status, iAccepted = acc.includes(me), iDone = done.includes(me);
  let actions = "";
  if ((s === "pending" || s === "awaiting_second_acceptance") && !iAccepted) actions = `<button class="mini-btn" data-offer-respond="${esc(o.id)}" data-accept="1">Accept</button><button class="mini-btn danger" data-offer-respond="${esc(o.id)}" data-accept="0">Decline</button>`;
  else if ((s === "pending" || s === "awaiting_second_acceptance") && iAccepted) actions = `<span class="muted">You accepted. Waiting for the other person.</span><button class="mini-btn danger" data-offer-cancel="${esc(o.id)}">Cancel</button>`;
  if ((s === "confirmed" || s === "completion_pending") && !iDone) actions = `<button class="mini-btn" data-offer-pickup="${esc(o.id)}">I completed the handoff</button>${s === "confirmed" && !done.length ? `<button class="mini-btn danger" data-offer-cancel="${esc(o.id)}">Cancel</button>` : ""}`;
  else if (s === "completion_pending" && iDone) actions = `<span class="muted">You confirmed. Waiting for the other person.</span>`;
  if (s === "completed") actions = `<button class="mini-btn" data-rate-offer="${esc(o.id)}">Rate partner</button>`;
  if (s === "declined") { const own = isMine(o.listing_id) ? o.listing_id : isMine(o.match_id) ? o.match_id : null; if (own) actions = `<button class="mini-btn" data-fallback="${esc(own)}">See next steps for my listing</button>`; }
  return `<div class="record"><strong>Exchange offer</strong>${status(s)}<p>Give: ${itemList(o.give_items || o.give)}</p><p>Receive: ${itemList(o.receive_items || o.receive)}</p><p>${esc(o.location)} &middot; ${esc(windowText(o.pickup_start, o.pickup_end))}</p><p class="muted">Accepted by ${acc.length}/2 &middot; Handoff confirmed by ${done.length}/2</p><div class="row-actions">${actions}</div></div>`;
}
function giveawayCard(r) {
  const me = sessionUser.id, owner = r.owner_id === me, done = (r.completed_by || r.confirmed_by || []).map(uid), iDone = done.includes(me), s = r.status;
  let actions = "";
  if (s === "pending" && owner) actions = `<button class="mini-btn" data-gr-decide="${esc(r.id)}" data-accept="1">Approve</button><button class="mini-btn danger" data-gr-decide="${esc(r.id)}" data-accept="0">Decline</button>`;
  if (s === "pending" && !owner) actions = `<span class="muted">Waiting for owner approval.</span><button class="mini-btn danger" data-gr-cancel="${esc(r.id)}">Cancel</button>`;
  if ((s === "approved" || s === "completion_pending") && !iDone) actions = `<button class="mini-btn" data-gr-pickup="${esc(r.id)}">I completed the handoff</button>${s === "approved" && !done.length ? `<button class="mini-btn danger" data-gr-cancel="${esc(r.id)}">Cancel</button>` : ""}`;
  else if (s === "completion_pending" && iDone) actions = `<span class="muted">You confirmed. Waiting for the other person.</span>`;
  if (s === "completed") actions = `<button class="mini-btn" data-rate-gr="${esc(r.id)}" data-to="${esc(owner ? r.requester_id : r.owner_id)}">Rate ${owner ? "recipient" : "giver"}</button>`;
  if (s === "declined" && owner && r.listing_id) actions = `<button class="mini-btn" data-fallback="${esc(r.listing_id)}">Next steps for my listing</button>`;
  const chosen = r.pickup_start ? new Date(r.pickup_start).toLocaleString(undefined, {weekday:"short", hour:"numeric", minute:"2-digit"}) : "the requested time";
  const person = owner ? r.requester_name || "The requester" : "You";
  return `<div class="record"><strong>Giveaway ${owner ? "you are giving" : "you requested"}</strong>${status(s)}<p>${itemList(r.item_details)}</p><p>${esc(person)} ${s === "completed" ? "picked up" : "will pick up"} ${esc(chosen)}</p><p>${esc(r.location)} &middot; ${esc(windowText(r.pickup_start, r.pickup_end))}</p><div class="row-actions">${actions}</div></div>`;
}
async function act(fn, okMsg) { try { const r = await fn(); if (okMsg) showToast(okMsg); return r; } catch (e) { showToast(e.message); if (e.status === 409) await load(); } finally { await refreshActivity(); refreshImpact(); } }
async function rate(body) { const stars = Number(prompt("Rate from 1 to 5 stars")); if (!Number.isInteger(stars) || stars < 1 || stars > 5) return; try { await post("/ratings", {...body, stars}); showToast("Rating saved."); } catch (e) { showToast(e.message); } }
document.addEventListener("click", async e => {
  const b = e.target.closest("button"); if (!b || !b.closest("#my-handoffs,#my-listings,#notifications-list")) return;
  const d = b.dataset; b.disabled = true;
  try {
    if (d.fallback) await openFallback(d.fallback);
    else if (d.edit) { const l = myListings.find(x => x.id === d.edit); if (l) editListing(l); }
    else if (d.withdraw) { if (confirm("Withdraw this open listing?")) await act(() => api(`/listings/${encodeURIComponent(d.withdraw)}`, {method:"DELETE"}), "Listing withdrawn."); await load(); }
    else if (d.offerRespond) {
      const accepting = d.accept === "1", offer = myOffers.find(x => x.id === d.offerRespond);
      if (accepting) {
        if (!offer) return showToast("The swap has changed. Refresh and try again.");
        const first = isMine(offer.listing_id);
        if (!confirmSwapAmounts(first ? offer.give_items : offer.receive_items,
          first ? offer.receive_items : offer.give_items, offer.location, offer.pickup_start, offer.pickup_end)) return;
      }
      const r = await act(() => post(`/offers/${encodeURIComponent(d.offerRespond)}/respond`, {accept:accepting}), accepting ? "Your acceptance was recorded." : "Offer declined.");
      if (r && !accepting) {
        const o = r.offer || myOffers.find(x => x.id === d.offerRespond);
        const own = o && (isMine(o.listing_id) ? o.listing_id : isMine(o.match_id) ? o.match_id : null);
        if (own) openFallback(own);
      }
    }
    else if (d.offerCancel) { if (confirm("Cancel this offer?")) await act(() => post(`/offers/${encodeURIComponent(d.offerCancel)}/cancel`), "Offer cancelled."); }
    else if (d.offerPickup) { if (confirm("Confirm you personally completed this handoff?")) await act(() => post(`/offers/${encodeURIComponent(d.offerPickup)}/confirm-pickup`), "Your handoff confirmation was recorded."); }
    else if (d.rateOffer) { const o = myOffers.find(x => x.id === d.rateOffer); const other = [...(o?.participants || []).map(uid), o?.owner_id, o?.match_owner_id, o?.user_id, o?.match_user_id].find(x => x && x !== sessionUser.id); if (other) await rate({offer_id:d.rateOffer, to_user_id:other}); else showToast("Could not identify your exchange partner."); }
    else if (d.grDecide) await act(() => post(`/giveaway-requests/${encodeURIComponent(d.grDecide)}/decision`, {accept:d.accept === "1"}), d.accept === "1" ? "Request approved." : "Request declined.");
    else if (d.grPickup) { if (confirm("Confirm you personally completed this handoff?")) await act(() => post(`/giveaway-requests/${encodeURIComponent(d.grPickup)}/pickup`), "Your handoff confirmation was recorded."); }
    else if (d.grCancel) { if (confirm("Cancel this giveaway request?")) await act(() => post(`/giveaway-requests/${encodeURIComponent(d.grCancel)}/cancel`), "Request cancelled."); }
    else if (d.rateGr) await rate({request_id:d.rateGr, to_user_id:d.to});
  } finally { b.disabled = false; }
});
$("#refresh-activity").onclick = () => sessionUser ? load() : openAuth();

/* Fallback + matches */
function destroyDonationMap() {
  if (donationMap) donationMap.remove();
  donationMap = null; donationMarkers = new Map();
}
function donationCard(o) {
  const route = Number.isFinite(o.drive_minutes) ? `<strong class="route-time">${esc(o.drive_minutes)} min</strong><span>${esc(o.drive_miles)} mi by car</span>` : `<span>Search for live drive time</span>`;
  const rules = o.item_rules_status === "matched" ? "Verified item match" : "Acceptance verified — confirm your item";
  return `<article class="donation-card" data-donation-card="${esc(o.id)}" tabindex="0">
    <div class="donation-card-head"><div><span class="verified-badge">${esc(rules)}</span><h5>${esc(o.name)}</h5></div><div class="route-summary">${route}</div></div>
    <p>${esc(o.address || o.location)}</p><p>${esc(o.policy || "See the official donation instructions for current acceptance rules.")}</p>
    <p>${esc(o.hours || "Hours not published")}</p>
    <p class="source-line">Acceptance source checked ${esc(o.checked_at || "unknown")} &middot; <a href="${esc(o.url)}" target="_blank" rel="noopener">Official donation instructions</a></p>
    ${o.notice ? `<p class="muted">${esc(o.notice)}</p>` : ""}
    <div class="donation-confirm">
      <label class="check"><input type="checkbox" data-feasible="${esc(o.id)}"><span>These unopened packages contain no glass, and I confirmed acceptance and can reach this destination during its published hours.</span></label>
      <label class="check"><input type="checkbox" data-drop="${esc(o.id)}"><span>I actually dropped this food off at ${esc(o.name)}.</span></label>
      <button type="button" class="mini-btn" data-outcome="${esc(o.id)}" data-type="donation">Record donation drop-off</button>
    </div>
  </article>`;
}
function compostCard(o, isInedible) {
  return `<div class="option"><strong>${esc(o.name)}</strong> <span class="status">compost</span><p>${esc(o.location)} &middot; Hours: ${esc(o.hours || "not published")}</p><p>Accepts: ${esc((o.accepted_categories || []).join(", ") || "see website")}${o.exclusions?.length ? ` &middot; Excludes: ${esc(o.exclusions.join(", "))}` : ""}</p>${o.notice ? `<p class="muted">${esc(o.notice)}</p>` : ""}<p class="muted">Checked ${esc(o.checked_at || "unknown")} ${o.url ? `&middot; <a href="${esc(o.url)}" target="_blank" rel="noopener">Confirm current rules</a>` : ""}</p>${!isInedible ? `<p class="muted">Composting is only for food that is no longer edible. Confirm below first.</p>` : `<label class="check"><input type="checkbox" data-feasible="${esc(o.id)}"><span>These are accepted fruit/vegetable scraps, and I confirmed acceptance and can reach this San Jose destination during its published hours.</span></label><label class="check"><input type="checkbox" data-drop="${esc(o.id)}"><span>I actually dropped this food off at ${esc(o.name)}.</span></label><button class="mini-btn" data-outcome="${esc(o.id)}" data-type="compost">Record compost drop-off</button>`}</div>`;
}
function selectDonation(id) {
  offerArea.querySelectorAll("[data-donation-card]").forEach(card => card.classList.toggle("selected", card.dataset.donationCard === id));
  const marker = donationMarkers.get(id); if (marker && donationMap) { donationMap.panTo(marker.getLatLng()); marker.openPopup(); }
}
function renderDonationMap(destinations, origin) {
  destroyDonationMap();
  const container = $("#donation-map"); if (!container || !destinations.length) return;
  donationMap = L.map(container, {scrollWheelZoom:false, zoomControl:true});
  const tileLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom:19, attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'});
  tileLayer.on("tileerror", () => { const s = $("#donation-map-status"); if (s) s.textContent = "Map tiles are unavailable, but the verified destination cards still work."; });
  tileLayer.addTo(donationMap);
  const points = [];
  destinations.forEach(o => {
    if (!Number.isFinite(o.latitude) || !Number.isFinite(o.longitude)) return;
    const marker = L.marker([o.latitude, o.longitude], {icon:L.divIcon({className:"donation-pin", html:"<span></span>", iconSize:[25,32], iconAnchor:[12,30]})}).addTo(donationMap).bindPopup(`<strong>${esc(o.name)}</strong><br>${esc(o.address || o.location)}`);
    marker.on("click", () => selectDonation(o.id)); donationMarkers.set(o.id, marker); points.push([o.latitude, o.longitude]);
  });
  if (origin && Number.isFinite(origin.latitude) && Number.isFinite(origin.longitude)) {
    L.circleMarker([origin.latitude, origin.longitude], {radius:7, color:"#315f45", fillColor:"#fff", fillOpacity:1, weight:3}).addTo(donationMap).bindPopup(`Search area: ${esc(origin.label)}`);
    points.push([origin.latitude, origin.longitude]);
  }
  if (points.length === 1) donationMap.setView(points[0], 12); else donationMap.fitBounds(points, {padding:[28,28], maxZoom:12});
  setTimeout(() => donationMap?.invalidateSize(), 0);
}
function bindDonationCards() {
  offerArea.querySelectorAll("[data-donation-card]").forEach(card => {
    card.onclick = e => { if (!e.target.closest("a,button,input,label")) selectDonation(card.dataset.donationCard); };
    card.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectDonation(card.dataset.donationCard); } };
  });
}
function bindOutcomeButtons(id) {
  offerArea.querySelectorAll("[data-outcome]").forEach(btn => btn.onclick = async () => {
    const cb = offerArea.querySelector(`[data-drop="${CSS.escape(btn.dataset.outcome)}"]`), fz = offerArea.querySelector(`[data-feasible="${CSS.escape(btn.dataset.outcome)}"]`);
    if (!fz?.checked) return showToast("Confirm acceptance and that you can reach this destination.");
    if (!cb?.checked) return showToast("Confirm you actually dropped it off.");
    const compost = btn.dataset.type === "compost", body = {listing_id:id, destination_id:btn.dataset.outcome, action:compost ? "compost" : "donation", confirmed:true, destination_feasible_confirmed:true};
    if (!compost) body.no_glass_confirmed = true; else Object.assign(body, {inedible_confirmed:true, material:"fruit_vegetable_scraps"});
    if (await act(() => post("/outcomes", body), "Drop-off recorded as user-reported.")) { destroyDonationMap(); offerArea.hidden = true; load(); }
  });
}
async function openFallback(id) {
  destroyDonationMap(); offerArea.hidden = false; offerArea.innerHTML = `<p class="muted">Checking matches and options...</p>`; offerArea.scrollIntoView({behavior:"smooth", block:"center"});
  let fb; try { fb = await api(`/listings/${encodeURIComponent(id)}/fallback-options`); } catch (e) { offerArea.innerHTML = `<h3>Could not load next steps</h3><p>${esc(e.message)}</p><button class="mini-btn" id="fb-retry">Retry</button>`; $("#fb-retry").onclick = () => openFallback(id); return; }
  const matches = fb.matches || [], opts = fb.options || [], donations = opts.filter(o => o.type === "donation"), compost = opts.filter(o => o.type === "compost"), isInedible = fb.inedible_confirmed === true || myListings.some(l => l.id === id && l.inedible_confirmed === true);
  offerArea.innerHTML = `<h3>Next steps for your listing</h3>${fb.message ? `<p>${esc(fb.message)}</p>` : ""}
  ${matches.length ? `<h4 class="sub-head">Compatible matches</h4>` + matches.map((m, i) => `<div class="option"><p>You give ${itemList(m.give_items || m.give)}; you receive ${itemList(m.receive_items || m.receive)}.</p><p>${esc(m.location)} &middot; ${esc(windowText(m.pickup_start, m.pickup_end))}</p>${m.reason ? `<p class="muted">${esc(m.reason)}</p>` : ""}<button class="mini-btn" data-propose="${i}">Propose this fixed offer</button></div>`).join("") : `<p>No compatible exchange match right now.</p>`}
  ${fb.can_switch_to_giveaway ? `<div class="option"><p>You can switch this listing to a giveaway. Only you decide.</p><button class="mini-btn" id="fb-giveaway">Switch to giveaway</button></div>` : ""}
  <h4 class="sub-head">Verified food donation options</h4>
  <p class="muted">The organizations' official pages are the acceptance evidence. OpenStreetMap supplies map geography only and is never treated as proof that a place accepts food.</p>
  ${donations.length ? `<form id="donation-route-form" class="donation-route-form"><label>ZIP code or neighborhood<input name="area" value="95125" maxlength="100" autocomplete="postal-code" required></label><label>Maximum drive<select name="max_drive_minutes"><option value="15">15 minutes</option><option value="30" selected>30 minutes</option><option value="60">60 minutes</option></select></label><button type="submit" class="mini-btn">Find closest options</button></form>
  <p id="donation-route-status" class="route-status" role="status">${fb.routing_configured ? "Enter your area for live driving times." : "Live driving times are not configured. Add OPENROUTESERVICE_API_KEY to Replit Secrets; verified addresses and links remain available."}</p>
  <div class="donation-results"><div id="donation-cards" class="donation-cards">${donations.map(donationCard).join("")}</div><div class="donation-map-wrap"><div id="donation-map" class="donation-map" aria-label="Map of verified donation destinations"></div><p id="donation-map-status" class="map-status">Map data © OpenStreetMap contributors. Driving estimates by openrouteservice.</p></div></div>` : `<p>${isInedible ? "Donation is unavailable because this listing is marked as no longer edible." : "No verified donation destination is compatible with these items. Current verified partners accept only eligible unopened, unexpired, shelf-stable food without glass."}</p>`}
  <h4 class="sub-head">Compost options</h4>
  ${compost.length ? compost.map(o => compostCard(o, isInedible)).join("") : `<p>${isInedible ? "No verified compost destination is compatible with these items. The current compost option accepts only fruit and vegetable scraps." : "Compost is available only after you confirm the food is no longer edible. A missed exchange or pickup deadline does not mean food is spoiled."}</p>`}
  ${!isInedible ? `<div class="option"><label class="check"><input type="checkbox" id="inedible-check"><span>This food is no longer edible. I understand this withdraws it from edible offers. A passed deadline alone does not mean it is spoiled.</span></label><button class="mini-btn danger" id="inedible-btn">Confirm not edible</button></div>` : ""}
  <p class="muted">Recommendations are not recorded as outcomes. Nothing is donated automatically.</p><button class="mini-btn" id="fb-close">Close</button>`;
  offerArea.querySelectorAll("[data-propose]").forEach(btn => btn.onclick = async () => { const m = matches[btn.dataset.propose]; if (!confirmSwapAmounts(m.give_items, m.receive_items, m.location, m.pickup_start, m.pickup_end)) return; btn.disabled = true; const r = await act(() => post("/offers", {listing_id:id, match_id:m.listing?.id || m.match_id}), "Offer sent. Both of you must accept."); btn.disabled = false; if (r) offerArea.hidden = true; else openFallback(id); });
  const sw = $("#fb-giveaway"); if (sw) sw.onclick = () => { const l = myListings.find(x => x.id === id) || listings.find(x => x.id === id); if (!l) return showToast("Could not load this listing's details. Refresh and try again."); offerArea.hidden = true; editListing(l, {mode:"giveaway"}); showToast("Review the giveaway details, check with Bob, then publish."); };
  const ib = $("#inedible-btn"); if (ib) ib.onclick = async () => { if (!$("#inedible-check").checked) return showToast("Tick the confirmation first."); if (await act(() => api(`/listings/${encodeURIComponent(id)}`, {method:"PATCH", body:JSON.stringify({inedible_confirmed:true})}), "Marked as not edible.")) { await load(); openFallback(id); } };
  bindDonationCards(); bindOutcomeButtons(id); if (donations.length) renderDonationMap(donations);
  const routeForm = $("#donation-route-form"); if (routeForm) {
    const runRouteSearch = async () => {
      const button = routeForm.querySelector("button"), statusEl = $("#donation-route-status"), area = routeForm.elements.area.value.trim(), max = Number(routeForm.elements.max_drive_minutes.value);
      button.disabled = true; statusEl.className = "route-status"; statusEl.textContent = "Calculating live drive times...";
      try {
        const result = await post(`/listings/${encodeURIComponent(id)}/donation-routes`, {area, max_drive_minutes:max}), rows = result.destinations || [];
        $("#donation-cards").innerHTML = rows.length ? rows.map(donationCard).join("") : `<div class="route-empty"><strong>No verified destination is within ${esc(max)} minutes.</strong><p>Try a larger drive limit, another area, an exchange, or a giveaway. We will not suggest an unverified drop-off.</p></div>`;
        statusEl.textContent = rows.length ? `${rows.length} verified option${rows.length === 1 ? "" : "s"} from ${result.origin?.label || area}, sorted by live driving time.` : `No verified options within ${max} minutes of ${result.origin?.label || area}.`;
        bindDonationCards(); bindOutcomeButtons(id); renderDonationMap(rows, result.origin);
      } catch (error) { statusEl.className = "route-status error"; statusEl.textContent = error.message; }
      finally { button.disabled = false; }
    };
    routeForm.onsubmit = e => { e.preventDefault(); runRouteSearch(); };
    routeForm.elements.max_drive_minutes.onchange = () => { if (routeForm.elements.area.value.trim()) runRouteSearch(); };
  }
  $("#fb-close").onclick = () => { destroyDonationMap(); offerArea.hidden = true; };
}

/* Giveaway request */
let giveawayTarget = null;
function openGiveawayDialog(id) {
  const l = listings.find(x => x.id === id); giveawayTarget = id; const f = $("#giveaway-form"); f.reset(); $("#gd-notice").hidden = true;
  $("#gd-summary").innerHTML = l ? `${itemList(itemsOf(l))}. Owner is available at ${esc(l.location)}, ${esc(windowText(l.pickup_start, l.pickup_end))}. Choose a window inside it.` : "";
  if (l) f.location.value = l.location || "";
  updateSpotCounters();
  if (l) { const lim = pickupLimits(), lo = Math.max(lim.min, new Date(l.pickup_start).getTime() || lim.min), hi = Math.min(lim.max, ...[l.pickup_end, l.deadline].map(v => new Date(v).getTime()).filter(Number.isFinite));
    [f.pickup_start, f.pickup_end].forEach(el => setRange(el, localStr(new Date(Math.ceil(lo / 60000) * 60000)), localStr(new Date(hi)))); }
  $("#giveaway-dialog").showModal();
}
$("#giveaway-form").addEventListener("submit", async e => {
  e.preventDefault(); const f = e.currentTarget, s = toUtcIso(f.pickup_start.value), en = toUtcIso(f.pickup_end.value), n = $("#gd-notice");
  if (!f.location.value || !s || !en) return setNotice(n, "Choose a zone and an exact pickup window.");
  const gl = listings.find(x => x.id === giveawayTarget);
  const ge = windowErrors({start:s, end:en, bound:gl ? {start:gl.pickup_start, end:gl.pickup_end, deadline:gl.deadline} : null});
  if (ge.length) return setNotice(n, ge.join(" "));
  try { await post(`/listings/${encodeURIComponent(giveawayTarget)}/requests`, {pickup_start:s, pickup_end:en, location:f.location.value}); $("#giveaway-dialog").close(); showToast("Request sent. The owner decides."); refreshActivity(); }
  catch (er) { setNotice(n, er.message); }
});
document.querySelectorAll("[data-close]").forEach(b => b.onclick = () => b.closest("dialog").close());

/* Listing form */
function renderAllergenChips() {
  $("#allergen-chips").innerHTML = allergenMarkup(form.allergens.value, esc, "data-allergen-other", "legacy-allergen");
}
renderAllergenChips();
$("#allergen-chips").addEventListener("click", e => {
  const chip = e.target.closest('[data-action="allergen-toggle"]'); if (!chip) return;
  form.allergens.value = toggleAllergen(form.allergens.value, chip.dataset.value);
  renderAllergenChips(); onFormChanged();
});
$("#allergen-chips").addEventListener("input", e => {
  if (!e.target.matches("[data-allergen-other]")) return;
  form.allergens.value = allergenValue(parseAllergens(form.allergens.value).selected, e.target.value);
});
function itemRow(i = {}) {
  const n = $("#item-rows").children.length + 1, row = document.createElement("fieldset"); row.className = "item-row";
  row.innerHTML = `<legend>Item ${n}</legend><button type="button" class="mini-btn danger remove-item">Remove</button><div class="form-row"><label class="wide">Food name<input data-f="name" required placeholder="${ITEM_PLACEHOLDER}" value="${esc(foldName(i.name ?? "", i.unit, i.quantity))}"></label><label>Quantity<input data-f="quantity" ${QTY_ATTRS} required value="${esc(i.quantity ?? "")}"></label></div><div class="form-row"><label>Category<select data-f="category" required><option value="">Choose</option><option value="produce">Whole produce</option><option value="shelf_stable">Unopened shelf-stable</option></select></label><label>Unopened?<select data-f="unopened"><option value="">Not applicable</option><option value="true">Yes, unopened</option><option value="false">No</option></select></label></div><div class="form-row"><label>Printed date <span class="optional">(optional)</span><input data-f="printed_date" type="date" min="${printedLimits().min}" max="${printedLimits().max}" value="${esc(/^\d{4}-\d{2}-\d{2}$/.test(i.printed_date || "") ? i.printed_date : "")}"></label><label>Date label<select data-f="date_type"><option value="">None</option><option value="best_by">Best by</option><option value="use_by">Use by</option><option value="sell_by">Sell by</option><option value="expiration">Expiration</option></select></label></div>`;
  row.querySelector("[data-f=category]").value = i.category ?? ""; row.querySelector("[data-f=unopened]").value = i.unopened == null ? "" : String(i.unopened);
  if (i.date_type) { const dt = row.querySelector("[data-f=date_type]"); dt.value = i.date_type; if (dt.value !== i.date_type) dt.value = ""; }
  row.querySelector(".remove-item").onclick = () => { row.remove(); renumber(); onFormChanged(); };
  $("#item-rows").append(row); renumber();
}
function renumber() { const rows = [...$("#item-rows").children], bundle = $("#listing-type").value === "bundle"; rows.forEach((r, i) => { r.querySelector("legend").textContent = `Item ${i + 1}`; r.querySelector(".remove-item").hidden = rows.length === 1; }); $("#add-item").hidden = !bundle || rows.length >= 5; }
function setMode() { const g = $("#listing-mode").value === "giveaway"; document.querySelectorAll("#listing-form .exchange-only").forEach(n => n.hidden = g); }
$("#listing-mode").onchange = setMode;
$("#listing-type").onchange = () => { if ($("#listing-type").value === "single") [...$("#item-rows").children].slice(1).forEach(r => r.remove()); renumber(); onFormChanged(); };
$("#add-item").onclick = () => { if ($("#item-rows").children.length < 5) { itemRow(); onFormChanged(); } };
function resetListingForm() { form.reset(); form.allergens.value = ""; renderAllergenChips(); $("#item-rows").innerHTML = ""; itemRow(); setMode(); renumber(); }

$("#interpret-button").onclick = async () => {
  const text = $("#interpret-text").value.trim(), st = $("#interpret-notice"), btn = $("#interpret-button");
  if (!sessionUser) return openAuth(); if (!text) { st.textContent = "Describe your surplus first."; return; }
  btn.disabled = true; st.textContent = "Bob is interpreting your description...";
  try {
    const {draft} = await post("/interpret-listing", {text, timezone:TZ});
    ["pickup_start","pickup_end","deadline"].forEach(k => form[k].value = ""); form.fresh.checked = false; form.storage_confirmed.checked = false;
    const items = (draft.item_details || []).slice(0, 5); $("#item-rows").innerHTML = "";
    $("#listing-type").value = items.length > 1 ? "bundle" : "single"; (items.length ? items : [{}]).forEach(itemRow);
    if (items.length && items.every(i => i.category === "produce")) {
      form.allergens.value = "None"; renderAllergenChips();
    }
    form.location.value = draft.location || ""; updateSpotCounters();
    const notes = [...(draft.ambiguities || [])];
    if (draft.location) notes.push(`Check pickup spot "${draft.location}". ${SPOT_NOTE}`);
    if (draft.availability) notes.push(`You said availability: "${draft.availability}". Set exact pickup times.`);
    if (draft.deadline_text) notes.push(`You said deadline: "${draft.deadline_text}". Set the exact date and time.`);
    st.innerHTML = `Draft ready. Review every field, fill blanks, and choose exact dates yourself. Nothing has been saved.${notes.length ? `<div class="ambiguities"><strong>Please resolve:</strong><ul>${notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul></div>` : ""}`;
    renumber(); invalidateApproval(true);
  } catch (e) { st.textContent = `Interpretation failed: ${e.message} Your text is kept; retry or fill the form manually.`; }
  finally { btn.disabled = false; }
};

/* Listing review + publish (Bob approval required before every new or updated listing) */
const REVIEW_TTL_MS = 15 * 60000, REVIEW_TIMEOUT_MS = 90000, MAX_PHOTO = 5 * 1024 * 1024;
{
  const help = document.createElement("p");
  help.textContent = "Bob is RePlate's AI helper. It drafts your listing and checks it before it goes live.";
  $("#listing-form-title").insertAdjacentElement("afterend", help);
}
let photo = null, photoBusy = false, uploadGen = 0, approval = null, reviewGen = 0, reviewing = false, publishing = false, editingId = null, reviewState = {kind:"idle"};
form.wanted_food.placeholder = ITEM_PLACEHOLDER;
form.wanted_quantity?.closest("label")?.remove();
{ const help = document.createElement("p"); help.textContent = "No return quantity required. Swaps exchange the full amounts shown in both listings, including every item in a bundle."; form.wanted_food.closest("label").insertAdjacentElement("afterend", help); }
{ const rf = $("#request-form"); rf.item.placeholder = ITEM_PLACEHOLDER; setQtyAttrs(rf.quantity); rf.quantity.required = false; rf.quantity.placeholder = "Any amount"; rf.quantity.closest("label").firstChild.textContent = "Quantity (optional; blank means any amount)"; rf.addEventListener("focusin", () => applyPickupLimits(rf)); applyPickupLimits(rf); }
function toLocalInput(v) { const d = new Date(v); if (!v || Number.isNaN(d.getTime())) return ""; const p = n => String(n).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`; }
function photoUrl(p) { return p.local_url || p.preview_url || `${API}/listing-photos/${encodeURIComponent(p.id)}`; }

function readDraft() {
  const miss = [], warn = [], type = $("#listing-type").value, mode = $("#listing-mode").value, now = Date.now();
  if (!["exchange","giveaway"].includes(mode)) miss.push("Choose exchange or giveaway.");
  if (!["single","bundle"].includes(type)) miss.push("Choose single item or bundle.");
  const items = [...$("#item-rows").children].map(r => { const g = f => r.querySelector(`[data-f=${f}]`).value.trim(); const u = g("unopened"); return {name:g("name"), quantity:parseQty(g("quantity")), rawQty:g("quantity"), unit:CANON_UNIT, category:g("category") || null, unopened:u === "" ? null : u === "true", printed_date:g("printed_date") || null, date_type:g("date_type") || null}; });
  if (type === "single" && items.length !== 1) miss.push("A single listing needs exactly one item.");
  if (items.length < 1 || items.length > 5) miss.push("List between 1 and 5 items.");
  items.forEach((it, i) => { const n = `Item ${i + 1}`;
    if (!it.name) miss.push(`${n}: food name.`);
    const qe = qtyError(it.rawQty, "quantity"); if (qe) miss.push(`${n}: ${qe}`);
    if (!["produce","shelf_stable"].includes(it.category)) miss.push(`${n}: category.`);
    if (it.category === "shelf_stable" && it.unopened !== true) miss.push(`${n}: packaged food must be marked unopened.`);
    if (!!it.printed_date !== !!it.date_type) miss.push(`${n}: printed date and date label must both be set or both left blank.`);
    const pc = printedCheck(it.printed_date, it.date_type, now); pc.errors.forEach(x => miss.push(`${n}: ${x}`)); pc.warnings.forEach(x => warn.push(`${n}: ${x}`));
  });
  items.forEach(it => delete it.rawQty);
  const wf = form.wanted_food.value.trim();
  if (mode === "exchange" && !wf) miss.push("Food you want in return.");
  const locationError = spotError(form.location.value); if (locationError) miss.push(locationError);
  const ps = toUtcIso(form.pickup_start.value), pe = toUtcIso(form.pickup_end.value), dl = toUtcIso(form.deadline.value);
  windowErrors({start:ps, end:pe, deadline:dl, needDeadline:true}, now).forEach(x => miss.push(x));
  if (!TZ) miss.push("Time zone could not be detected.");
  const allergens = form.allergens.value.trim(), allergenMessage = allergenError(form.allergens.value);
  if (allergenMessage) miss.push(allergenMessage);
  if (!form.fresh.checked) miss.push("Confirm food condition.");
  if (!form.storage_confirmed.checked) miss.push("Confirm storage and allergen disclosure.");
  if (photoBusy) miss.push("Wait for the photo upload to finish."); else if (!photo?.id) miss.push(photo ? "Upload the selected photo successfully." : "Photo of the actual food.");
  const payload = {mode, listing_type:type, item_details:items, wanted_food:mode === "exchange" ? wf : null, wanted_quantity:null, wanted_unit:mode === "exchange" ? CANON_UNIT : null, location:form.location.value, pickup_start:ps, pickup_end:pe, deadline:dl, timezone:TZ, fresh:form.fresh.checked, storage_confirmed:form.storage_confirmed.checked, allergens, ...allergenPayload(form.allergens.value), photo_id:photo?.id || null};
  return {payload, key:JSON.stringify(payload), miss, warn};
}
function approvalValid(d) { return !!(approval && sessionUser && approval.key === d.key && Date.now() < approval.exp && approval.photo_id === photo?.id); }
function renderReview() {
  const d = readDraft(); applyPickupLimits(form);
  if (approval && Date.now() >= approval.exp) { approval = null; reviewState = {kind:"error", message:"Bob's approval expired. Check again before publishing."}; }
  $("#listing-checklist").innerHTML = (d.miss.length ? `<p class="muted">Missing or incorrect details (${d.miss.length}):</p><ul>${d.miss.map(m => `<li>${esc(m)}</li>`).join("")}</ul>` : `<p class="ok">All required details are complete.</p>`) + (d.warn.length ? `<p class="muted">Heads up:</p><ul>${d.warn.map(w => `<li>${esc(w)}</li>`).join("")}</ul>` : "");
  const ok = approvalValid(d), st = $("#review-status");
  $("#review-button").disabled = !sessionUser || d.miss.length > 0 || reviewing || publishing;
  $("#review-button").textContent = reviewing ? "Bob is checking..." : ok ? "Check again with Bob" : "Check with Bob";
  $("#publish-button").disabled = !sessionUser || d.miss.length > 0 || !ok || reviewing || publishing;
  $("#publish-button").textContent = publishing ? "Publishing..." : editingId ? "Publish updated listing" : "Publish listing";
  const r = reviewState; let html = "";
  if (reviewing) html = `<div class="rs-checking">Bob is checking your details and photo. You can keep editing, but any edit cancels this check.</div>`;
  else if (ok) { const mins = Math.max(0, Math.ceil((approval.exp - Date.now()) / 60000)); html = `<div class="rs-approved"><strong>Ready to publish.</strong> Bob found the details and photo consistent.${approval.reason ? ` ${esc(approval.reason)}` : ""} Approval expires at ${esc(new Date(approval.exp).toLocaleTimeString(undefined,{hour:"numeric",minute:"2-digit"}))} (about ${mins} min).</div>`; }
  else if (r.kind === "rejected" || r.kind === "error") { const checks = Array.isArray(r.checks) ? r.checks : r.checks && typeof r.checks === "object" ? Object.entries(r.checks).map(([k, v]) => `${k.replaceAll("_"," ")}: ${typeof v === "object" ? (v.message || v.reason || (v.passed ? "ok" : "needs attention")) : v}`) : []; html = `<div class="rs-${r.kind}"><strong>${r.kind === "rejected" ? "Not approved." : "Check could not finish."}</strong> ${esc(r.message || "")}${checks.length ? `<ul>${checks.map(c => `<li>${esc(typeof c === "string" ? c : c.message || c.reason || c.name || JSON.stringify(c))}</li>`).join("")}</ul>` : ""}<br>Your entries are kept. Fix them and check again.</div>`; }
  else if (r.kind === "idle" && r.message) html = `<div class="rs-checking">${esc(r.message)}</div>`;
  st.innerHTML = html;
}
function invalidateApproval(changed) {
  const had = approval || reviewing; approval = null; reviewGen++; reviewing = false;
  reviewState = {kind:"idle", message:had && changed ? "Details changed, so the previous check no longer applies. Check again with Bob." : ""};
  renderReview();
}
function onFormChanged() { invalidateApproval(true); }
form.addEventListener("input", e => { if (e.target.id !== "interpret-text" && e.target.id !== "photo-input") onFormChanged(); });
form.addEventListener("change", e => {
  if (e.target.matches('[data-f="category"]') && e.target.value === "produce" &&
      [...form.querySelectorAll('[data-f="category"]')].every(input => input.value === "produce")) {
    form.allergens.value = "None"; renderAllergenChips();
  }
  if (e.target.id !== "interpret-text" && e.target.id !== "photo-input") onFormChanged();
});
setInterval(() => { if (approval || reviewing) renderReview(); }, 1000);
setInterval(() => { if (approval && Date.now() >= approval.exp) renderReview(); }, 1000);

/* Photo */
function renderPhoto() {
  const pv = $("#photo-preview"); pv.classList.toggle("busy", photoBusy);
  pv.innerHTML = photo ? `<img src="${esc(photoUrl(photo))}" alt="Selected food photo preview">` : `<span>Your image preview</span>`;
  $("#photo-remove").hidden = !photo;
  $("#photo-retry").hidden = !photo?.file || photoBusy;
  $("#photo-retry").textContent = sessionUser ? "Retry upload" : "Sign in to upload";
  $("#photo-filename").textContent = photo?.filename || (photo?.id ? "Saved listing photo." : "No image selected.");
  document.querySelector("label.photo-pick").textContent = photo ? "Replace photo" : "Choose photo";
}
function discardPhoto(p) { if (p?.local_url) URL.revokeObjectURL(p.local_url); if (p?.owned && p.id) api(`/listing-photos/${encodeURIComponent(p.id)}`, {method:"DELETE"}).catch(() => {}); }
$("#photo-input").addEventListener("change", e => {
  const file = e.target.files[0], ps = $("#photo-status"); e.target.value = ""; if (!file) return;
  if (!["image/jpeg","image/png","image/webp"].includes(file.type)) { ps.textContent = "Use a JPG, PNG or WebP image."; return; }
  if (file.size > MAX_PHOTO) { ps.textContent = "That photo is larger than 5 MB. Choose a smaller one."; return; }
  const old = photo; photo = {id:null, local_url:URL.createObjectURL(file), file, filename:file.name, owned:false, guestDraft:!sessionUser}; discardPhoto(old);
  renderPhoto(); invalidateApproval(true);
  if (!sessionUser) { ps.textContent = "Photo selected on this device. Sign in to upload it, complete the details, and request Bob's review."; return; }
  uploadSelectedPhoto();
});
async function uploadSelectedPhoto() {
  if (!photo?.file) return;
  if (!sessionUser) return openAuth();
  const file = photo.file, ps = $("#photo-status");
  const gen = ++uploadGen; photoBusy = true; ps.textContent = "Uploading photo... Your preview is not approval to publish."; renderPhoto(); invalidateApproval(true);
  const fd = new FormData(); fd.append("photo", file);
  try {
    const res = await fetch(`${API}/listing-photos`, {method:"POST", credentials:"include", body:fd}).catch(() => { throw Error("Cannot reach the RePlate server."); });
    let data = {}; try { data = await res.json(); } catch {}
    if (!res.ok) throw Error(data.error || `Upload failed (${res.status}).`);
    if (!data.photo_id || !data.preview_url) throw Error("The upload did not return a saved photo. Please retry.");
    const fresh = {id:data.photo_id, preview_url:data.preview_url, filename:file.name, owned:true};
    if (gen !== uploadGen || !sessionUser) { discardPhoto(fresh); return; }
    const old = photo; photo = fresh; discardPhoto(old); ps.textContent = "Photo uploaded. Bob will check it with your details.";
  } catch (er) { if (gen === uploadGen) ps.textContent = `${er.message} The image is selected but not uploaded. Your entries are kept; use Retry upload when ready.`; }
  finally { if (gen === uploadGen) { photoBusy = false; renderPhoto(); invalidateApproval(true); } }
}
$("#photo-retry").onclick = uploadSelectedPhoto;
$("#photo-remove").onclick = () => { uploadGen++; photoBusy = false; discardPhoto(photo); photo = null; $("#photo-status").textContent = "Photo removed. A photo is required to publish."; renderPhoto(); invalidateApproval(true); };

/* Bob review */
$("#review-button").onclick = async () => {
  if (!sessionUser) return openAuth();
  const d = readDraft(); if (d.miss.length) return renderReview();
  const gen = ++reviewGen; approval = null; reviewing = true; reviewState = {kind:"checking"}; renderReview();
  const ctrl = new AbortController(), t = setTimeout(() => ctrl.abort(), REVIEW_TIMEOUT_MS);
  try {
    const r = await api("/listings/review", {method:"POST", body:d.key, signal:ctrl.signal});
    if (gen !== reviewGen || !sessionUser || readDraft().key !== d.key) return;
    if (r.approved === true && r.approval_id && r.photo_id === d.payload.photo_id) {
      const server = Date.parse(r.expires_at); approval = {id:r.approval_id, key:d.key, photo_id:r.photo_id, reason:r.reason || "", exp:Math.min(Number.isFinite(server) ? server : Infinity, Date.now() + REVIEW_TTL_MS)};
      reviewState = {kind:"approved"};
    } else reviewState = {kind:"rejected", message:r.reason || r.error || "Bob did not approve this listing.", checks:r.checks};
  } catch (er) {
    if (gen !== reviewGen) return;
    if (er.status === 422) reviewState = {kind:"rejected", message:er.data?.reason || er.message, checks:er.data?.checks};
    else if (er.status === 401) { reviewState = {kind:"error", message:"Your session ended. Sign in again."}; }
    else reviewState = {kind:"error", message:er.timeout ? "Bob did not respond in time. Publishing stays blocked; try again shortly." : er.status === 503 ? `${er.message} Bob is unavailable, so publishing is blocked for now.` : er.message};
  } finally { clearTimeout(t); if (gen === reviewGen) { reviewing = false; renderReview(); } }
};

/* Publish */
form.addEventListener("submit", async e => {
  e.preventDefault(); if (!sessionUser) return openAuth(); notice.hidden = true;
  const d = readDraft(); if (d.miss.length || !approvalValid(d) || reviewing || publishing) { renderReview(); return setNotice(notice, d.miss.length ? "Complete the missing details first." : "Check the current details with Bob before publishing."); }
  const body = JSON.stringify({...d.payload, approval_id:approval.id}), id = editingId, mode = d.payload.mode;
  publishing = true; renderReview();
  try {
    const r = id ? await api(`/listings/${encodeURIComponent(id)}`, {method:"PATCH", body}) : await post("/listings", JSON.parse(body));
    const n = (r.matches || []).length; photo = null; resetEditor(false);
    setNotice(notice, (id ? "Listing updated. " : "") + (mode === "giveaway" ? "Giveaway published." : n ? `Listing published. ${n} compatible match${n === 1 ? "" : "es"} found.` : "Listing published. No compatible match yet."), true);
    $("#interpret-text").value = ""; $("#interpret-notice").textContent = ""; await load();
    const lid = r.listing?.id || id; if (mode === "exchange" && lid) openFallback(lid);
  } catch (er) {
    approval = null; reviewState = {kind:"error", message:"This approval can no longer be used. Check again with Bob."};
    setNotice(notice, `${er.message} Your entries are kept.`); if (er.status === 409) load();
  } finally { publishing = false; renderReview(); }
});

/* Editor state */
function resetEditor(discard = true) {
  uploadGen++; if (discard) discardPhoto(photo); else if (photo?.local_url) URL.revokeObjectURL(photo.local_url); photo = null; photoBusy = false; editingId = null;
  resetListingForm(); $("#photo-status").textContent = ""; $("#edit-banner").hidden = true; $("#cancel-edit").hidden = true; $("#listing-form-title").textContent = "Create a listing";
  renderPhoto(); invalidateApproval(false);
}
function editListing(l, over = {}) {
  if (!sessionUser) return openAuth();
  uploadGen++; discardPhoto(photo); notice.hidden = true; form.reset(); $("#item-rows").innerHTML = "";
  const items = itemsOf(l).slice(0, 5); $("#listing-type").value = items.length > 1 ? "bundle" : "single";
  (items.length ? items : [{}]).forEach(itemRow);
  $("#listing-mode").value = over.mode || l.mode || "exchange"; setMode();
  form.wanted_food.value = l.wanted_food ? foldName(l.wanted_food, l.wanted_unit, l.wanted_quantity) : "";
  form.location.value = l.location || ""; updateSpotCounters();
  form.pickup_start.value = toLocalInput(l.pickup_start); form.pickup_end.value = toLocalInput(l.pickup_end); form.deadline.value = toLocalInput(l.deadline);
  form.allergens.value = l.allergens || ""; renderAllergenChips();
  photo = l.photo_id ? {id:l.photo_id, owned:false} : null; photoBusy = false; editingId = l.id;
  $("#photo-status").textContent = photo ? "Using the saved photo. Replace it if the food has changed." : "This listing has no photo yet. Add one to publish.";
  $("#listing-form-title").textContent = "Edit listing"; $("#cancel-edit").hidden = false;
  $("#edit-banner").hidden = false; $("#edit-banner").textContent = `Editing your existing listing${over.mode === "giveaway" ? " as a giveaway" : ""}. Reconfirm condition and storage, check with Bob, then publish to update it. Nothing changes until you publish.`;
  renumber(); renderPhoto(); invalidateApproval(false);
  $("#create").scrollIntoView({behavior:"smooth", block:"start"});
}
$("#cancel-edit").onclick = () => { resetEditor(true); notice.hidden = true; showToast("Editing cancelled. The saved listing is unchanged."); };

/* Standalone request */
$("#request-form").addEventListener("submit", async e => {
  e.preventDefault(); if (!sessionUser) return openAuth(); const f = e.currentTarget, m = $("#request-notice");
  const q = f.quantity.value.trim() ? parseQty(f.quantity.value) : null, ps = toUtcIso(f.pickup_start.value), pe = toUtcIso(f.pickup_end.value), dl = toUtcIso(f.deadline.value);
  if (!f.item.value.trim()) return setNotice(m, "Enter the food you need.");
  const qe = f.quantity.validity?.badInput ? "Enter a whole quantity from 1 to 99, or leave it blank for any amount." : f.quantity.value.trim() ? qtyError(f.quantity.value, "quantity") : ""; if (qe) return setNotice(m, qe);
  if (spotError(f.location.value)) return setNotice(m, spotError(f.location.value));
  if (!ps || !pe || !dl) return setNotice(m, "Choose a pickup window and needed-by time.");
  const we = windowErrors({start:ps, end:pe, deadline:dl, needDeadline:true, labels:{deadline:"Needed by"}}); if (we.length) return setNotice(m, we.join(" "));
  try { await post("/requests", {items:[f.item.value.trim()], quantity:q, unit:CANON_UNIT, location:f.location.value, pickup_start:ps, pickup_end:pe, deadline:dl, timezone:TZ}); setNotice(m, "Request posted.", true); f.reset(); refreshActivity(); }
  catch (er) { setNotice(m, er.message); }
});

/* Notifications */
async function refreshNotifications() {
  try { const {notifications = []} = await api("/notifications"); $("#notification-count").textContent = `${notifications.filter(n => !n.read).length} NEW`;
    $("#notifications-list").innerHTML = notifications.length ? notifications.slice(0, 8).map(n => `<div class="notification-item"><span class="notification-dot" aria-hidden="true"></span><div><strong>${esc(String(n.kind || "").replaceAll("_", " "))}</strong><p>${esc(n.message)}</p>${n.location ? `<p>${esc(n.location)}${n.pickup_start ? ` &middot; ${esc(windowText(n.pickup_start, n.pickup_end))}` : ""}</p>` : ""}${n.kind === "trade_declined" && isMine(n.entity_id) ? `<button class="mini-btn" data-fallback="${esc(n.entity_id)}">Next steps</button>` : ""}</div></div>`).join("") + `<p class="muted">Act on offers and requests in My handoffs.</p>` : `<p class="muted">No notifications yet.</p>`;
  } catch {}
}

/* Auth */
function setAuthMode(mode) { authMode = mode; const s = mode === "signup"; $("#auth-title").textContent = s ? "Join RePlate" : "Welcome back"; $("#auth-subtitle").textContent = s ? "Create an account to share and find food." : "Sign in to list food and manage your exchanges."; $("#display-name-field").hidden = !s; $("#auth-form [name=display_name]").required = s; $("#auth-form [name=password]").autocomplete = s ? "new-password" : "current-password"; $("#auth-submit").textContent = s ? "Create account" : "Sign in"; $("#login-tab").classList.toggle("selected", !s); $("#signup-tab").classList.toggle("selected", s); }
function clearScoped() { myListings = []; myOffers = []; myRequests = []; offerArea.hidden = true; offerArea.innerHTML = ""; inedible.clear(); if (editingId || (photo && !photo.guestDraft) || approval || reviewing) resetEditor(false); $("#notifications-list").innerHTML = `<p class="muted">No notifications yet.</p>`; $("#notification-count").textContent = "0 NEW"; }
function renderSession() { const name = sessionUser?.display_name || "Guest"; $("#profile-name").textContent = name; $("#profile-avatar").textContent = name.charAt(0).toUpperCase(); $("#auth-button").hidden = !!sessionUser; $("#logout-button").hidden = !sessionUser; $("#delete-account").hidden = !sessionUser; $("#listing-auth-help").hidden = !!sessionUser; renderReview(); $("#request-auth-help").hidden = !!sessionUser; if (!sessionUser) clearScoped(); renderActivity(); renderListings(); refreshImpact(); }
function openAuth(mode = "login") { setAuthMode(mode); $("#auth-notice").hidden = true; $("#auth-dialog").showModal(); }
$("#auth-button").onclick = () => openAuth(); $("#listing-auth-button").onclick = () => openAuth(); $("#request-auth-button").onclick = () => openAuth(); $("#close-auth").onclick = () => $("#auth-dialog").close(); $("#login-tab").onclick = () => setAuthMode("login"); $("#signup-tab").onclick = () => setAuthMode("signup");
$("#auth-form").addEventListener("submit", async e => {
  e.preventDefault(); const f = e.currentTarget, v = new FormData(f), s = authMode === "signup", m = $("#auth-notice"), b = $("#auth-submit"); m.hidden = true; b.disabled = true;
  try { const r = await post(`/auth/${s ? "signup" : "login"}`, s ? {display_name:v.get("display_name"), email:v.get("email"), password:v.get("password")} : {email:v.get("email"), password:v.get("password")});
    if (r.email_confirmation_required) return setNotice(m, "Check your email to confirm your account, then sign in.", true);
    sessionUser = r.user; if (photo?.file) photo.guestDraft = false; $("#auth-dialog").close(); f.reset(); renderSession(); if (photo?.file) uploadSelectedPhoto(); await load(); showToast(s ? "Welcome to RePlate." : "You are signed in.");
  } catch (er) { setNotice(m, er.message); } finally { b.disabled = false; }
});
$("#logout-button").onclick = async () => { resetEditor(true); try { await post("/auth/logout"); } catch {} sessionUser = null; renderSession(); await load(); showToast("Signed out."); };
$("#delete-account").onclick = async () => { if (!confirm("Delete your account? This deactivates sign-in and hides your active listings.")) return; try { await api("/auth/account", {method:"DELETE"}); sessionUser = null; renderSession(); await load(); showToast("Account deactivated."); } catch (e) { showToast(e.message); } };

// Keep the image action visible from the first screen, and show the uploader
// before the longer food-details fields when the publishing form opens.
const photoField = $("#photo-input").closest(".photo-field");
$("#edit-banner").after(photoField);
const postPhotoLink = document.createElement("a");
postPhotoLink.href = "#create";
postPhotoLink.className = "post-photo-nav";
postPhotoLink.textContent = "Post with photo";
document.querySelector(".topbar nav").append(postPhotoLink);
const heroPostLink = document.querySelector('.welcome a[href="#create"]');
heroPostLink.textContent = "Post food with photo";
function startPhotoPost(e) {
  e.preventDefault();
  photoField.scrollIntoView({behavior:"instant", block:"start"});
  $("#photo-input").click();
}
postPhotoLink.addEventListener("click", startPhotoPost);
heroPostLink.addEventListener("click", startPhotoPost);

async function initialize() {
  resetEditor(false); renderSession(); checkHealth();
  try { sessionUser = (await api("/auth/me")).user || null; } catch { sessionUser = null; }
  renderSession(); await load();
}
initialize();
setInterval(() => { if (!document.hidden) load(); }, 15000);
