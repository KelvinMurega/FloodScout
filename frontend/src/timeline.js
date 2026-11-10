/**
 * The timeline under the period bar: the days with stored scores, then the
 * days of the newest forecast, as one row of steps the slider moves along
 * and Play animates. Pure, so it can be tested under node.
 */

// How many days back the slider reaches. The history chart looks back as far.
export const TIMELINE_PAST_DAYS = 30;

/**
 * Steps, oldest first: each { date, kind } where kind is "observed" for an
 * earlier day, "today" for the newest scored day and "forecast" after it.
 *
 * `scoreDates` are newest first (as the API lists them); `forecastDays`
 * oldest first. Forecast days not after the newest scored day are dropped,
 * so a stale forecast never stands in for a day that has been scored.
 */
export function timelineSteps(scoreDates, forecastDays, pastDays = TIMELINE_PAST_DAYS) {
  const observed = [...scoreDates].slice(0, pastDays).reverse();
  const today = observed.at(-1);
  const steps = observed.map((date) => ({
    date,
    kind: date === today ? "today" : "observed",
  }));
  for (const date of forecastDays) {
    if (!today || date > today) steps.push({ date, kind: "forecast" });
  }
  return steps;
}

/**
 * The step on screen: the forecast day, the earlier day, or today.
 * -1 when the view is not on the timeline (an outlook month).
 */
export function currentStep(steps, { scoreDate = null, forecastDay = null, outlook = false } = {}) {
  if (outlook) return -1;
  const wanted = forecastDay ?? scoreDate;
  if (wanted) return steps.findIndex((step) => step.date === wanted);
  return steps.findIndex((step) => step.kind === "today");
}

/** What choosing `step` sets: { scoreDate, forecastDay }, null for "latest". */
export function stateForStep(step) {
  if (!step || step.kind === "today") return { scoreDate: null, forecastDay: null };
  if (step.kind === "forecast") return { scoreDate: null, forecastDay: step.date };
  return { scoreDate: step.date, forecastDay: null };
}
