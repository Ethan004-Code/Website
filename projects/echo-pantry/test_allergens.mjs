import { test } from "node:test";
import assert from "node:assert/strict";
import { parseAllergens, allergenValue, toggleAllergen, allergenError, allergenPayload } from "./allergens.js";

test("None and Unknown are exclusive; named allergens support multiple selections and toggling", () => {
  let value = toggleAllergen("None", "Milk");
  assert.equal(value, "Milk");
  value = toggleAllergen(value, "Eggs");
  assert.equal(value, "Milk, Eggs");
  assert.equal(toggleAllergen(value, "None"), "None");
  assert.equal(toggleAllergen(value, "Unknown"), "Unknown");
  assert.equal(toggleAllergen("Unknown", "Sesame"), "Sesame");
  assert.equal(toggleAllergen("Milk", "Milk"), "");
  assert.ok(allergenError(""));
});

test("Other requires text, preserves commas and enforces the 50-character boundary", () => {
  for (const text of ["", " "]) assert.ok(allergenError(allergenValue(["Milk", "Other"], text)));
  const text = "Mustard, Other: lupin";
  const value = allergenValue(["Milk", "Other"], text);
  assert.deepEqual(parseAllergens(value), {selected: ["Milk", "Other"], other: text, legacy: ""});
  assert.deepEqual(allergenPayload(value), {allergen_choices: ["Milk", "Other"], allergen_other: text});
  assert.equal(allergenError(allergenValue(["Other"], "a".repeat(50))), "");
  assert.ok(allergenError(allergenValue(["Other"], "a".repeat(51))));
  assert.ok(allergenError("Other:  "));
});

test("old arbitrary declarations remain unmodified until a chip is chosen", () => {
  const old = "Contains lupin; may contain milk & nuts.";
  assert.equal(parseAllergens(old).legacy, old);
  assert.deepEqual(allergenPayload(old), {});
  assert.ok(allergenError(old)); // Editing requires a chip; viewing keeps the old text.
  assert.equal(toggleAllergen(old, "Unknown"), "Unknown");
});
