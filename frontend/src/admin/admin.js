/**
 * The System Administrator's analytics page (#/admin/<tab>).
 *
 * Everything comes from one call, `admin/analytics/`, which only an
 * administrator may make: the model's evaluation as its training run wrote
 * it, its scores against satellite-mapped floods, and the pipeline's health
 * worked out from the database now, and how the system does in use: its
 * forecasts against what came, its alerts against KMD's, the alert service,
 * and the rainfall and quota behind it. The page says nothing the API did not.
 *
 * Every chart has its numbers in a table under it, and every status carries
 * a word as well as a colour.
 */

import { fetchAnalytics } from "../api.js";
import { CATEGORY_ORDER, colourFor, labelFor } from "../risk.js";
import { BAR, isDark, makeChart, palette } from "./charts.js";
import {
  BAND_LABELS,
  STATUS_LABELS,
  confusion,
  count,
  countyErrors,
  day,
  duration,
  featureLabel,
  fixed,
  foldLabel,
  forecastGain,
  monthName,
  ofNormal,
  percent,
  shortDay,
  tabFromSlug,
  trustedLead,
  yearList,
} from "./format.js";
import "./admin.css";

export const TABS = [
  { id: "overview", label: "Overview" },
  { id: "evaluation", label: "Model evaluation" },
  { id: "drivers", label: "What drives it" },
  { id: "errors", label: "Where it fails" },
  { id: "forecast", label: "Forecast & KMD" },
  { id: "satellite", label: "Satellite check" },
  { id: "alerts", label: "Alerts" },
  { id: "health", label: "System health" },
  { id: "provenance", label: "Provenance" },
];

const WARNING_FROM = { moderate: "Moderate or above", high: "High or above", very_high: "Very high" };

let charts = [];
let analytics = null;
let lastRender = null;
let lastTab = null;

// --- Small DOM helpers ------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function clearCharts() {
  for (const chart of charts) chart.destroy();
  charts = [];
}

function card(parent, title, subtitle, { wide = false } = {}) {
  const node = el("section", `admin-card${wide ? " admin-card--wide" : ""}`);
  const head = el("header", "admin-card__head");
  head.append(el("h3", "admin-card__title", title));
  if (subtitle) head.append(el("p", "admin-card__subtitle", subtitle));
  node.append(head);
  parent.append(node);
  return node;
}

function grid(parent, className = "admin-grid") {
  const node = el("div", className);
  parent.append(node);
  return node;
}

function tile(parent, label, value, note) {
  const node = el("div", "admin-tile");
  node.append(el("p", "admin-tile__label", label), el("p", "admin-tile__value", value));
  if (note) node.append(el("p", "admin-tile__note", note));
  parent.append(node);
  return node;
}

function callout(parent, title, text, kind = "info") {
  const node = el("div", `admin-callout admin-callout--${kind}`);
  if (title) node.append(el("p", "admin-callout__title", title));
  node.append(el("p", "admin-callout__text", text));
  parent.append(node);
  return node;
}

function chart(parent, config, height = 260) {
  const wrap = el("div", "admin-chart");
  wrap.style.height = `${height}px`;
  const canvas = document.createElement("canvas");
  wrap.append(canvas);
  parent.append(wrap);
  charts.push(makeChart(canvas, config));
  return wrap;
}

/** The numbers behind a chart, folded away under it. */
function numbers(parent, headers, rows, summary = "Show the numbers") {
  const details = el("details", "admin-numbers");
  details.append(el("summary", "admin-numbers__toggle", summary));
  details.append(table(headers, rows));
  parent.append(details);
  return details;
}

function table(headers, rows) {
  const node = el("table", "admin-table");
  const head = el("thead");
  const headRow = el("tr");
  for (const header of headers) headRow.append(el("th", null, header));
  head.append(headRow);
  const body = el("tbody");
  for (const row of rows) {
    const tr = el("tr");
    for (const cell of row) {
      const td = el("td");
      if (cell instanceof Node) td.append(cell);
      else td.textContent = cell;
      tr.append(td);
    }
    body.append(tr);
  }
  node.append(head, body);
  return node;
}

function statusPill(status) {
  const node = el("span", `admin-status admin-status--${status}`);
  node.append(el("span", "admin-status__dot"), el("span", null, STATUS_LABELS[status] ?? status));
  return node;
}

/** A thin horizontal meter for a share, with its value as text beside it. */
function meter(share, text) {
  const node = el("span", "admin-meter");
  const track = el("span", "admin-meter__track");
  const fill = el("span", "admin-meter__fill");
  fill.style.width = `${Math.max(0, Math.min(1, share ?? 0)) * 100}%`;
  track.append(fill);
  node.append(track, el("span", "admin-meter__text", text));
  return node;
}

/** One stacked bar of the four risk categories, with a legend row. */
function categoryBar(parent, label, counts) {
  const total = CATEGORY_ORDER.reduce((sum, category) => sum + (counts?.[category] ?? 0), 0);
  const row = el("div", "admin-stack");
  const head = el("div", "admin-stack__head");
  head.append(el("span", "admin-stack__label", label), el("span", "admin-stack__total", `${count(total)} scored`));
  const bar = el("div", "admin-stack__bar");
  bar.setAttribute("role", "img");
  bar.setAttribute(
    "aria-label",
    `${label}: ${CATEGORY_ORDER.map((c) => `${counts?.[c] ?? 0} ${labelFor(c)}`).join(", ")}`,
  );
  const legend = el("div", "admin-stack__legend");
  for (const category of CATEGORY_ORDER) {
    const n = counts?.[category] ?? 0;
    if (total && n) {
      const segment = el("span", "admin-stack__segment");
      segment.style.flexGrow = String(n);
      segment.style.background = colourFor(category, isDark());
      segment.title = `${labelFor(category)}: ${n}`;
      bar.append(segment);
    }
    const key = el("span", "admin-stack__key");
    const swatch = el("span", "admin-stack__swatch");
    swatch.style.background = colourFor(category, isDark());
    key.append(swatch, el("span", null, `${labelFor(category)} ${n}`));
    legend.append(key);
  }
  if (!total) bar.append(el("span", "admin-stack__empty", "No scores"));
  row.append(head, bar, legend);
  parent.append(row);
}

// --- Charts shared by several tabs -------------------------------------------

function trendChart(parent, trend) {
  if (!trend.length) {
    parent.append(el("p", "admin-empty", "No scores stored yet."));
    return;
  }
  const colours = palette();
  chart(
    parent,
    {
      type: "bar",
      label: "County risk categories per day over the last 30 days with scores",
      data: {
        labels: trend.map((row) => day(row.date).replace(/ \d{4}$/, "")),
        datasets: CATEGORY_ORDER.map((category) => ({
          label: labelFor(category),
          data: trend.map((row) => row[category]),
          backgroundColor: colourFor(category, isDark()),
          borderColor: colours.surface,
          borderWidth: { top: 1 },
          borderSkipped: false,
          maxBarThickness: 18,
        })),
      },
      options: {
        interaction: { mode: "index", intersect: false },
        plugins: {
          tooltip: {
            callbacks: {
              title: (items) => day(trend[items[0].dataIndex].date),
              footer: (items) => (trend[items[0].dataIndex].model_versions ?? []).join(", "),
            },
          },
        },
        scales: {
          x: { stacked: true, grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 12 } },
          y: { stacked: true, title: { display: true, text: "Counties" }, ticks: { precision: 0 } },
        },
        plugins: { legend: { position: "bottom" } },
      },
    },
    240,
  );
  // A change of model moves the whole map at once, so bars from different
  // models are not comparable. Say which models scored which days.
  const daysByVersion = new Map();
  for (const row of trend) {
    for (const version of row.model_versions ?? []) {
      daysByVersion.set(version, (daysByVersion.get(version) ?? 0) + 1);
    }
  }
  if (daysByVersion.size > 1) {
    const list = [...daysByVersion]
      .sort((a, b) => b[1] - a[1])
      .map(([version, n]) => `${version} (${n} day${n === 1 ? "" : "s"})`)
      .join(", ");
    parent.append(
      el(
        "p",
        "admin-note",
        `These days were scored by ${daysByVersion.size} different models: ${list}. A jump between days scored by different models is the model changing, not the weather. Hover a bar to see which model scored it.`,
      ),
    );
  }
  numbers(
    parent,
    ["Day", ...CATEGORY_ORDER.map(labelFor), "Model"],
    trend.map((row) => [day(row.date), ...CATEGORY_ORDER.map((c) => count(row[c])), (row.model_versions ?? []).join(", ")]),
  );
}

function comparisonChart(parent, rows, metric, { label, digits = 3, max } = {}) {
  const colours = palette();
  chart(
    parent,
    {
      type: "bar",
      label,
      data: {
        labels: rows.map((row) => (row.baseline ? `${row.label} (baseline)` : row.label)),
        datasets: [
          {
            label,
            data: rows.map((row) => row[metric]),
            backgroundColor: rows.map((row) =>
              row.selected ? colours.primary : row.baseline ? colours.faint : colours.muted,
            ),
            valueLabels: (value) => fixed(value, digits),
            ...BAR,
          },
        ],
      },
      options: {
        indexAxis: "y",
        layout: { padding: { right: 44 } },
        plugins: { legend: { display: false } },
        scales: {
          x: { min: 0, max, title: { display: true, text: label } },
          y: { grid: { display: false } },
        },
      },
    },
    40 + rows.length * 38,
  );
}

// --- Tabs -----------------------------------------------------------------------

function selectedRow(model) {
  return model.comparison.find((row) => row.selected);
}

function renderOverview(body, { model, satellite_check: satellite, health }) {
  if (health.status !== "ok") {
    const failing = health.checks.filter((check) => check.status !== "ok");
    callout(
      body,
      `System ${STATUS_LABELS[health.status].toLowerCase()}`,
      failing.map((check) => `${check.name}: ${check.detail}`).join(" "),
      health.status === "degraded" ? "danger" : "warning",
    );
  }

  if (!model) {
    callout(
      body,
      "No trained model is loaded",
      "Scores come from the placeholder rule, which was never evaluated, so there is no model performance to show. Train the model on Colab and ship its artifacts.",
      "warning",
    );
  } else {
    const chosen = selectedRow(model);
    const baseRate = model.comparison.find((row) => row.key === "base-rate");
    const test = model.final_test;
    const testChosen = test.comparison.find((row) => row.selected);
    const tiles = grid(body, "admin-tiles");
    tile(tiles, "ROC AUC", fixed(chosen.roc_auc), "Cross-validated by year. 0.5 is guessing, 1 is perfect.");
    tile(
      tiles,
      "Average precision",
      fixed(chosen.average_precision),
      `${model.lift_over_base_rate}× what guessing the usual ${percent(model.training.base_rate)} chance scores`,
    );
    tile(
      tiles,
      "Brier score",
      fixed(chosen.brier, 4),
      baseRate ? `Lower is better. Always guessing the usual chance scores ${fixed(baseRate.brier, 4)}.` : "Lower is better.",
    );
    tile(
      tiles,
      `Held-out ${test.years[0]}–${test.years[1]}`,
      fixed(testChosen.roc_auc),
      `ROC AUC on years never used to choose or train`,
    );
    tile(
      tiles,
      "Training data",
      count(model.training.rows),
      `county-months, ${count(model.training.floods)} with a large flood, ${model.training.period[0].slice(0, 4)}–${model.training.period[1].slice(0, 4)}`,
    );
    if (satellite?.length) {
      tile(
        tiles,
        "Satellite check",
        satellite.map((event) => fixed(event.roc_auc, 2)).join(" · "),
        `ROC AUC against UNOSAT flood maps, ${satellite.map((event) => day(event.date).slice(-8)).join(" and ")}`,
      );
    }

    callout(
      body,
      "How to read these",
      `Each score is the chance that a large flood is recorded in the county within 30 days. ROC AUC ${fixed(chosen.roc_auc, 2)} means that, on years the model never trained on, a month that flooded scored higher than one that did not ${percent(chosen.roc_auc, 0)} of the time. Floods are rare (${percent(model.training.base_rate)} of county-months), so average precision is the headline: it would be ${fixed(model.training.base_rate)} for a model that only knew the usual chance.`,
    );
  }

  const row = grid(body);
  const today = card(row, "On the map now", `Scores for ${day(health.latest_scores.date)}`);
  categoryBar(today, "Counties", health.latest_scores.categories.county);
  categoryBar(today, "Sub-counties", health.latest_scores.categories.sub_county);

  const status = card(row, "Pipeline", health.rules);
  const list = el("ul", "admin-checks");
  for (const check of health.checks) {
    const item = el("li", "admin-checks__item");
    item.append(statusPill(check.status), el("span", "admin-checks__name", check.name), el("span", "admin-checks__detail", check.detail));
    list.append(item);
  }
  status.append(list);

  const trend = card(body, "Counties by category, last 30 days", "How the map has moved day by day", { wide: true });
  trendChart(trend, health.trend);
}

function renderEvaluation(body, { model }) {
  if (!model) return renderNoModel(body);
  const colours = palette();
  const chosen = selectedRow(model);

  callout(
    body,
    "How the model was chosen",
    `${model.validation}. Three model types were compared with two baselines that need no rainfall at all: always guessing the usual chance, and guessing from the season alone. ${model.selected_label} scored best and is the model in use.`,
  );

  const row = grid(body);
  const ap = card(row, "Average precision", "Higher is better. The headline, because floods are rare.");
  comparisonChart(ap, model.comparison, "average_precision", { label: "Average precision", max: 0.35 });
  const auc = card(row, "ROC AUC", "Higher is better. 0.5 is guessing.");
  comparisonChart(auc, model.comparison, "roc_auc", { label: "ROC AUC", max: 1 });
  numbers(
    ap,
    ["Model", "Average precision", "ROC AUC", "Brier"],
    model.comparison.map((r) => [
      `${r.label}${r.selected ? " (in use)" : ""}${r.baseline ? " (baseline)" : ""}`,
      fixed(r.average_precision),
      fixed(r.roc_auc),
      fixed(r.brier, 4),
    ]),
  );

  // Stability: each held-out group of years.
  const cv = model.cross_validation;
  if (cv.folds.length) {
    const folds = card(
      body,
      "Is it stable across years?",
      "Average precision on each held-out group of years. A model that only looks good on one split would jump around here.",
      { wide: true },
    );
    const models = [...cv.models].sort((a, b) => (a.key === model.selected ? -1 : b.key === model.selected ? 1 : 0));
    chart(
      folds,
      {
        type: "line",
        label: "Average precision per cross-validation fold for each model",
        data: {
          labels: cv.folds.map((fold) => foldLabel(fold.years)),
          datasets: models.map((m, i) => ({
            label: m.key === model.selected ? `${m.label} (in use)` : m.label,
            data: cv.folds.map((fold) => fold.scores[m.key]?.average_precision ?? null),
            showLine: false,
            pointRadius: m.key === model.selected ? 7 : 5,
            pointHoverRadius: 9,
            pointBackgroundColor: colours.series[i % colours.series.length],
            pointBorderColor: colours.surface,
            pointBorderWidth: 2,
            borderColor: colours.series[i % colours.series.length],
          })),
        },
        options: {
          plugins: {
            legend: { position: "bottom" },
            tooltip: {
              callbacks: {
                title: (items) => `Held out: ${yearList(cv.folds[items[0].dataIndex].years)}`,
                label: (item) => `${item.dataset.label}: ${fixed(item.raw)}`,
              },
            },
          },
          scales: {
            x: { grid: { display: false }, title: { display: true, text: "Held-out years (first year +others)" } },
            y: { min: 0, title: { display: true, text: "Average precision" } },
          },
        },
      },
      280,
    );
    numbers(
      folds,
      ["Model", "Average precision (mean ± sd)", "ROC AUC (mean ± sd)"],
      models
        .filter((m) => cv.summary[m.key])
        .map((m) => [
          m.label,
          `${fixed(cv.summary[m.key].average_precision_mean)} ± ${fixed(cv.summary[m.key].average_precision_sd)}`,
          `${fixed(cv.summary[m.key].roc_auc_mean)} ± ${fixed(cv.summary[m.key].roc_auc_sd)}`,
        ]),
    );
    const seeds = model.seed_variation.find((s) => s.key === model.selected);
    if (seeds) {
      folds.append(
        el(
          "p",
          "admin-note",
          `Retrained with ${seeds.average_precision.values.length} different random seeds, ${model.selected_label}'s average precision moved by only ${fixed(seeds.average_precision.sd, 4)} (sd), so the result is not luck of the seed.`,
        ),
      );
    }
  }

  renderSpread(body, model);

  const pair = grid(body);
  // Calibration: does a 30% score mean 30%?
  const calibration = card(pair, "Calibration", "When the model says 30%, do floods follow about 30% of the time?");
  const points = (bands) => bands.map((band) => ({ x: band.mean_predicted, y: band.observed_flood_rate, band: band.band }));
  const all = [...model.bands, ...model.final_test.bands];
  const top = Math.ceil(Math.max(...all.flatMap((b) => [b.mean_predicted, b.observed_flood_rate])) * 10 + 0.5) / 10;
  chart(
    calibration,
    {
      type: "scatter",
      label: "Predicted flood chance against the flood rate observed, per risk band",
      data: {
        datasets: [
          {
            label: "Perfect calibration",
            data: [
              { x: 0, y: 0 },
              { x: top, y: top },
            ],
            showLine: true,
            borderColor: colours.muted,
            borderDash: [4, 4],
            borderWidth: 1.5,
            pointRadius: 0,
            pointHitRadius: 0,
          },
          {
            label: "Cross-validation",
            data: points(model.bands),
            showLine: true,
            borderColor: colours.series[0],
            backgroundColor: colours.series[0],
            borderWidth: 2,
            pointRadius: 5,
            pointBorderColor: colours.surface,
            pointBorderWidth: 2,
          },
          {
            label: `Held-out ${model.final_test.years.join("–")}`,
            data: points(model.final_test.bands),
            showLine: true,
            borderColor: colours.series[1],
            backgroundColor: colours.series[1],
            borderWidth: 2,
            pointRadius: 5,
            pointBorderColor: colours.surface,
            pointBorderWidth: 2,
          },
        ],
      },
      options: {
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            filter: (item) => item.datasetIndex > 0,
            callbacks: {
              label: (item) =>
                `${item.dataset.label}, ${BAND_LABELS[item.raw.band]}: predicted ${percent(item.raw.x)}, observed ${percent(item.raw.y)}`,
            },
          },
        },
        scales: {
          x: { min: 0, max: top, title: { display: true, text: "Predicted chance" }, ticks: { callback: (v) => percent(v, 0) } },
          y: { min: 0, max: top, title: { display: true, text: "Floods observed" }, ticks: { callback: (v) => percent(v, 0) } },
        },
      },
    },
    300,
  );
  const over = model.final_test.bands.filter((b) => b.observed_flood_rate < b.mean_predicted).length;
  if (model.final_test.bands.length && over === model.final_test.bands.length) {
    calibration.append(
      el(
        "p",
        "admin-note",
        `On ${model.final_test.years.join("–")} every band saw fewer floods than predicted: those years flooded less than the training years (${percent(model.final_test.base_rate)} against ${percent(model.training.base_rate)}), so read a score as relative risk rather than an exact chance.`,
      ),
    );
  }
  numbers(
    calibration,
    ["Band", "Rows", "Predicted", "Observed", "Held-out predicted", "Held-out observed"],
    model.bands.map((band, i) => {
      const held = model.final_test.bands[i];
      return [
        BAND_LABELS[band.band],
        count(band.rows),
        percent(band.mean_predicted),
        percent(band.observed_flood_rate),
        held ? percent(held.mean_predicted) : "–",
        held ? percent(held.observed_flood_rate) : "–",
      ];
    }),
  );

  // Threshold trade-off: the confusion matrix at each warning level.
  const thresholds = card(pair, "Choosing a warning level", "Treat a band and above as a warning: floods caught against false alarms.");
  renderThresholds(thresholds, model);

  // Learning curve.
  if (model.learning_curve.length) {
    const curve = card(
      body,
      "Would more data help?",
      "Average precision as the model is given more years to learn from (±1 sd across folds). Still rising at the right means more years of flood records would likely help.",
      { wide: true },
    );
    const lc = model.learning_curve;
    chart(
      curve,
      {
        type: "line",
        label: "Learning curve: average precision against years of training data",
        data: {
          labels: lc.map((row) => row.training_years_mean),
          datasets: [
            {
              label: "+1 sd",
              data: lc.map((row) => row.average_precision_mean + row.average_precision_sd),
              borderWidth: 0,
              pointRadius: 0,
              fill: false,
            },
            {
              label: "−1 sd",
              data: lc.map((row) => Math.max(0, row.average_precision_mean - row.average_precision_sd)),
              borderWidth: 0,
              pointRadius: 0,
              fill: "-1",
              backgroundColor: `${colours.primary}22`,
            },
            {
              label: model.selected_label,
              data: lc.map((row) => row.average_precision_mean),
              borderColor: colours.primary,
              backgroundColor: colours.primary,
              borderWidth: 2,
              pointRadius: 5,
              pointBorderColor: colours.surface,
              pointBorderWidth: 2,
              tension: 0.25,
            },
          ],
        },
        options: {
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: { display: false },
            tooltip: {
              filter: (item) => item.datasetIndex === 2,
              callbacks: {
                title: (items) => `${lc[items[0].dataIndex].training_years_mean} years, ${count(lc[items[0].dataIndex].training_rows_mean)} rows`,
                label: (item) => `Average precision ${fixed(item.raw)} ± ${fixed(lc[item.dataIndex].average_precision_sd)}`,
              },
            },
          },
          scales: {
            x: { grid: { display: false }, title: { display: true, text: "Years of training data (mean per fold)" } },
            y: { min: 0, title: { display: true, text: "Average precision" } },
          },
        },
      },
      240,
    );
  }

  // The years set aside.
  const test = model.final_test;
  const held = card(
    body,
    `The final test: ${test.years[0]}–${test.years[1]}`,
    `The whole choice was made again using ${test.chosen_on[0]}–${test.chosen_on[1]} only, and the pick was then scored once on years it never saw.`,
    { wide: true },
  );
  const heldRow = grid(held);
  const left = el("div");
  const right = el("div");
  heldRow.append(left, right);
  comparisonChart(left, test.comparison, "average_precision", { label: "Average precision, held-out years", max: 0.35 });
  const testTiles = grid(right, "admin-tiles admin-tiles--compact");
  const testChosen = test.comparison.find((r) => r.selected);
  tile(testTiles, "Picked on earlier years", test.selected_label);
  tile(testTiles, "ROC AUC", fixed(testChosen.roc_auc));
  tile(testTiles, "Over the usual chance", `${test.lift_over_base_rate}×`);
  tile(testTiles, "Over season alone", `${test.lift_over_season_only}×`);
  const honest =
    test.lift_over_season_only < 1.2
      ? `On these years the season alone already did nearly as well (${test.lift_over_season_only}× lift). The rainfall inputs add little there, so the map should be read as "how unusual is this month", not as a forecast of a specific flood.`
      : `The pick beat the season-only baseline by ${test.lift_over_season_only}×, so the rainfall inputs add real information.`;
  callout(right, "Read honestly", honest, test.lift_over_season_only < 1.2 ? "warning" : "info");
  const best = test.comparison.filter((r) => !r.baseline).reduce((a, b) => (b.average_precision > a.average_precision ? b : a));
  if (best.key !== test.selected) {
    right.append(
      el(
        "p",
        "admin-note",
        `${best.label} did best on these years (${fixed(best.average_precision)} against ${fixed(testChosen.average_precision)}) although the earlier years did not pick it: with this few floods per year, the ranking of close models is not stable.`,
      ),
    );
  }
  if (test.selected !== model.selected) {
    right.append(
      el(
        "p",
        "admin-note",
        `On the earlier years alone the procedure picked ${test.selected_label}; on all years it picks ${model.selected_label}, the model in use. So this measures how well the method of choosing generalises, not this exact model.`,
      ),
    );
  }

  // Tuning and calibration choices.
  const choices = grid(body);
  const tuning = card(choices, "Tuning", "A small grid search per model, kept only when it beat the hand-set values.");
  tuning.append(
    table(
      ["Model", "Hand-set AP", "Searched AP", "Kept"],
      model.tuning.map((t) => [t.label, fixed(t.fixed.average_precision), fixed(t.searched.average_precision), t.adopted ? "Searched" : "Hand-set"]),
    ),
  );
  const calib = card(choices, "Probability calibration", "Recalibrating the probabilities was tried and kept only if it helped.");
  calib.append(
    table(
      ["Method", "Average precision", "Brier", "Used"],
      model.calibration_comparison.map((c) => [c.method, fixed(c.average_precision), fixed(c.brier, 4), c.selected ? "Yes" : ""]),
    ),
  );
}

function renderThresholds(container, model) {
  const rows = model.thresholds;
  if (!rows.length) return;
  const bands = Object.fromEntries(model.warnings.map((w) => [w.threshold, w.from_band]));
  const picker = el("div", "admin-segmented");
  picker.setAttribute("role", "group");
  picker.setAttribute("aria-label", "Warning level");
  const view = el("div");
  container.append(picker, view);

  function show(index) {
    const row = rows[index];
    for (const [i, b] of [...picker.children].entries()) b.setAttribute("aria-pressed", String(i === index));
    const matrix = confusion(row);
    const grid2 = el("div", "admin-matrix");
    grid2.append(
      el("span", "admin-matrix__corner"),
      el("span", "admin-matrix__head", "Warned"),
      el("span", "admin-matrix__head", "Not warned"),
      el("span", "admin-matrix__side", `Flood (${count(matrix.floods)})`),
    );
    const [tp, fn, fp, tn] = matrix.cells;
    for (const cell of [tp, fn]) grid2.append(matrixCell(cell));
    grid2.append(el("span", "admin-matrix__side", `No flood (${count(matrix.dry)})`));
    for (const cell of [fp, tn]) grid2.append(matrixCell(cell));

    const stats = el("dl", "admin-stats");
    for (const [label, value] of [
      ["Floods caught (recall)", percent(row.recall)],
      ["Warnings that were right (precision)", percent(row.precision)],
      ["Quiet months left quiet", percent(row.specificity)],
      ["Balanced accuracy", percent(row.balanced_accuracy)],
      ["F1", fixed(row.f1)],
    ]) {
      stats.append(el("dt", null, label), el("dd", null, value));
    }
    view.replaceChildren(grid2, stats);
  }

  rows.forEach((row, index) => {
    const band = bands[row.threshold];
    const button = el("button", "admin-segmented__button", band ? WARNING_FROM[band] : `≥ ${percent(row.threshold, 0)}`);
    button.type = "button";
    button.title = `Warn at a predicted chance of ${percent(row.threshold, 0)} or more`;
    button.addEventListener("click", () => show(index));
    picker.append(button);
  });
  show(Math.min(1, rows.length - 1));
}

function matrixCell(cell) {
  const node = el("span", `admin-matrix__cell admin-matrix__cell--${cell.key}`);
  node.append(
    el("strong", "admin-matrix__value", count(cell.value)),
    el("span", "admin-matrix__label", `${cell.label}, ${percent(cell.share, 0)}`),
  );
  return node;
}

function renderDrivers(body, { model }) {
  if (!model) return renderNoModel(body);
  const colours = palette();
  const rows = model.importance;
  const importance = card(
    body,
    "Which inputs the model leans on",
    "Permutation importance: how much average precision drops when one input is shuffled and the others are left alone. Whiskers are ±1 sd across folds; a whisker that crosses zero means the effect is not reliable.",
    { wide: true },
  );
  chart(
    importance,
    {
      type: "bar",
      label: "Permutation importance of each model input",
      data: {
        labels: rows.map((row) => featureLabel(row.feature)),
        datasets: [
          {
            label: "Drop in average precision",
            data: rows.map((row) => row.mean),
            whiskers: rows.map((row) => row.sd),
            backgroundColor: rows.map((row) => (row.mean > 0 ? colours.primary : colours.muted)),
            ...BAR,
          },
        ],
      },
      options: {
        indexAxis: "y",
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (item) => `Drop in AP: ${fixed(item.raw, 4)} ± ${fixed(rows[item.dataIndex].sd, 4)}` } },
        },
        scales: {
          x: {
            suggestedMin: Math.min(0, ...rows.map((r) => r.mean - r.sd)),
            suggestedMax: Math.max(...rows.map((r) => r.mean + r.sd)),
            title: { display: true, text: "Drop in average precision when shuffled" },
            ticks: { callback: (v) => fixed(v, 2) },
          },
          y: { grid: { display: false } },
        },
      },
    },
    60 + rows.length * 32,
  );
  numbers(importance, ["Input", "Mean drop", "sd"], rows.map((row) => [featureLabel(row.feature), fixed(row.mean, 4), fixed(row.sd, 4)]));

  const top = rows.slice(0, 3).map((row) => featureLabel(row.feature).toLowerCase());
  callout(
    body,
    "Inputs, not causes",
    `The model relies most on ${top.join(", ")}. These say what the model uses to rank months, not what causes floods: inputs that move together share their importance, and county area is there only to account for how floods were recorded.`,
  );

  const inputs = card(body, "Every input", `${model.features.length} inputs, from ${model.rain_source ?? "the stored rainfall"} and terrain.`, { wide: true });
  const chips = el("ul", "admin-chips");
  for (const feature of model.features) chips.append(el("li", "admin-chip", featureLabel(feature)));
  inputs.append(chips);
}

/** The live record: daily scores against the floods ReliefWeb reported since (riskengine/live.py). */
function renderLive(body, live) {
  if (!live) return;
  const section = card(
    body,
    "Live record",
    "Since the map went live: each finished county-month, warned or not, against the floods reported that month",
    { wide: true },
  );
  if (!live.switched_on) {
    section.append(
      el(
        "p",
        "admin-empty",
        "Not switched on. ReliefWeb's API answers only approved app names: request one, then set RELIEFWEB_APPNAME in the server's .env.",
      ),
    );
    return;
  }
  const totals = live.totals;
  if (!totals.county_months) {
    section.append(el("p", "admin-empty", "No month has finished since scoring began. The first figures appear when one does."));
    return;
  }
  const tiles = grid(section, "admin-tiles");
  const training = live.training;
  tile(
    tiles,
    "Floods caught",
    totals.recall === null ? "No floods reported" : percent(totals.recall, 0),
    `${count(totals.caught)} of ${count(totals.floods)}` + (training ? ` · ${percent(training.recall, 0)} when evaluated` : ""),
  );
  tile(
    tiles,
    "Warnings followed by a report",
    totals.precision === null ? "No warnings" : percent(totals.precision, 0),
    `${count(totals.false_alarms)} without one` + (training?.precision != null ? ` · ${percent(training.precision, 0)} when evaluated` : ""),
  );
  tile(tiles, "County-months counted", count(totals.county_months), `From ${day(live.first_month).replace(/^1 /, "")}`);

  section.append(
    el(
      "p",
      "admin-card__subtitle",
      `A warning is a daily chance of ${percent(live.threshold, 0)} or more at any point in the month, as in the evaluation. A flood is a ReliefWeb report that names the county. Floods nobody reported count as false alarms, so that share is a lower bound.`,
    ),
  );

  numbers(
    section,
    ["Month", "County-months", "Floods reported", "Caught", "Missed", "False alarms"],
    live.by_month.map((r) => [day(r.month).replace(/^1 /, ""), count(r.county_months), count(r.floods), count(r.caught), count(r.missed), count(r.false_alarms)]),
  );
  if (live.reports.length) {
    const rows = live.reports.map((r) => {
      const link = el("a", null, r.title);
      link.href = r.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      return [day(r.published), r.county, link, r.source || "–"];
    });
    numbers(section, ["Published", "County", "Report", "Source"], rows, `Show the ${count(live.reports.length)} report matches`);
  }
}

function renderErrors(body, { model, live }) {
  if (!model) return renderNoModel(body);
  renderLive(body, live);
  const colours = palette();
  const errors = model.errors;
  if (!errors?.by_month) {
    body.append(el("p", "admin-empty", "This training run did not record an error breakdown."));
    return;
  }
  const threshold = percent(errors.threshold, 0);
  callout(
    body,
    "When it was evaluated: where the misses are",
    `Counted with a warning at a predicted chance of ${threshold} or more, across the cross-validation folds. Misses that cluster in a month or a county point at what the inputs cannot see there.`,
  );

  const months = card(body, "By month", "Floods caught and missed, and warnings that came to nothing", { wide: true });
  const byMonth = errors.by_month;
  chart(
    months,
    {
      type: "bar",
      label: "Floods caught, missed and false alarms by calendar month",
      data: {
        labels: byMonth.map((row) => monthName(row.month)),
        datasets: [
          { label: "Caught", data: byMonth.map((r) => r.caught), backgroundColor: colours.series[0], stack: "floods", ...BAR },
          { label: "Missed", data: byMonth.map((r) => r.missed), backgroundColor: colours.series[1], stack: "floods", ...BAR },
          { label: "False alarms", data: byMonth.map((r) => r.false_alarms), backgroundColor: colours.faint, stack: "alarms", ...BAR },
        ],
      },
      options: {
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom" } },
        scales: {
          x: { stacked: true, grid: { display: false } },
          y: { stacked: true, title: { display: true, text: "County-months" } },
        },
      },
    },
    280,
  );
  numbers(
    months,
    ["Month", "County-months", "Floods", "Caught", "Missed", "False alarms"],
    byMonth.map((r) => [monthName(r.month), count(r.rows), count(r.floods), count(r.caught), count(r.missed), count(r.false_alarms)]),
  );

  const counties = card(body, "By county", "Worst catch rate first. Counties with no recorded flood are last.", { wide: true });
  const wrap = el("div", "admin-table-scroll");
  wrap.append(
    table(
      ["County", "Floods", "Caught", "Missed", "False alarms", "Share of floods caught"],
      countyErrors(errors.by_county).map((r) => [
        r.county,
        count(r.floods),
        count(r.caught),
        count(r.missed),
        count(r.false_alarms),
        r.recall === null ? "No floods recorded" : meter(r.recall, percent(r.recall, 0)),
      ]),
    ),
  );
  counties.append(wrap);
}

function renderSatellite(body, { model, satellite_check: events, satellite_check_sub_county: subEvents, label_check: labels }) {
  if (!model) return renderNoModel(body);
  if (!events?.length) {
    body.append(el("p", "admin-empty", "No satellite check has been run. Run `manage.py validate_unosat`."));
    return;
  }
  callout(
    body,
    "A check the model never trained on",
    "UNOSAT mapped floodwater from satellite images during recent floods. Here the model scores every county for the last day of each event, and we ask whether the counties with mapped water scored higher than the dry ones. ROC AUC 0.5 means no better than chance. One caution: a county hidden by cloud in every image is counted as dry, so some \"dry\" counties may have flooded.",
  );
  const views = [{ key: "county", label: "Counties", unit: "counties", events }];
  if (subEvents?.length) views.push({ key: "sub_county", label: "Sub-counties", unit: "sub-counties", events: subEvents });
  const holder = el("div");
  if (views.length > 1) {
    const picker = el("div", "admin-segmented");
    picker.setAttribute("role", "group");
    picker.setAttribute("aria-label", "Regions scored");
    let undo = () => {};
    const show = (index) => {
      for (const [i, b] of [...picker.children].entries()) b.setAttribute("aria-pressed", String(i === index));
      undo();
      holder.replaceChildren();
      const from = charts.length;
      satelliteEvents(holder, views[index]);
      undo = chartsSince(from);
    };
    views.forEach((view, index) => {
      const button = el("button", "admin-segmented__button", view.label);
      button.type = "button";
      button.addEventListener("click", () => show(index));
      picker.append(button);
    });
    body.append(picker, holder);
    show(0);
    if (subEvents?.length) {
      holder.before(
        el(
          "p",
          "admin-note",
          "The model was trained on counties. Sub-counties are scored with the same model from their own rainfall, so this asks whether it can point to the right part of a county, which it was never trained to do.",
        ),
      );
    }
  } else {
    body.append(holder);
    satelliteEvents(holder, views[0]);
  }
  renderLabelCheck(body, labels);
}

function satelliteEvents(body, { unit, events }) {
  const colours = palette();
  const sub = unit === "sub-counties";
  const nameOf = (c) => (sub ? `${c.name} (${c.county})` : c.name ?? c.county);
  for (const event of events) {
    const section = card(body, event.event, `Scored for ${day(event.date)}`, { wide: true });
    const row = grid(section);
    const left = el("div");
    const right = el("div");
    row.append(left, right);

    const tiles = grid(left, "admin-tiles admin-tiles--compact");
    tile(tiles, "ROC AUC", fixed(event.roc_auc, 2), verdict(event.roc_auc, unit));
    tile(tiles, "Mean score, flooded", percent(event.mean_probability_flooded), `${event.flooded_counties} ${unit} with mapped water`);
    tile(tiles, "Mean score, dry", percent(event.mean_probability_dry), `${event.dry_counties} ${unit}`);
    tile(
      tiles,
      "Flooded at High or above",
      `${event.flooded_at_high_or_above} of ${event.flooded_counties}`,
      `and ${event.dry_at_high_or_above} of ${event.dry_counties} dry ${unit}`,
    );

    // A strip: each county is a dot at its score, flooded above, dry below.
    const jitter = (i) => ((i * 37) % 11) / 11 - 0.5;
    const flooded = event.counties.filter((c) => c.flooded);
    const dry = event.counties.filter((c) => !c.flooded);
    chart(
      right,
      {
        type: "scatter",
        label: `Each region's score on ${day(event.date)}, flooded and dry ${unit}`,
        data: {
          datasets: [
            {
              label: "Mapped floodwater",
              data: flooded.map((c, i) => ({ x: c.probability, y: 1 + jitter(i) * 0.5, county: c })),
              backgroundColor: colours.primary,
              pointRadius: 5,
              pointHoverRadius: 7,
              pointBorderColor: colours.surface,
              pointBorderWidth: 1.5,
            },
            {
              label: "Dry",
              data: dry.map((c, i) => ({ x: c.probability, y: jitter(i) * 0.5, county: c })),
              backgroundColor: colours.muted,
              pointRadius: 5,
              pointHoverRadius: 7,
              pointBorderColor: colours.surface,
              pointBorderWidth: 1.5,
            },
          ],
        },
        options: {
          plugins: {
            legend: { position: "bottom" },
            tooltip: {
              callbacks: {
                label: (item) => {
                  const c = item.raw.county;
                  return `${nameOf(c)}: ${percent(c.probability)} (${labelFor(c.risk_category)})${c.flooded ? `, ${fixed(c.flooded_km2, 1)} km² under water` : ""}`;
                },
              },
            },
          },
          scales: {
            x: { min: 0, title: { display: true, text: "Model score (chance of a large flood)" }, ticks: { callback: (v) => percent(v, 0) } },
            y: {
              min: -0.6,
              max: 1.6,
              grid: { display: false },
              ticks: { stepSize: 1, callback: (v) => (v === 1 ? "Flooded" : v === 0 ? "Dry" : "") },
            },
          },
        },
      },
      220,
    );
    numbers(
      section,
      [sub ? "Sub-county" : "County", "Mapped water (km²)", "Score", "Category"],
      event.counties.map((c) => [nameOf(c), c.flooded ? fixed(c.flooded_km2, 1) : "None", percent(c.probability), labelFor(c.risk_category)]),
      sub ? "Show every sub-county" : "Show every county",
    );
  }
}

function verdict(auc, unit = "counties") {
  if (auc === null) return `Not enough ${unit} to compare`;
  if (auc >= 0.7) return `Flooded ${unit} scored clearly higher`;
  if (auc > 0.55) return `Flooded ${unit} scored a little higher`;
  if (auc >= 0.45) return "No better than chance on this event";
  return `Dry ${unit} scored higher: the model missed this event`;
}

function renderHealth(body, { health, data_quality: dataQuality }) {
  const head = card(body, "Status", null, { wide: true });
  const status = el("div", "admin-health");
  status.append(statusPill(health.status), el("span", "admin-health__when", `Checked ${day(health.today)}`));
  head.append(status);
  const list = el("ul", "admin-checks");
  for (const check of health.checks) {
    const item = el("li", "admin-checks__item");
    item.append(statusPill(check.status), el("span", "admin-checks__name", check.name), el("span", "admin-checks__detail", check.detail));
    list.append(item);
  }
  head.append(list, el("p", "admin-note", health.rules));

  const tiles = grid(body, "admin-tiles");
  tile(tiles, "Regions", count(health.regions.total), `${count(health.regions.by_level.county)} counties, ${count(health.regions.by_level.sub_county)} sub-counties`);
  tile(tiles, "Latest scores", day(health.latest_scores.date), health.latest_scores.model_versions.join(", ") || "None");
  tile(
    tiles,
    "Rainfall stored",
    count(health.rainfall.rows),
    health.rainfall.first ? `daily rows, ${day(health.rainfall.first)} to ${day(health.rainfall.last)}` : "none yet",
  );
  tile(tiles, "Rainfall on the latest day", `${count(health.rainfall.regions_on_last_day)} of ${count(health.regions.total)}`, "regions");
  tile(
    tiles,
    "Seasonal outlook",
    health.outlook.months.length ? `${health.outlook.months.length} months` : "None",
    health.outlook.issued_on ? `issued ${day(health.outlook.issued_on)}` : "run flood_outlook",
  );
  tile(tiles, "Model loaded", health.loaded_model ?? "Placeholder rule").classList.add("admin-tile--text");

  const row = grid(body);
  const today = card(row, "On the map now", `Scores for ${day(health.latest_scores.date)}`);
  categoryBar(today, "Counties", health.latest_scores.categories.county);
  categoryBar(today, "Sub-counties", health.latest_scores.categories.sub_county);
  const trend = card(row, "Last 30 days", "Counties by category, per day");
  trendChart(trend, health.trend);

  if (dataQuality) renderDataQuality(body, dataQuality);
}

function renderProvenance(body, { model }) {
  if (!model) return renderNoModel(body);
  const p = model.provenance;
  const c = model.compute;
  const where = card(body, "Where this model came from", "Enough to trace the model on the map back to the exact code, data and run that made it.", { wide: true });
  const list = el("dl", "admin-facts");
  for (const [label, value] of [
    ["Model version", model.model_version],
    ["Training run", p.run_id],
    ["Git commit", p.git_commit],
    ["Training set SHA-256", p.training_set_sha256],
    ["scikit-learn", p.scikit_learn],
    ["Random seed", p.random_state],
    ["What it predicts", model.target],
    ["Validation", model.validation],
    ["Rainfall source", model.rain_source],
    ["Probability calibration", model.calibration],
  ]) {
    if (value === undefined || value === null) continue;
    list.append(el("dt", null, label), el("dd", "admin-facts__value", String(value)));
  }
  where.append(list);

  const stages = Object.entries(c).filter(([key]) => key.startsWith("seconds_"));
  if (stages.length) {
    const compute = card(body, "Training run on Google Colab", `${c.cpu ?? "CPU"}, ${c.cores ?? "?"} cores. Time per stage.`, { wide: true });
    const colours = palette();
    const label = (key) => key.replace("seconds_", "").replaceAll("_", " ").replace(/^./, (s) => s.toUpperCase());
    chart(
      compute,
      {
        type: "bar",
        label: "Training run time per stage",
        data: {
          labels: stages.map(([key]) => label(key)),
          datasets: [
            {
              label: "Seconds",
              data: stages.map(([, v]) => v),
              backgroundColor: colours.primary,
              valueLabels: (v) => duration(v),
              ...BAR,
            },
          ],
        },
        options: {
          indexAxis: "y",
          layout: { padding: { right: 80 } },
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: (item) => duration(item.raw) } } },
          scales: { x: { title: { display: true, text: "Seconds" } }, y: { grid: { display: false } } },
        },
      },
      50 + stages.length * 36,
    );
    const total = stages.reduce((sum, [, v]) => sum + v, 0);
    compute.append(el("p", "admin-note", `About ${duration(total)} in all.`));
  }
}

// --- In use: forecasts, KMD, alerts, data quality ----------------------------

const LEVEL_NAMES = { advisory: "Advisory", watch: "Watch", warning: "Warning" };

function leadLabel(n) {
  return `${n} day${n === 1 ? "" : "s"} ahead`;
}

function weekLabel(iso) {
  return `w/c ${shortDay(iso)}`;
}

/** Destroy the charts drawn since `from`, for a view that redraws in place. */
function chartsSince(from) {
  const drawn = charts.slice(from);
  return () => {
    for (const c of drawn) c.destroy();
    charts = charts.filter((c) => !drawn.includes(c));
  };
}

function stackedOptions(yTitle, { title } = {}) {
  return {
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { position: "bottom" }, ...(title ? { tooltip: { callbacks: { title } } } : {}) },
    scales: {
      x: { stacked: true, grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 10 } },
      y: { stacked: true, title: { display: Boolean(yTitle), text: yTitle }, ticks: { precision: 0 } },
    },
  };
}

function renderForecast(body, { forecast_skill: skill, kmd }) {
  const colours = palette();
  callout(
    body,
    "Forecasts checked against what came",
    `Every day the model scores each county for the week ahead from the rain forecast, and those forecasts are kept. Once a day arrives, its forecasts are set against the score the county actually got. "Assume no change" is the plain alternative: keep the score from the day the forecast was made. A forecast is only worth having where it beats that. Counties only, since ${day(skill.since)}.`,
  );
  const leads = skill.leads;
  if (!leads.length) {
    body.append(el("p", "admin-empty", "No forecast has reached its day yet. forecast_scores issues them daily, so this fills in from tomorrow."));
  } else {
    const first = leads[0];
    const last = leads[leads.length - 1];
    const trusted = trustedLead(leads);
    const tiles = grid(body, "admin-tiles");
    tile(tiles, "Forecasts checked", count(leads.reduce((s, l) => s + l.pairs, 0)), `from ${skill.issues_checked} daily issues`);
    tile(tiles, leadLabel(first.lead_days), percent(first.same_category, 0), "got the map's category right");
    tile(tiles, leadLabel(last.lead_days), percent(last.same_category, 0), "got the map's category right");
    tile(tiles, "Trust up to", trusted === null ? "None" : leadLabel(trusted), "right at least 80% of the time");
    tile(
      tiles,
      `Warnings caught, ${leadLabel(first.lead_days)}`,
      percent(first.warnings_caught, 0),
      `${first.hits} of ${first.hits + first.misses} High or above days were forecast`,
    );

    const right = card(body, "Right category, by days ahead", "The share of forecasts that matched the category the day got, against assuming no change.", { wide: true });
    const lowest = Math.min(...leads.flatMap((l) => [l.same_category ?? 1, l.persistence_same ?? 1]));
    chart(
      right,
      {
        type: "line",
        label: "Share of forecasts in the right category by days ahead",
        data: {
          labels: leads.map((l) => leadLabel(l.lead_days)),
          datasets: [
            { label: "Forecast: same category", data: leads.map((l) => l.same_category), borderColor: colours.primary, backgroundColor: colours.primary, borderWidth: 2, pointRadius: 5, pointBorderColor: colours.surface, pointBorderWidth: 2 },
            { label: "Forecast: within one category", data: leads.map((l) => l.within_one), borderColor: colours.series[2], backgroundColor: colours.series[2], borderWidth: 2, pointRadius: 4 },
            { label: "Assume no change", data: leads.map((l) => l.persistence_same), borderColor: colours.muted, backgroundColor: colours.muted, borderDash: [5, 4], borderWidth: 2, pointRadius: 3 },
          ],
        },
        options: {
          interaction: { mode: "index", intersect: false },
          plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (item) => `${item.dataset.label}: ${percent(item.raw)}` } } },
          scales: {
            x: { grid: { display: false } },
            y: { min: Math.max(0, Math.floor(lowest * 10 - 1) / 10), max: 1, ticks: { callback: (v) => percent(v, 0) } },
          },
        },
      },
      280,
    );
    const gains = leads.map(forecastGain).filter((g) => g !== null);
    if (gains.length) {
      const better = gains.filter((g) => g > 0).length;
      right.append(
        el(
          "p",
          "admin-note",
          better === gains.length
            ? `The forecast beats assuming no change at every lead, by ${percent(gains[0], 0)} one day out and ${percent(gains[gains.length - 1], 0)} at the furthest: the rain forecast earns its keep further out.`
            : `The forecast beats assuming no change at ${better} of ${gains.length} leads.`,
        ),
      );
    }
    numbers(
      right,
      ["Days ahead", "Pairs", "Same category", "Within one", "Assume no change", "Mean score error", "Warnings caught", "Warnings that came"],
      leads.map((l) => [l.lead_days, count(l.pairs), percent(l.same_category), percent(l.within_one), percent(l.persistence_same), fixed(l.mean_abs_error, 4), percent(l.warnings_caught), percent(l.warnings_that_came)]),
    );

    const warned = card(body, "Warnings by days ahead", "High or above, forecast against the day: caught, missed, and forecast but not reached.", { wide: true });
    chart(
      warned,
      {
        type: "bar",
        label: "Warnings caught, missed and false alarms by days ahead",
        data: {
          labels: leads.map((l) => leadLabel(l.lead_days)),
          datasets: [
            { label: "Caught", data: leads.map((l) => l.hits), backgroundColor: colours.primary, maxBarThickness: 32 },
            { label: "Missed", data: leads.map((l) => l.misses), backgroundColor: colours.series[1], maxBarThickness: 32 },
            { label: "Forecast, did not come", data: leads.map((l) => l.false_alarms), backgroundColor: colours.muted, maxBarThickness: 32 },
          ],
        },
        options: stackedOptions("County-days"),
      },
      240,
    );
  }

  // Agreement with KMD.
  const k = card(body, "Agreement with KMD", `County-days since ${day(kmd.since)} with a FloodScout alert, a Kenya Meteorological Department flood-related warning, or both.`, { wide: true });
  const tiles = grid(k, "admin-tiles admin-tiles--compact");
  tile(tiles, "Agreement", percent(kmd.agreement, 0), "of county-days flagged by either, flagged by both");
  tile(tiles, "Both", count(kmd.both), "county-days");
  tile(tiles, "FloodScout only", count(kmd.ours_only), "county-days");
  tile(tiles, "KMD only", count(kmd.kmd_only), "county-days");
  if (kmd.both + kmd.ours_only + kmd.kmd_only === 0) {
    k.append(el("p", "admin-empty", "Neither FloodScout nor KMD flagged a county in this period."));
  } else {
    chart(
      k,
      {
        type: "bar",
        label: "Counties flagged per day by FloodScout, KMD or both",
        data: {
          labels: kmd.daily.map((d) => shortDay(d.date)),
          datasets: [
            { label: "Both", data: kmd.daily.map((d) => d.both), backgroundColor: colours.primary, maxBarThickness: 12 },
            { label: "FloodScout only", data: kmd.daily.map((d) => d.ours_only), backgroundColor: colours.series[1], maxBarThickness: 12 },
            { label: "KMD only", data: kmd.daily.map((d) => d.kmd_only), backgroundColor: colours.series[2], maxBarThickness: 12 },
          ],
        },
        options: stackedOptions("Counties", { title: (items) => day(kmd.daily[items[0].dataIndex].date) }),
      },
      240,
    );
  }
  k.append(
    el(
      "p",
      "admin-note",
      `They answer different questions, so they should not match exactly: KMD warns of heavy rain coming, FloodScout of flood risk from rain that has fallen and is forecast. KMD keeps no history of past warnings, so this record starts with the first one FloodScout stored${kmd.kmd_first_stored ? ` (${day(kmd.kmd_first_stored)})` : ""}. Withdrawn warnings are left out.`,
    ),
  );
  if (kmd.warnings.length) {
    numbers(
      k,
      ["From", "To", "KMD warning", "Counties", "With a FloodScout alert"],
      kmd.warnings.map((w) => {
        const link = el("a", null, w.event + (w.severity ? ` (${w.severity})` : ""));
        link.href = w.web;
        link.target = "_blank";
        link.rel = "noopener";
        return [day(w.onset), day(w.ends), link, count(w.counties), `${w.counties_with_our_alert} of ${w.counties}`];
      }),
      "Show KMD's warnings",
    );
  }
}

function renderAlerts(body, { alerts: a, data_quality: dq }) {
  const colours = palette();
  const levelColour = { advisory: colours.series[3], watch: colours.series[1], warning: colours.series[0] };
  const tiles = grid(body, "admin-tiles");
  const levels = Object.entries(a.alerts.by_level)
    .map(([level, n]) => `${n} ${(LEVEL_NAMES[level] ?? level).toLowerCase()}`)
    .join(", ");
  tile(tiles, `Alerts since ${shortDay(a.since)}`, count(a.alerts.issued), levels || "none issued");
  tile(tiles, "Active now", count(a.alerts.active), `${count(a.alerts.upgraded)} raised a level after issue`);
  tile(tiles, "Typical length", a.alerts.median_days === null ? "–" : `${fixed(a.alerts.median_days, 1)} days`, "median, alerts that have ended");
  tile(
    tiles,
    "Short-lived",
    a.alerts.ended ? percent(a.alerts.short_lived / a.alerts.ended, 0) : "–",
    `${a.alerts.short_lived} of ${a.alerts.ended} ended within ${a.alerts.short_lived_days} days of issue`,
  );
  tile(tiles, "Emails delivered", percent(a.delivery.rate, 0), `${count(a.delivery.sent)} sent, ${count(a.delivery.failed)} failed`);
  tile(tiles, "Subscribers", count(a.subscribers.now), `${a.subscribers.pending} waiting to confirm, ${a.subscribers.stopped} have stopped`);

  const weeks = a.weekly.map((w) => weekLabel(w.week));
  const row = grid(body);
  const activity = card(row, "What the rules did each week", "Alerts issued, raised a level, and ended.");
  chart(
    activity,
    {
      type: "bar",
      label: "Alerts issued, upgraded and ended per week",
      data: {
        labels: weeks,
        datasets: [
          { label: "Issued", data: a.weekly.map((w) => w.issued), backgroundColor: colours.primary, maxBarThickness: 14 },
          { label: "Raised a level", data: a.weekly.map((w) => w.upgraded), backgroundColor: colours.series[1], maxBarThickness: 14 },
          { label: "Ended", data: a.weekly.map((w) => w.ended), backgroundColor: colours.muted, maxBarThickness: 14 },
        ],
      },
      options: {
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom" } },
        scales: { x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 8 } }, y: { ticks: { precision: 0 } } },
      },
    },
    260,
  );
  numbers(activity, ["Week", "Runs", "Issued", "Raised", "Ended"], a.weekly.map((w) => [weekLabel(w.week), w.runs, w.issued, w.upgraded, w.ended]));

  const lengths = card(row, "How long alerts last", "Alerts that have ended, by level. Many under two days means alerts that come and go.");
  chart(
    lengths,
    {
      type: "bar",
      label: "How long ended alerts lasted, by level",
      data: {
        labels: a.alerts.durations.map((b) => b.label),
        datasets: Object.keys(LEVEL_NAMES).map((level) => ({
          label: LEVEL_NAMES[level],
          data: a.alerts.durations.map((b) => b[level] ?? 0),
          backgroundColor: levelColour[level],
          maxBarThickness: 32,
        })),
      },
      options: stackedOptions("Alerts"),
    },
    260,
  );
  if (a.alerts.ended && a.alerts.short_lived / a.alerts.ended > 0.25) {
    lengths.append(
      el(
        "p",
        "admin-note",
        `${percent(a.alerts.short_lived / a.alerts.ended, 0)} of alerts ended within ${a.alerts.short_lived_days} days. Each one emails the subscribers for that place, so a high share risks people learning to ignore them; a longer minimum before an alert ends would cut it.`,
      ),
    );
  }

  const row2 = grid(body);
  const delivery = card(row2, "Email delivery each week", "Alert and all-clear emails sent and failed.");
  chart(
    delivery,
    {
      type: "bar",
      label: "Emails sent and failed per week",
      data: {
        labels: weeks,
        datasets: [
          { label: "Sent", data: a.weekly.map((w) => w.sent), backgroundColor: colours.primary, maxBarThickness: 14 },
          { label: "Failed", data: a.weekly.map((w) => w.failed), backgroundColor: colours.series[1], maxBarThickness: 14 },
        ],
      },
      options: stackedOptions("Emails"),
    },
    240,
  );
  numbers(delivery, ["Week", "Sent", "Failed", "Skipped", "Delivered"], a.weekly.map((w) => [weekLabel(w.week), w.sent, w.failed, w.skipped, percent(w.delivery_rate, 0)]));

  const people = card(row2, "Subscribers", "Signed up at each week's end, with new confirmations and stops.");
  chart(
    people,
    {
      type: "bar",
      label: "Subscribers signed up per week, with new and stopped",
      data: {
        labels: weeks,
        datasets: [
          { type: "line", label: "Signed up", data: a.weekly.map((w) => w.signed_up), borderColor: colours.primary, backgroundColor: colours.primary, borderWidth: 2, pointRadius: 3, tension: 0.2 },
          { label: "New", data: a.weekly.map((w) => w.new_subscribers), backgroundColor: colours.series[2], maxBarThickness: 12 },
          { label: "Stopped", data: a.weekly.map((w) => w.stopped), backgroundColor: colours.muted, maxBarThickness: 12 },
        ],
      },
      options: {
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom" } },
        scales: { x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 8 } }, y: { min: 0, ticks: { precision: 0 } } },
      },
    },
    240,
  );
  if (a.subscribers.stops_by_how.length) {
    people.append(table(["How they stopped", "People"], a.subscribers.stops_by_how.map((s) => [s.label, count(s.count)])));
  } else {
    people.append(el("p", "admin-note", "No one has stopped their alerts."));
  }

  // How jumpy the map is.
  const flips = dq.flips;
  const jumpy = card(
    body,
    "How often the map changes colour",
    `Counties whose category changed from the day before, over the last ${flips.days_compared} days. A county that keeps flipping sends alerts that come and go.`,
    { wide: true },
  );
  if (!flips.daily.length) {
    jumpy.append(el("p", "admin-empty", "Not enough days of scores yet."));
    return;
  }
  const weather = flips.daily.filter((d) => !d.model_changed);
  const average = weather.length ? weather.reduce((s, d) => s + d.up + d.down, 0) / weather.length : 0;
  const t = grid(jumpy, "admin-tiles admin-tiles--compact");
  tile(t, "Changes a day", fixed(average, 1), `of ${flips.counties} counties, on average`);
  tile(t, "Never changed", count(flips.never_changed), "counties kept their colour throughout");
  tile(t, "Jumpiest", flips.jumpiest[0]?.county ?? "–", flips.jumpiest[0] ? `${flips.jumpiest[0].changes} changes` : "");
  chart(
    jumpy,
    {
      type: "bar",
      label: "Counties that moved up or down a category per day",
      data: {
        labels: flips.daily.map((d) => shortDay(d.date)),
        datasets: [
          { label: "Up a category", data: flips.daily.map((d) => d.up), backgroundColor: flips.daily.map((d) => (d.model_changed ? colours.faint : colours.series[1])), maxBarThickness: 14 },
          { label: "Down a category", data: flips.daily.map((d) => -d.down), backgroundColor: flips.daily.map((d) => (d.model_changed ? colours.faint : colours.primary)), maxBarThickness: 14 },
        ],
      },
      options: {
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              title: (items) => day(flips.daily[items[0].dataIndex].date) + (flips.daily[items[0].dataIndex].model_changed ? " (model changed)" : ""),
              label: (item) => `${item.dataset.label}: ${Math.abs(item.raw)}`,
            },
          },
        },
        scales: {
          x: { stacked: true, grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 12 } },
          y: { stacked: true, title: { display: true, text: "Counties (down below zero)" }, ticks: { precision: 0, callback: (v) => Math.abs(v) } },
        },
      },
    },
    240,
  );
  if (flips.daily.some((d) => d.model_changed)) {
    jumpy.append(el("p", "admin-note", "Grey days are when a new model rescored every county: that is the model changing, not the weather, and they are left out of the average."));
  }
  numbers(jumpy, ["County", "Changes"], flips.jumpiest.map((j) => [j.county, j.changes]), "Show the jumpiest counties");
}

function renderDataQuality(body, dq) {
  const colours = palette();
  const gaps = dq.rain_gaps;
  const row = grid(body);
  const missing = card(row, "Rainfall gaps", gaps.last ? `Regions with rainfall each day, ${gaps.days} days to ${day(gaps.last)}.` : "No rainfall stored yet.");
  if (gaps.last) {
    const t = grid(missing, "admin-tiles admin-tiles--compact");
    tile(t, "Complete", `${count(gaps.complete)} of ${count(gaps.regions)}`, "regions with every day");
    tile(t, "Days missing", count(gaps.missing_days), `across ${count(gaps.with_gaps)} regions`);
    chart(
      missing,
      {
        type: "bar",
        label: "Regions with rainfall stored per day",
        data: {
          labels: gaps.daily.map((d) => shortDay(d.date)),
          datasets: [
            {
              label: "Regions with rainfall",
              data: gaps.daily.map((d) => d.regions),
              backgroundColor: gaps.daily.map((d) => (d.regions < gaps.regions ? colours.series[1] : colours.primary)),
              maxBarThickness: 12,
            },
          ],
        },
        options: {
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { title: (items) => day(gaps.daily[items[0].dataIndex].date), label: (item) => `${item.raw} of ${gaps.regions} regions` } },
          },
          scales: { x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 12 } }, y: { min: 0, max: gaps.regions, ticks: { precision: 0 } } },
        },
      },
      200,
    );
    if (gaps.worst.length) {
      numbers(missing, ["Region", "County", "Days missing"], gaps.worst.map((r) => [r.region, r.county, r.missing_days]), "Show the regions with gaps");
    } else {
      missing.append(el("p", "admin-note", "Every region has rainfall for every day. A gap would leave that region's score resting on fewer days of rain."));
    }
  }

  const api = dq.api_usage;
  const quota = card(
    row,
    "Open-Meteo usage",
    `Calls per day against the free limit of ${count(api.limit_per_day)}. FloodScout's own count, weighed as Open-Meteo weighs calls: each location, and each 14 days of data at it.`,
  );
  const t2 = grid(quota, "admin-tiles admin-tiles--compact");
  tile(t2, "Today", count(api.today), `${percent(api.today / api.limit_per_day, 0)} of the daily limit`);
  tile(t2, "Busiest day", count(api.peak), `${api.days_over_80_percent} day${api.days_over_80_percent === 1 ? "" : "s"} over 80%`);
  tile(t2, "Rate limited", count(api.rate_limited), `replies asking to wait; ${api.failed} failed`);
  chart(
    quota,
    {
      type: "bar",
      label: "Open-Meteo calls per day against the free daily limit",
      data: {
        labels: api.daily.map((d) => shortDay(d.date)),
        datasets: [
          { type: "line", label: "Free daily limit", data: api.daily.map(() => api.limit_per_day), borderColor: colours.series[1], borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0 },
          {
            label: "Calls",
            data: api.daily.map((d) => d.calls),
            backgroundColor: api.daily.map((d) => (d.calls >= 0.8 * api.limit_per_day ? colours.series[1] : colours.primary)),
            maxBarThickness: 12,
          },
        ],
      },
      options: {
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom" } },
        scales: { x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 12 } }, y: { min: 0, ticks: { callback: (v) => count(v) } } },
      },
    },
    200,
  );
  quota.append(
    el(
      "p",
      "admin-note",
      api.counting_since
        ? `Counted since ${day(api.counting_since)}. The limits are also ${count(api.limit_per_hour)} an hour and ${count(api.limit_per_minute)} a minute; the downloads wait out a rate-limit reply rather than fail.`
        : "Nothing counted yet: counting starts with the next download.",
    ),
  );

  const normal = dq.rain_vs_normal;
  if (!normal) return;
  const rows = normal.counties;
  const rain = card(
    body,
    "Rain against normal",
    `Each county's last 30 days of rain, to ${day(normal.date)}, against what it normally gets in that month: the model's main input. ${normal.above_normal} counties above 120%, ${normal.below_normal} below 80%.`,
    { wide: true },
  );
  chart(
    rain,
    {
      type: "bar",
      label: "Last 30 days of rain as a share of each county's normal",
      data: {
        labels: rows.map((r) => r.county),
        datasets: [
          {
            label: "Share of normal",
            data: rows.map((r) => r.ratio),
            backgroundColor: rows.map((r) => (r.ratio > 1.2 ? colours.primary : r.ratio < 0.8 ? colours.faint : colours.muted)),
            valueLabels: (v) => percent(v, 0),
            ...BAR,
          },
        ],
      },
      options: {
        indexAxis: "y",
        layout: { padding: { right: 48 } },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (item) => `${rows[item.dataIndex].rain_30d} mm against a normal ${rows[item.dataIndex].normal} mm (${ofNormal(item.raw)})` } },
        },
        scales: {
          x: { min: 0, title: { display: true, text: "Share of normal (100% = a usual month)" }, ticks: { callback: (v) => percent(v, 0) } },
          y: { grid: { display: false }, ticks: { autoSkip: false, font: { size: 11 } } },
        },
      },
    },
    40 + rows.length * 18,
  );
  numbers(rain, ["County", "Last 30 days (mm)", "Normal (mm)", "Share"], rows.map((r) => [r.county, fixed(r.rain_30d, 1), fixed(r.normal, 1), ofNormal(r.ratio)]));
}

function renderLabelCheck(body, check) {
  if (!check) return;
  const colours = palette();
  const section = card(
    body,
    "Are the training labels right?",
    "The model learned from flood circles: every county a recorded flood's circle touches counts as flooded. For floods the Global Flood Database mapped from satellite (2000 to 2018), this is how often a labelled county held mapped water, and how often a county with mapped water was labelled.",
    { wide: true },
  );
  const t = grid(section, "admin-tiles admin-tiles--compact");
  tile(t, "Labels with water", percent(check.labels_with_water, 0), `${check.both} of ${check.labelled} labelled counties held mapped water`);
  tile(t, "Water that was labelled", percent(check.water_labelled, 0), `${check.both} of ${check.wet} counties with water were labelled`);
  tile(t, "Floods checked", count(check.events.length), "that the database mapped");
  chart(
    section,
    {
      type: "bar",
      label: "Labelled counties and counties with mapped water, per flood",
      data: {
        labels: check.events.map((e) => `${monthName(Number(e.began.slice(5, 7)))} ${e.began.slice(0, 4)}`),
        datasets: [
          { label: "Labelled and wet", data: check.events.map((e) => e.both), backgroundColor: colours.primary, maxBarThickness: 18 },
          { label: "Labelled, no water mapped", data: check.events.map((e) => e.labelled - e.both), backgroundColor: colours.muted, maxBarThickness: 18 },
          { label: "Water, not labelled", data: check.events.map((e) => e.wet - e.both), backgroundColor: colours.series[1], maxBarThickness: 18 },
        ],
      },
      options: stackedOptions("Counties", {
        title: (items) => `DFO event ${check.events[items[0].dataIndex].event}, began ${day(check.events[items[0].dataIndex].began)}`,
      }),
    },
    260,
  );
  section.append(
    el(
      "p",
      "admin-note",
      "Grey is a county the labels call flooded where no water was mapped: a false label, or water the 250 m satellite missed under cloud. Orange is water the labels left out. Either way the noise is in the answers the model is marked against, which pulls its measured scores down, not up.",
    ),
  );
  numbers(section, ["DFO event", "Began", "Labelled", "Mapped wet", "Both"], check.events.map((e) => [e.event, day(e.began), e.labelled, e.wet, e.both]));
}

function renderSpread(body, model) {
  const cv = model.cross_validation;
  const labels = Object.fromEntries([...model.comparison, ...cv.models].map((m) => [m.key, m.label]));
  const folds = Object.entries(cv.summary).map(([key, s]) => ({
    key,
    label: labels[key] ?? key,
    mean: s.average_precision_mean,
    sd: s.average_precision_sd,
  }));
  const seeds = model.seed_variation.map((s) => ({ key: s.key, label: s.label, mean: s.average_precision.mean, sd: s.average_precision.sd }));
  if (!folds.length && !seeds.length) return;
  const colours = palette();
  const section = card(
    body,
    "How much do the scores move?",
    "Average precision with ±1 sd whiskers. Across held-out years: how much the result depends on which years are tested. Across random seeds: how much is luck.",
    { wide: true },
  );
  const pair = grid(section);
  const draw = (rows, title) => {
    const box = el("div");
    box.append(el("p", "admin-note", title));
    pair.append(box);
    const top = Math.max(...rows.map((r) => r.mean + r.sd));
    chart(
      box,
      {
        type: "bar",
        label: `Average precision ${title.toLowerCase()}`,
        data: {
          labels: rows.map((r) => (r.key === model.selected ? `${r.label} (in use)` : r.label)),
          datasets: [
            {
              label: "Average precision",
              data: rows.map((r) => r.mean),
              whiskers: rows.map((r) => r.sd),
              backgroundColor: rows.map((r) => (r.key === model.selected ? colours.primary : colours.muted)),
              ...BAR,
            },
          ],
        },
        options: {
          indexAxis: "y",
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: (item) => `${fixed(item.raw)} ± ${fixed(rows[item.dataIndex].sd)}` } } },
          scales: { x: { min: 0, suggestedMax: top * 1.05, title: { display: true, text: "Average precision" } }, y: { grid: { display: false } } },
        },
      },
      50 + rows.length * 36,
    );
  };
  if (folds.length) draw(folds, "Across held-out years");
  if (seeds.length) draw(seeds, "Across random seeds");
  const widest = [...folds].sort((x, y) => y.sd - x.sd)[0];
  const seedMax = Math.max(0, ...seeds.map((s) => s.sd));
  if (widest) {
    const trained = folds.filter((f) => cv.models.some((m) => m.key === f.key)).map((f) => f.mean);
    const close = trained.length > 1 && Math.max(...trained) - Math.min(...trained) < widest.sd;
    section.append(
      el(
        "p",
        "admin-note",
        `Which years are tested moves average precision by up to ±${fixed(widest.sd)} (${widest.label}); the seed moves it by at most ±${fixed(seedMax, 4)}, so the result is not luck of the seed.${close ? " The gaps between the trained models are smaller than the spread across years, so the choice between them is close and could go the other way on other years." : ""} Logistic regression has no random seed, so it is not on the seeds chart.`,
      ),
    );
  }
}

function renderNoModel(body) {
  callout(
    body,
    "No trained model is loaded",
    "The placeholder rule is scoring, and it was never evaluated. Train the model on Colab and ship its artifacts to see this tab.",
    "warning",
  );
}

const RENDERERS = {
  overview: renderOverview,
  evaluation: renderEvaluation,
  drivers: renderDrivers,
  errors: renderErrors,
  forecast: renderForecast,
  satellite: renderSatellite,
  alerts: renderAlerts,
  health: renderHealth,
  provenance: renderProvenance,
};

// --- The page ------------------------------------------------------------------

function renderLocked(root) {
  const box = el("div", "admin-locked");
  box.append(
    el("h2", "admin-locked__title", "Model and system analytics"),
    el(
      "p",
      "admin-locked__text",
      "Model evaluation and system health are for Analysts and the System Administrator. Sign in with the button at the top right to see them.",
    ),
  );
  const back = el("a", "auth__button", "Back to the map");
  back.href = "#/";
  box.append(back);
  root.replaceChildren(box);
}

function renderShell(root, tab, { onRefresh }) {
  const page = el("div", "admin-page");
  const head = el("header", "admin-head");
  const titles = el("div");
  titles.append(el("p", "admin-head__eyebrow", "System Administrator"), el("h2", "admin-head__title", "Model & system analytics"));
  const model = analytics.model;
  titles.append(
    el(
      "p",
      "admin-head__lede",
      model ? `${model.model_version} · ${model.selected_label} · trained in Colab run ${model.run_id ?? "unknown"}` : "No trained model loaded",
    ),
  );
  const actions = el("div", "admin-head__actions");
  actions.append(statusPill(analytics.health.status));
  const refresh = el("button", "auth__button", "Refresh");
  refresh.type = "button";
  refresh.addEventListener("click", onRefresh);
  const back = el("a", "auth__button auth__button--primary", "Back to the map");
  back.href = "#/";
  actions.append(refresh, back);
  head.append(titles, actions);

  const nav = el("nav", "admin-tabs");
  nav.setAttribute("aria-label", "Analytics sections");
  for (const t of TABS) {
    const link = el("a", "admin-tabs__link", t.label);
    link.href = `#/admin/${t.id}`;
    if (t.id === tab) link.setAttribute("aria-current", "page");
    nav.append(link);
  }

  const body = el("div", "admin-body");
  page.append(head, nav, body);
  root.replaceChildren(page);
  return body;
}

function renderLoading(root) {
  const page = el("div", "admin-page admin-page--loading");
  page.setAttribute("aria-busy", "true");
  page.append(el("div", "admin-skeleton admin-skeleton--head"));
  const tiles = el("div", "admin-tiles");
  for (let i = 0; i < 6; i += 1) tiles.append(el("div", "admin-skeleton admin-skeleton--tile"));
  page.append(tiles, el("div", "admin-skeleton admin-skeleton--chart"));
  root.replaceChildren(page);
}

/**
 * Draw the analytics page into `root` for the hash slug `admin/<tab>`.
 * `user` is who is signed in (null when nobody is).
 */
export async function showAdmin(root, { slug, user }) {
  clearCharts();
  lastRender = { root, slug, user };
  if (!(user?.can_see_analytics ?? user?.is_administrator)) {
    analytics = null;
    renderLocked(root);
    return;
  }
  const tab = tabFromSlug(slug, TABS);
  if (tab !== lastTab) root.scrollTop = 0;
  lastTab = tab;

  if (!analytics) {
    renderLoading(root);
    try {
      analytics = await fetchAnalytics();
    } catch (error) {
      if (error.status === 401 || error.status === 403) {
        renderLocked(root);
        return;
      }
      const box = el("div", "admin-locked");
      box.append(el("h2", "admin-locked__title", "Analytics could not load"), el("p", "admin-locked__text", error.message));
      const retry = el("button", "auth__button auth__button--primary", "Try again");
      retry.type = "button";
      retry.addEventListener("click", () => showAdmin(root, { slug, user }));
      box.append(retry);
      root.replaceChildren(box);
      return;
    }
    // Navigated elsewhere while loading.
    if (lastRender.slug !== slug || lastRender.root !== root) return;
  }

  const body = renderShell(root, tab, {
    onRefresh: () => {
      analytics = null;
      showAdmin(root, { slug, user });
    },
  });
  RENDERERS[tab](body, analytics);
}

/** Leaving the page: free the charts. The fetched data is kept for a return visit. */
export function hideAdmin() {
  clearCharts();
  lastRender = null;
}

/** Forget the data, e.g. on sign-out or after a rescore. */
export function resetAdmin() {
  analytics = null;
}

// Charts take their colours when drawn; redraw when the theme flips.
if (typeof window !== "undefined" && window.matchMedia) {
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (lastRender && analytics) showAdmin(lastRender.root, lastRender);
  });
}
