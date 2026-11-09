/* RePlate: shared quantity, unit-folding and date-range rules for the share,
   giveaway and request forms. Pure functions so they can be tested in Node. */
const pad = n => String(n).padStart(2, "0");

export const QTY_MIN = 1, QTY_MAX = 99;
export const ITEM_PLACEHOLDER = "e.g. onions, bags of rice, cans of beans";
export const CANON_UNIT = "each";
export const SPOT_MAX = 100;
export const SPOT_PLACEHOLDER = "e.g. Front steps of the main library";
export const SPOT_NOTE = "Meet at a public spot. Never share your home address.";
export function spotError(value) {
  if (!String(value ?? "").trim()) return "Enter a public pickup spot.";
  if (String(value).length > SPOT_MAX) return "Pickup spot must be 100 characters or fewer.";
  return "";
}

const PLURAL = { bag: "bags", can: "cans", box: "boxes", bottle: "bottles", package: "packages", jar: "jars", carton: "cartons" };

/* Fold a legacy unit into the food name so "rice, 3 bag" reads "3 bags of rice". */
export function foldName(name, unit, quantity) {
  const n = String(name ?? "").trim();
  const u = String(unit ?? "").trim();
  if (!u || u === CANON_UNIT) return n;
  const word = Number(quantity) === 1 ? u : (PLURAL[u] || u);
  if (!n) return word;
  for (const label of [u, PLURAL[u]].filter(Boolean)) {
    if (n.toLowerCase().startsWith(label.toLowerCase() + " of "))
      return `${word} of ${n.slice(label.length + 4)}`;
  }
  return `${word} of ${n}`;
}

export function parseQty(v) {
  const s = String(v ?? "").trim();
  if (!/^\d+$/.test(s)) return NaN;
  return Number(s);
}
export function qtyError(v, label = "amount") {
  const q = parseQty(v);
  if (!Number.isInteger(q) || q < QTY_MIN || q > QTY_MAX) return `Use a whole-number ${label} from ${QTY_MIN} to ${QTY_MAX}.`;
  return "";
}

export function toLocalInput(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
export function dateInput(d) { return toLocalInput(d).slice(0, 10); }

function calendarYears(d, years, utc = false) {
  const copy = new Date(d);
  const month = utc ? copy.getUTCMonth() : copy.getMonth();
  if (utc) copy.setUTCFullYear(copy.getUTCFullYear() + years);
  else copy.setFullYear(copy.getFullYear() + years);
  if ((utc ? copy.getUTCMonth() : copy.getMonth()) !== month) {
    if (utc) copy.setUTCDate(0); else copy.setDate(0);
  }
  return copy;
}

/* Pickup limits: from now until the same moment one calendar year ahead. */
export function pickupLimits(nowMs = Date.now()) {
  const now = new Date(nowMs);
  const max = calendarYears(now, 1, true);
  // Inputs use minutes. Round UP rather than offering a minute already in the past.
  const minInput = new Date(Math.ceil(nowMs / 60000) * 60000);
  return { min: nowMs, max: max.getTime(), minInput: toLocalInput(minInput), maxInput: toLocalInput(max), minDay: dateInput(now), maxDay: dateInput(max) };
}

/* Printed date limits: two calendar years ago through ten years ahead. */
export function printedLimits(nowMs = Date.now()) {
  const a = calendarYears(new Date(nowMs), -2);
  const b = calendarYears(new Date(nowMs), 10);
  return { min: dateInput(a), max: dateInput(b), today: dateInput(new Date(nowMs)) };
}

export function fmtMoment(ms) {
  return new Date(ms).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}
export function fmtDay(s) {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}
const ms = v => { if (!v) return NaN; const t = new Date(v).getTime(); return t; };

/* Returns an error string when [lo, hi] is not a valid moment for `label`. */
export function inRange(label, value, lo, hi) {
  const t = ms(value);
  if (!Number.isFinite(t)) return "";
  if (t < lo || t > hi) return `${label} must be between ${fmtMoment(lo)} and ${fmtMoment(hi)}.`;
  return "";
}

/* Validate a pickup window (+ optional deadline) against now..1 year,
   and optionally against an outer bound (a listing's pickup window/deadline). */
export function windowErrors({ start, end, deadline, needDeadline = false, bound = null, labels = {} }, nowMs = Date.now()) {
  const L = { start: "Pickup start", end: "Pickup end", deadline: "Available until", ...labels };
  const lim = pickupLimits(nowMs), errs = [];
  const s = ms(start), e = ms(end), d = ms(deadline);
  if (!Number.isFinite(s)) errs.push(`Set the ${L.start.toLowerCase()}.`);
  if (!Number.isFinite(e)) errs.push(`Set the ${L.end.toLowerCase()}.`);
  if (needDeadline && !Number.isFinite(d)) errs.push(`Set "${L.deadline}".`);
  let lo = lim.min, hi = lim.max;
  if (bound) {
    const bs = ms(bound.start), be = Math.min(...[bound.end, bound.deadline].map(ms).filter(Number.isFinite));
    if (Number.isFinite(bs)) lo = Math.max(lo, bs);
    if (Number.isFinite(be)) hi = Math.min(hi, be);
    if (lo > hi) { errs.push("This listing's pickup window has already passed."); return errs; }
  }
  [[L.start, s], [L.end, e]].forEach(([lab, t]) => { const x = inRange(lab, t, lo, hi); if (x) errs.push(x); });
  if (Number.isFinite(d)) { const x = inRange(`"${L.deadline}"`, d, lim.min, lim.max); if (x) errs.push(x); }
  if (Number.isFinite(s) && Number.isFinite(e) && e <= s) errs.push(`${L.end} needs to be after the start.`);
  if (Number.isFinite(e) && Number.isFinite(d) && e > d) errs.push(`"${L.deadline}" can't be earlier than the ${L.end.toLowerCase()}.`);
  return errs;
}

/* Printed date: hard errors and soft warnings. */
export function printedCheck(date, type, nowMs = Date.now()) {
  const errors = [], warnings = [];
  if (!date) return { errors, warnings };
  const p = printedLimits(nowMs);
  const parsed = new Date(`${date}T12:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)
      || !Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date) {
    errors.push("enter a valid printed date."); return { errors, warnings };
  }
  if (date < p.min || date > p.max) { errors.push(`the printed date must be between ${fmtDay(p.min)} and ${fmtDay(p.max)}.`); return { errors, warnings }; }
  if (date < p.today) {
    if (type === "use_by" || type === "expiration") errors.push(`the ${type === "use_by" ? "use-by" : "expiration"} date has passed, so it can't be shared.`);
    else if (type === "best_by") warnings.push("the best-by date has passed. Check quality before sharing.");
  }
  return { errors, warnings };
}
