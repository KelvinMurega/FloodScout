/**
 * Chart.js, set up once for the analytics page.
 *
 * Colours: one blue for the thing that matters (the model in use, floods
 * caught), greys for everything it is compared against, and the fixed
 * categorical order below only where several series are equal peers. The
 * risk categories keep the map's own colours (risk.js). Every value is read
 * at draw time, so a chart drawn in dark mode uses the dark steps.
 */

import {
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  Filler,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  ScatterController,
  Tooltip,
} from "chart.js";

import { prefersDark } from "../risk.js";

Chart.register(
  BarController,
  BarElement,
  CategoryScale,
  Filler,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  ScatterController,
  Tooltip,
);

// Validated categorical order (blue, orange, aqua, yellow): every adjacent
// pair clears the colour-blind separation target in both modes.
const SERIES = {
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
  dark: ["#3987e5", "#d95926", "#199e70", "#c98500"],
};
const MUTED = { light: "#a3a29b", dark: "#6b6a65" };
const FAINT = { light: "#d6d5cf", dark: "#3d3d3a" };

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function isDark() {
  const stamp = document.documentElement.dataset.theme;
  return stamp ? stamp === "dark" : prefersDark();
}

export function palette() {
  const mode = isDark() ? "dark" : "light";
  return {
    series: SERIES[mode],
    primary: SERIES[mode][0],
    muted: MUTED[mode],
    faint: FAINT[mode],
    text: cssVar("--text-secondary"),
    textStrong: cssVar("--text-primary"),
    grid: cssVar("--hairline"),
    surface: cssVar("--surface-1"),
  };
}

/** Draws each bar's value just past its end, for datasets with `valueLabels`. */
const valueLabels = {
  id: "valueLabels",
  afterDatasetsDraw(chart) {
    const { ctx } = chart;
    const colours = palette();
    chart.data.datasets.forEach((dataset, index) => {
      if (!dataset.valueLabels) return;
      const meta = chart.getDatasetMeta(index);
      if (meta.hidden) return;
      const horizontal = chart.options.indexAxis === "y";
      ctx.save();
      ctx.fillStyle = colours.textStrong;
      ctx.font = `600 12px ${cssVar("--font-sans")}`;
      ctx.textBaseline = "middle";
      meta.data.forEach((bar, i) => {
        const value = dataset.data[i];
        if (value === null || value === undefined) return;
        const text = dataset.valueLabels(value, i);
        if (horizontal) {
          const negative = value < 0;
          ctx.textAlign = negative ? "right" : "left";
          ctx.fillText(text, bar.x + (negative ? -6 : 6), bar.y);
        } else {
          ctx.textAlign = "center";
          ctx.fillText(text, bar.x, bar.y - 9);
        }
      });
      ctx.restore();
    });
  },
};

/** Draws ±sd whiskers on horizontal bars, for datasets with `whiskers`. */
const whiskers = {
  id: "whiskers",
  afterDatasetsDraw(chart) {
    const { ctx, scales } = chart;
    chart.data.datasets.forEach((dataset, index) => {
      if (!dataset.whiskers) return;
      const meta = chart.getDatasetMeta(index);
      ctx.save();
      ctx.strokeStyle = palette().textStrong;
      ctx.globalAlpha = 0.55;
      ctx.lineWidth = 1.5;
      meta.data.forEach((bar, i) => {
        const mean = dataset.data[i];
        const sd = dataset.whiskers[i];
        const left = scales.x.getPixelForValue(mean - sd);
        const right = scales.x.getPixelForValue(mean + sd);
        ctx.beginPath();
        ctx.moveTo(left, bar.y);
        ctx.lineTo(right, bar.y);
        ctx.moveTo(left, bar.y - 4);
        ctx.lineTo(left, bar.y + 4);
        ctx.moveTo(right, bar.y - 4);
        ctx.lineTo(right, bar.y + 4);
        ctx.stroke();
      });
      ctx.restore();
    });
  },
};

function baseOptions() {
  const colours = palette();
  const axis = {
    grid: { color: colours.grid, drawTicks: false },
    border: { display: false },
    ticks: { color: colours.text, padding: 8, font: { size: 12 } },
    title: { color: colours.text, font: { size: 12 } },
  };
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 500 },
    font: { family: cssVar("--font-sans") },
    interaction: { mode: "nearest", intersect: true },
    plugins: {
      legend: {
        labels: {
          color: colours.text,
          boxWidth: 10,
          boxHeight: 10,
          useBorderRadius: true,
          borderRadius: 2,
          font: { size: 12 },
        },
      },
      tooltip: {
        backgroundColor: colours.surface,
        titleColor: colours.textStrong,
        bodyColor: colours.text,
        borderColor: colours.grid,
        borderWidth: 1,
        padding: 10,
        cornerRadius: 8,
        boxPadding: 4,
        usePointStyle: true,
      },
    },
    scales: { x: structuredClone(axis), y: structuredClone(axis) },
  };
}

function merge(target, source) {
  for (const [key, value] of Object.entries(source ?? {})) {
    if (value && typeof value === "object" && !Array.isArray(value) && typeof target[key] === "object") {
      merge(target[key], value);
    } else {
      target[key] = value;
    }
  }
  return target;
}

/**
 * A chart in `canvas`, with the page's theme under `options`. The canvas
 * gets `label` as its accessible name; the numbers are also in a table.
 */
export function makeChart(canvas, { type, data, options, label }) {
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", label);
  return new Chart(canvas, {
    type,
    data,
    options: merge(baseOptions(), options),
    plugins: [valueLabels, whiskers],
  });
}

/** Bar styling the dataviz spec asks for: thin, rounded data end, 2px gap. */
export const BAR = {
  borderRadius: 4,
  borderSkipped: "start",
  maxBarThickness: 26,
  categoryPercentage: 0.72,
  barPercentage: 0.9,
};
