const filters = {
  country: document.getElementById("country-filter"),
  sport: document.getElementById("sport-filter"),
  year: document.getElementById("year-filter"),
  medal: document.getElementById("medal-filter"),
  search: document.getElementById("search-input")
};

const resultsBody = document.getElementById("results-body");
const summaryTotal = document.getElementById("summary-total");
const summaryGold = document.getElementById("summary-gold");
const summarySilver = document.getElementById("summary-silver");
const summaryBronze = document.getElementById("summary-bronze");

const demoWarning = document.getElementById("demo-warning");
const demoStatus = document.getElementById("demo-status");

const CSV_URL =
  "https://raw.githubusercontent.com/Ethan004-Code/Olympic_Stats/main/Summer-Olympic-medals-1976-to-2008.csv";

let records = [];

function showWarning(message) {
  if (demoWarning) {
    demoWarning.textContent = message;
  }
}

function showStatus(message) {
  if (demoStatus) {
    demoStatus.textContent = message;
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
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

  const headers = parseCsvLine(lines[0]).map(header =>
    header.trim().toLowerCase()
  );

  return lines.slice(1).map(line => {
    const values = parseCsvLine(line);
    const row = {};

    headers.forEach((header, index) => {
      row[header] = values[index] ? values[index].trim() : "";
    });

    return row;
  });
}

function normalizeRecord(row) {
  return {
    city: row.city || "",
    year: (row.year || "").replace(".0", ""),
    sport: row.sport || "",
    discipline: row.discipline || "",
    event: row.event || "",
    athlete: row.athlete || "",
    gender: row.gender || "",
    country_code: row.country_code || "",
    country: row.country || "",
    event_gender: row.event_gender || "",
    medal: row.medal || ""
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

function buildFilterOptions() {
  populateSelect(filters.country, records.map(record => record.country));
  populateSelect(filters.sport, records.map(record => record.sport));
  populateSelect(filters.year, records.map(record => record.year));
  populateSelect(filters.medal, records.map(record => record.medal));
}

function matchesFilter(value, filterValue) {
  if (!filterValue) return true;
  return String(value).toLowerCase() === String(filterValue).toLowerCase();
}

function updateSummary(results) {
  if (summaryTotal) {
    summaryTotal.textContent = results.length;
  }

  if (summaryGold) {
    summaryGold.textContent = results.filter(
      record => record.medal.toLowerCase() === "gold"
    ).length;
  }

  if (summarySilver) {
    summarySilver.textContent = results.filter(
      record => record.medal.toLowerCase() === "silver"
    ).length;
  }

  if (summaryBronze) {
    summaryBronze.textContent = results.filter(
      record => record.medal.toLowerCase() === "bronze"
    ).length;
  }
}

function updateTable(results) {
  if (!resultsBody) return;

  if (!results.length) {
    resultsBody.innerHTML = `
      <tr>
        <td id="table-empty" colspan="6">
          No matching results found. Try another filter or search term.
        </td>
      </tr>
    `;
    return;
  }

  resultsBody.innerHTML = results
    .slice(0, 200)
    .map(record => {
      return `
        <tr>
          <td>${escapeHtml(record.year)}</td>
          <td>${escapeHtml(record.sport)}</td>
          <td>${escapeHtml(record.event)}</td>
          <td>${escapeHtml(record.athlete)}</td>
          <td>${escapeHtml(record.country)}</td>
          <td>${escapeHtml(record.medal || "N/A")}</td>
        </tr>
      `;
    })
    .join("");
}

function applyFilters() {
  const countryValue = filters.country ? filters.country.value : "";
  const sportValue = filters.sport ? filters.sport.value : "";
  const yearValue = filters.year ? filters.year.value : "";
  const medalValue = filters.medal ? filters.medal.value : "";
  const searchValue = filters.search
    ? filters.search.value.trim().toLowerCase()
    : "";

  const filteredRecords = records.filter(record => {
    const matchesSearch =
      !searchValue || record.athlete.toLowerCase().includes(searchValue);

    return (
      matchesFilter(record.country, countryValue) &&
      matchesFilter(record.sport, sportValue) &&
      matchesFilter(record.year, yearValue) &&
      matchesFilter(record.medal, medalValue) &&
      matchesSearch
    );
  });

  updateSummary(filteredRecords);
  updateTable(filteredRecords);
}

function bindEvents() {
  Object.values(filters).forEach(input => {
    if (!input) return;

    input.addEventListener("change", applyFilters);

    if (input.id === "search-input") {
      input.addEventListener("input", applyFilters);
    }
  });
}

function initialize() {
  showStatus("Loading full Olympics dataset from GitHub...");
  showWarning("");

  fetch(CSV_URL)
    .then(response => {
      if (!response.ok) {
        throw new Error("Could not load CSV from GitHub.");
      }

      return response.text();
    })
    .then(csvText => {
      records = parseCsv(csvText).map(normalizeRecord);

      buildFilterOptions();
      bindEvents();
      applyFilters();

      showStatus(`Loaded ${records.length} Olympic medal records from GitHub.`);
    })
    .catch(error => {
      console.error(error);

      showWarning(
        "Could not load the Olympics CSV from GitHub. Check your internet connection and make sure the GitHub repository/file is public."
      );

      showStatus("Dataset failed to load.");

      if (resultsBody) {
        resultsBody.innerHTML = `
          <tr>
            <td id="table-empty" colspan="6">
              Error loading data from GitHub.
            </td>
          </tr>
        `;
      }
    });
}

initialize();