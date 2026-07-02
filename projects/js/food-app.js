const elements = {
  category: document.getElementById("category-filter"),
  sort: document.getElementById("sort-select"),
  search: document.getElementById("search-input"),
  resultsBody: document.getElementById("results-body"),
  summaryCount: document.getElementById("summary-count"),
  summaryCalories: document.getElementById("summary-calories"),
  summaryProtein: document.getElementById("summary-protein"),
  summaryCarbs: document.getElementById("summary-carbs"),
  demoStatus: document.getElementById("demo-status")
};

const FOOD_CSV_URLS = [
  "https://raw.githubusercontent.com/Ethan004-Code/Food_nutrition/refs/heads/main/FOOD-DATA-GROUP1.csv",
  "https://raw.githubusercontent.com/Ethan004-Code/Food_nutrition/refs/heads/main/FOOD-DATA-GROUP2.csv",
  "https://raw.githubusercontent.com/Ethan004-Code/Food_nutrition/refs/heads/main/FOOD-DATA-GROUP3.csv",
  "https://raw.githubusercontent.com/Ethan004-Code/Food_nutrition/refs/heads/main/FOOD-DATA-GROUP4.csv",
  "https://raw.githubusercontent.com/Ethan004-Code/Food_nutrition/refs/heads/main/FOOD-DATA-GROUP5.csv"
];

let foodData = [];

function showStatus(message) {
  if (elements.demoStatus) {
    elements.demoStatus.textContent = message;
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function cleanKey(key) {
  return String(key)
    .trim()
    .toLowerCase()
    .replace(/\uFEFF/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function parseCsvLine(line) {
  const values = [];
  let current = "";
  let inQuotes = false;

  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];

    if (char === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (char === "," && !inQuotes) {
      values.push(current);
      current = "";
    } else {
      current += char;
    }
  }

  values.push(current);
  return values;
}

function parseCsv(text) {
  const lines = text
    .trim()
    .split(/\r?\n/)
    .filter(line => line.trim().length > 0);

  if (!lines.length) return [];

  const headers = parseCsvLine(lines[0]).map(header => cleanKey(header));

  console.log("CSV HEADERS:", headers);

  return lines.slice(1).map(line => {
    const values = parseCsvLine(line);
    const row = {};

    headers.forEach((header, index) => {
      row[header] = values[index] ? values[index].trim() : "";
    });

    return row;
  });
}

function getValue(row, possibleKeys) {
  for (const key of possibleKeys) {
    const cleaned = cleanKey(key);

    if (row[cleaned] !== undefined && row[cleaned] !== "") {
      return row[cleaned];
    }
  }

  return "";
}

function parseNumber(value) {
  const cleaned = String(value ?? "").replace(/[^\d.-]/g, "");
  const number = Number(cleaned);
  return Number.isFinite(number) ? number : 0;
}

function inferCategory(name) {
  const lower = String(name).toLowerCase();

  if (
    lower.includes("chicken") ||
    lower.includes("beef") ||
    lower.includes("pork") ||
    lower.includes("fish") ||
    lower.includes("salmon") ||
    lower.includes("egg") ||
    lower.includes("turkey")
  ) {
    return "Protein";
  }

  if (
    lower.includes("apple") ||
    lower.includes("banana") ||
    lower.includes("orange") ||
    lower.includes("berry") ||
    lower.includes("mango") ||
    lower.includes("fruit")
  ) {
    return "Fruit";
  }

  if (
    lower.includes("rice") ||
    lower.includes("bread") ||
    lower.includes("oat") ||
    lower.includes("pasta") ||
    lower.includes("cereal") ||
    lower.includes("wheat")
  ) {
    return "Grain";
  }

  if (
    lower.includes("broccoli") ||
    lower.includes("spinach") ||
    lower.includes("carrot") ||
    lower.includes("lettuce") ||
    lower.includes("vegetable")
  ) {
    return "Vegetable";
  }

  if (
    lower.includes("milk") ||
    lower.includes("cheese") ||
    lower.includes("yogurt")
  ) {
    return "Dairy";
  }

  return "Other";
}

function normalizeFoodRecord(row) {
  const name = getValue(row, [
    "food",
    "Food",
    "food_name",
    "Food Name",
    "name",
    "Name",
    "item",
    "Item",
    "description",
    "Description"
  ]);

  const calories = parseNumber(
    getValue(row, [
      "Caloric Value",
      "caloric_value",
      "calories",
      "Calories",
      "calorie",
      "Energy",
      "energy_kcal",
      "kcal"
    ])
  );

  const protein = parseNumber(
    getValue(row, [
      "Protein",
      "protein",
      "protein_g",
      "Protein (g)"
    ])
  );

  const carbs = parseNumber(
    getValue(row, [
      "Carbohydrates",
      "carbohydrates",
      "carbs",
      "Carbs",
      "carbohydrate",
      "carbohydrate_g",
      "Carbohydrate (g)"
    ])
  );

  const fat = parseNumber(
    getValue(row, [
      "Fat",
      "fat",
      "total_fat",
      "Total Fat",
      "fat_g",
      "Fat (g)"
    ])
  );

  const category =
    getValue(row, [
      "Category",
      "category",
      "food_group",
      "Food Group",
      "group",
      "Group"
    ]) || inferCategory(name);

  return {
    name,
    category,
    calories,
    protein,
    carbs,
    fat
  };
}

function populateSelect(selectElement, values) {
  if (!selectElement) return;

  const uniqueValues = [...new Set(values.filter(Boolean))].sort((a, b) =>
    String(a).localeCompare(String(b))
  );

  selectElement.innerHTML =
    '<option value="">All</option>' +
    uniqueValues
      .map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`)
      .join("");
}

function formatNumber(value, suffix = "") {
  const number = Number(value);

  if (!Number.isFinite(number)) {
    return `0${suffix}`;
  }

  return `${number.toFixed(number % 1 === 0 ? 0 : 1)}${suffix}`;
}

function updateTable(items) {
  if (!elements.resultsBody) return;

  if (!items.length) {
    elements.resultsBody.innerHTML = `
      <tr>
        <td id="table-empty" colspan="6">
          No matching items found. Try another filter or search term.
        </td>
      </tr>
    `;
    return;
  }

  elements.resultsBody.innerHTML = items
    .slice(0, 300)
    .map(item => {
      return `
        <tr>
          <td>${escapeHtml(item.name)}</td>
          <td>${escapeHtml(item.category)}</td>
          <td>${escapeHtml(formatNumber(item.calories))}</td>
          <td>${escapeHtml(formatNumber(item.protein, "g"))}</td>
          <td>${escapeHtml(formatNumber(item.carbs, "g"))}</td>
          <td>${escapeHtml(formatNumber(item.fat, "g"))}</td>
        </tr>
      `;
    })
    .join("");
}

function average(items, key) {
  if (!items.length) return 0;

  const total = items.reduce((sum, item) => sum + Number(item[key] || 0), 0);
  return total / items.length;
}

function updateSummary(items) {
  if (elements.summaryCount) {
    elements.summaryCount.textContent = items.length;
  }

  if (elements.summaryCalories) {
    elements.summaryCalories.textContent = formatNumber(average(items, "calories"));
  }

  if (elements.summaryProtein) {
    elements.summaryProtein.textContent = formatNumber(average(items, "protein"), "g");
  }

  if (elements.summaryCarbs) {
    elements.summaryCarbs.textContent = formatNumber(average(items, "carbs"), "g");
  }
}

function applyFilters() {
  const categoryValue = elements.category ? elements.category.value : "";
  const sortValue = elements.sort ? elements.sort.value : "name";
  const searchValue = elements.search
    ? elements.search.value.trim().toLowerCase()
    : "";

  let filteredItems = foodData.filter(item => {
    const matchesCategory = !categoryValue || item.category === categoryValue;
    const matchesSearch =
      !searchValue || item.name.toLowerCase().includes(searchValue);

    return matchesCategory && matchesSearch;
  });

  filteredItems = filteredItems.slice().sort((a, b) => {
    if (sortValue === "name") {
      return a.name.localeCompare(b.name);
    }

    return Number(a[sortValue] || 0) - Number(b[sortValue] || 0);
  });

  updateTable(filteredItems);
  updateSummary(filteredItems);

  showStatus(
    `Showing ${filteredItems.length} food item${filteredItems.length === 1 ? "" : "s"} from GitHub nutrition CSV data.`
  );
}

function bindEvents() {
  [elements.category, elements.sort, elements.search].forEach(input => {
    if (!input) return;

    input.addEventListener("change", applyFilters);

    if (input === elements.search) {
      input.addEventListener("input", applyFilters);
    }
  });
}

function removeDuplicateFoods(items) {
  const seen = new Set();

  return items.filter(item => {
    const key = item.name.toLowerCase();

    if (!key || seen.has(key)) {
      return false;
    }

    seen.add(key);
    return true;
  });
}

async function loadFoodDataFromGitHub() {
  showStatus("Loading full Food Nutrition CSV data from GitHub...");

  const csvTexts = await Promise.all(
    FOOD_CSV_URLS.map(url =>
      fetch(url).then(response => {
        if (!response.ok) {
          throw new Error(`Could not load ${url}`);
        }

        return response.text();
      })
    )
  );

  const allRows = csvTexts.flatMap(csvText => parseCsv(csvText));

  console.log("RAW ROW COUNT:", allRows.length);
  console.log("FIRST RAW ROW:", allRows[0]);

  foodData = removeDuplicateFoods(
    allRows
      .map(normalizeFoodRecord)
      .filter(item => item.name)
  );

  console.log("NORMALIZED FOOD COUNT:", foodData.length);
  console.log("FIRST NORMALIZED FOOD:", foodData[0]);

  populateSelect(elements.category, foodData.map(item => item.category));
  bindEvents();
  applyFilters();

  showStatus(`Loaded ${foodData.length} food items from GitHub nutrition CSV files.`);
}

loadFoodDataFromGitHub().catch(error => {
  console.error(error);

  showStatus("Could not load Food Nutrition CSV data from GitHub.");

  if (elements.resultsBody) {
    elements.resultsBody.innerHTML = `
      <tr>
        <td id="table-empty" colspan="6">
          Error loading food data from GitHub. Check the GitHub CSV links and internet connection.
        </td>
      </tr>
    `;
  }
});