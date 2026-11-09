export const ALLERGENS = ["None", "Unknown", "Wheat/Gluten", "Milk", "Eggs", "Peanuts", "Tree nuts", "Soy", "Fish", "Shellfish", "Sesame", "Other"];
const exclusive = value => value === "None" || value === "Unknown";

export function parseAllergens(value = "") {
  const text = String(value).trim();
  if (!text) return { selected: [], other: "", legacy: "" };
  let at = text === "Other:" || text.startsWith("Other: ") ? 0 : text.indexOf(", Other: ");
  if (at < 0 && text.endsWith(", Other:")) at = text.length - 8;
  const other = at >= 0 ? text.slice(at + (at === 0 ? 7 : 9)) : "";
  const selected = (at >= 0 ? text.slice(0, at) : text).split(", ").filter(Boolean);
  if (at >= 0) selected.push("Other");
  if (selected.some(v => !ALLERGENS.includes(v)) ||
      (selected.some(exclusive) && selected.length !== 1)) {
    return { selected: [], other: "", legacy: String(value) };
  }
  return { selected, other, legacy: "" };
}

export function allergenValue(selected, other = "") {
  return ALLERGENS.filter(v => selected.includes(v))
    .map(v => v === "Other" && other ? `Other: ${other}` : v).join(", ");
}

export function toggleAllergen(value, choice) {
  if (!ALLERGENS.includes(choice)) return value;
  let { selected, other } = parseAllergens(value);
  if (selected.includes(choice)) selected = selected.filter(v => v !== choice);
  else if (exclusive(choice)) selected = [choice];
  else selected = [...selected.filter(v => !exclusive(v)), choice];
  return allergenValue(selected, other);
}

export function allergenError(value) {
  const { selected, other } = parseAllergens(value);
  if (!selected.length) return "Select at least one allergen chip (None or Unknown is okay).";
  if (selected.includes("Other") && !other.trim()) return "Describe the other allergen.";
  if (other.length > 50) return "Other allergen must be 50 characters or fewer.";
  return "";
}

export function allergenPayload(value) {
  const { selected, other, legacy } = parseAllergens(value);
  return legacy ? {} : { allergen_choices: selected, allergen_other: other };
}

export function allergenMarkup(value, esc, otherAttrs = "", prefix = "allergen") {
  const { selected, other, legacy } = parseAllergens(value);
  return `<div class="allergen-field" role="group" aria-label="Allergens (required)">
    <p class="allergen-help">Allergens · select at least one</p>
    ${legacy ? `<p class="allergen-help">Existing allergen information: ${esc(legacy)}. Kept unchanged unless you choose chips below.</p>` : ""}
    <div class="allergen-chips">${ALLERGENS.map(v => `<button type="button" class="allergen-chip" data-action="allergen-toggle" data-value="${v}" aria-pressed="${selected.includes(v)}">${v}</button>`).join("")}</div>
    ${selected.includes("Other") ? `<label class="allergen-other" for="${prefix}-other">Other allergen<input id="${prefix}-other" type="text" maxlength="50" value="${esc(other)}" placeholder="Describe the other allergen" ${otherAttrs}></label>` : ""}
    </div>`;
}
