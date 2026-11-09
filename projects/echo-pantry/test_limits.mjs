import { test } from "node:test";
import assert from "node:assert/strict";
import {
  foldName, qtyError, parseQty, pickupLimits, printedLimits, printedCheck,
  windowErrors, dateInput,
  spotError,
} from "./limits.js";

const now = new Date(2026, 9, 24, 12, 30, 30).getTime();
const hour = 3600000;

test("public pickup spot accepts free text and limits raw input to 100 characters", () => {
  assert.equal(spotError("Front steps of the main library"), "");
  assert.equal(spotError("x".repeat(100)), "");
  for (const value of ["", " ", "x".repeat(101)]) assert.ok(spotError(value));
});

test("quantities are whole numbers 1 through 99", () => {
  for (const v of ["1", "3", "99"]) assert.equal(qtyError(v), "");
  for (const v of ["", "0", "-1", "100", "1.5", "Infinity", "NaN", "1e1", null])
    assert.match(qtyError(v), /whole-number/);
  assert.equal(parseQty("3"), 3);
});

test("legacy units become part of the name without losing their meaning", () => {
  assert.equal(foldName("onions", "each", 3), "onions");
  assert.equal(foldName("rice", "bag", 3), "bags of rice");
  assert.equal(foldName("beans", "can", 1), "can of beans");
  assert.equal(foldName("bags of rice", "bag", 3), "bags of rice");
  assert.equal(foldName("bag of rice", "bag", 3), "bags of rice");
});

test("min is exact now, picker min rounds up rather than permitting past seconds", () => {
  const lim = pickupLimits(now);
  assert.equal(lim.min, now);
  assert.ok(new Date(lim.minInput).getTime() >= now);
  assert.ok(windowErrors({ start: now - 1, end: now + hour }, now).length);
  assert.deepEqual(windowErrors({ start: now, end: now + hour }, now), []);
});

test("pickup windows require all fields and chronological order", () => {
  assert.deepEqual(windowErrors({ start: now + hour, end: now + 2 * hour,
    deadline: now + 3 * hour, needDeadline: true }, now), []);
  for (const input of [
    { start: "", end: now + hour },
    { start: now + hour, end: "" },
    { start: now + hour, end: now + 2 * hour, deadline: "", needDeadline: true },
    { start: now + hour, end: now + hour },
    { start: now + 2 * hour, end: now + hour },
    { start: now + hour, end: now + 3 * hour, deadline: now + 2 * hour },
  ]) assert.ok(windowErrors(input, now).length);
});

test("one-year upper bound includes the boundary, rejects even one millisecond beyond", () => {
  const upper = pickupLimits(now).max;
  assert.deepEqual(windowErrors({ start: now + hour, end: upper }, now), []);
  assert.ok(windowErrors({ start: now + hour, end: upper + 1 }, now).length);
});

test("request must be entirely within listing window and available-until", () => {
  const bound = { start: now + hour, end: now + 4 * hour, deadline: now + 3 * hour };
  assert.deepEqual(windowErrors({ start: now + hour, end: now + 3 * hour, bound }, now), []);
  for (const input of [
    { start: now + hour - 1, end: now + 2 * hour },
    { start: now + hour, end: now + 3 * hour + 1 },
    { start: now - 1, end: now + 2 * hour },
  ]) assert.match(windowErrors({ ...input, bound }, now).join(" "), /between/);
});

test("expired listing requests cannot be submitted", () => {
  assert.match(windowErrors({ start: now + hour, end: now + 2 * hour,
    bound: { start: now - 2 * hour, end: now - hour } }, now).join(" "), /already passed/);
});

test("printed date bounds are inclusive two calendar years back and ten ahead", () => {
  const p = printedLimits(now);
  for (const d of [p.min, p.today, p.max])
    assert.deepEqual(printedCheck(d, "best_by", now).errors, []);
  for (const d of ["2024-10-23", "2036-10-25", "2026-02-30", "2026-99-99"])
    assert.ok(printedCheck(d, "best_by", now).errors.length);
});

test("past best-by warns, past use-by/expiration block, clear is blank", () => {
  const yesterday = dateInput(new Date(now - 86400000));
  const bestBy = printedCheck(yesterday, "best_by", now);
  assert.equal(bestBy.errors.length, 0);
  assert.match(bestBy.warnings.join(" "), /best-by date has passed/);
  for (const type of ["use_by", "expiration"])
    assert.ok(printedCheck(yesterday, type, now).errors.length);
  assert.deepEqual(printedCheck("", "", now), { errors: [], warnings: [] });
});

test("calendar year arithmetic clamps February 29 like the server", () => {
  const leap = Date.parse("2028-02-29T12:00:00Z");
  assert.equal(new Date(pickupLimits(leap).max).toISOString(), "2029-02-28T12:00:00.000Z");
  const localLeap = new Date(2028, 1, 29, 12).getTime();
  assert.equal(printedLimits(localLeap).min, "2026-02-28");
  assert.equal(printedLimits(localLeap).max, "2038-02-28");
});
