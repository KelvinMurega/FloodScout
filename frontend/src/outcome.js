/**
 * The months the outlook offers, and a past month's outlook set beside the
 * floods recorded in it (GET risk-outlook/outcome/, riskengine/outcome.py).
 * No DOM here, so it can be tested on its own.
 */

/** How many months ahead the period bar offers: this one and the two after. */
export const MONTHS_AHEAD = 3;

/** The first of the month `day` is in, as YYYY-MM-DD. */
export function monthOf(day = new Date()) {
  const month = String(day.getMonth() + 1).padStart(2, "0");
  return `${day.getFullYear()}-${month}-01`;
}

/**
 * The months from `risk-outlook/months/` as the period bar shows them:
 * `upcoming`, soonest first and at most MONTHS_AHEAD, and `past`, newest
 * first. The server marks each with `kind`; a row without one is placed by
 * the browser's own date.
 */
export function splitOutlookMonths(months, today = new Date()) {
  const thisMonth = monthOf(today);
  const kindOf = (m) => m.kind ?? (m.month < thisMonth ? "past" : "upcoming");
  const sorted = [...months].sort((a, b) => a.month.localeCompare(b.month));
  return {
    upcoming: sorted.filter((m) => kindOf(m) === "upcoming").slice(0, MONTHS_AHEAD),
    past: sorted.filter((m) => kindOf(m) === "past").reverse(),
  };
}

const ORDER = ["very_high", "high", "moderate", "low"];

/**
 * The look back in sentences: where floods were reported, and how the
 * outlook had rated those places. `labelFor` names a risk category.
 */
export function outcomeLines(outcome, labelFor) {
  const { summary } = outcome;
  const lines = [];
  if (!outcome.has_outlook) return ["No county outlook was stored for this month."];
  const counties = (n) => (n === 1 ? "1 county" : `${n} counties`);

  if (summary.flooded === 0) {
    lines.push(
      outcome.complete
        ? "No flood was found reported in any county this month."
        : "No flood found reported in any county so far this month.",
    );
  } else {
    lines.push(
      `Floods were reported in ${counties(summary.flooded)}${outcome.complete ? "" : " so far"}.`,
    );
    const parts = ORDER.filter((c) => summary.by_category[c]?.flooded > 0).map((c) => {
      const { rated, flooded } = summary.by_category[c];
      return `${flooded} of the ${rated} rated ${labelFor(c)}`;
    });
    if (parts.length) lines.push(`Of those, the outlook had put ${parts.join(", ")}.`);
    if (summary.flooded_ranks.length) {
      lines.push(
        `Their places on the outlook's list, highest chance first: ` +
          `${summary.flooded_ranks.join(", ")} of ${summary.counties}.`,
      );
    }
  }
  return lines;
}

/** One record as a line: the date, the source, the headline. */
export function recordLabel(record, sources) {
  const source = sources.find((s) => s.key === record.source)?.name ?? record.source;
  const by = record.publisher && record.publisher !== source ? `${record.publisher}, ${source}` : source;
  return { date: record.date, by, title: record.title, url: record.url };
}
