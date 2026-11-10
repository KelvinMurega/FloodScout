/**
 * Finding a region by name (FR-01).
 *
 * Pure functions, no DOM and no network, so they run under `node --test`
 * (see search.test.js). The dashboard builds the entries from the regions it
 * has loaded and hands them here.
 */

/** Lower-case, without accents or punctuation: "Murang'a" and "muranga" match. */
export function normalise(text) {
  return String(text ?? "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

// Lower is better. A name that starts with the query beats one where a later
// word starts with it, which beats one that only contains it.
const STARTS_NAME = 0;
const STARTS_WORD = 1;
const CONTAINS = 2;

function rank(name, query) {
  if (name.startsWith(query)) return STARTS_NAME;
  if (name.includes(` ${query}`)) return STARTS_WORD;
  // Spaces dropped on both sides, so "homabay" finds "Homa Bay".
  if (name.replaceAll(" ", "").includes(query.replaceAll(" ", ""))) return CONTAINS;
  return null;
}

/**
 * The entries whose name matches `query`, best first, at most `limit`.
 *
 * An entry is `{ name, level, ... }`; anything else on it is passed through
 * untouched. Within one rank, counties come before sub-counties and then
 * names run alphabetically, so "Kisumu" the county leads "Kisumu Central".
 */
export function searchRegions(entries, query, limit = 8) {
  const wanted = normalise(query);
  if (!wanted) return [];

  const matches = [];
  for (const entry of entries) {
    const score = rank(normalise(entry.name), wanted);
    if (score !== null) matches.push({ entry, score });
  }
  matches.sort(
    (a, b) =>
      a.score - b.score ||
      Number(a.entry.level !== "county") - Number(b.entry.level !== "county") ||
      a.entry.name.localeCompare(b.entry.name),
  );
  return matches.slice(0, limit).map((match) => match.entry);
}
