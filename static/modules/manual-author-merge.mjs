// Opt-in selection and review; no identity writes happen before confirmation.
import { state } from "./state.mjs?v=20260922-2";
import { $, escapeHtml, showToast } from "./format.mjs?v=20260922-2";
import { authorComparisonsLoading } from "./author-comparison.mjs?v=20260922-2";

function sources(author) {
  return [...new Set([author.asin, ...(author.source_asins || [])].filter(Boolean))].sort();
}

export function manualMergeAuthors(results) {
  const authors = new Map();
  for (const result of results) {
    const members = result.kind === "author-match" ? result.members : result.kind === "author" ? [result] : [];
    for (const member of members || []) {
      const asins = sources(member);
      if (!asins.length) continue;
      const snapshots = member.source_groups || (member.author_group_members || [member]).map(sources);
      authors.set(JSON.stringify(asins), { ...member, kind: "author", source_asins: asins, source_groups: snapshots });
    }
  }
  return [...authors.values()];
}

function seriesKey(series) {
  return series.asin ? `asin:${series.asin}` : `name:${String(series.name || "").trim().toLowerCase()}`;
}

export function manualMergeCandidate(members) {
  if (members.length !== 2 || sources(members[0]).some(asin => sources(members[1]).includes(asin))) {
    throw new Error("Choose two different authors or non-overlapping groups.");
  }
  const ordered = [...members].sort((left, right) =>
    (right.book_count || 0) - (left.book_count || 0)
    || (right.series_count || 0) - (left.series_count || 0)
    || Number(left.asin.startsWith("name:")) - Number(right.asin.startsWith("name:")));
  const rightSeries = new Set((ordered[1].series || []).map(seriesKey));
  const shared = [...new Map((ordered[0].series || []).filter(series => rightSeries.has(seriesKey(series))).map(series => [seriesKey(series), series])).values()];
  const overrides = ordered.some((member, index) => sources(member).some(asin => (ordered[1 - index].separate_source_asins || []).includes(asin)));
  return { kind: "author-match", manual_merge: true, overrides_separation: overrides,
    asin: `manual:${ordered.map(member => member.asin).join("|")}`, primary_asin: ordered[0].asin,
    members: ordered, shared_series: shared, shared_series_count: shared.length, comparisonOpen: true };
}

export function renderManualMergeControls() {
  const container = $("#manual-author-merge-controls");
  if (!container) return;
  const mode = state.manualAuthorMerge;
  container.hidden = !mode && manualMergeAuthors(state.searchResults).length < 2;
  if (container.hidden) { container.innerHTML = ""; return; }
  const busy = state.authorSearchDecisionPending || authorComparisonsLoading();
  if (!mode) {
    container.innerHTML = `<button type="button" class="manual-merge-link" data-manual-merge-start${busy ? " disabled" : ""}>Merge authors…</button>`;
  } else if (mode.phase === "select") {
    container.innerHTML = `<span class="muted">Select two authors or groups.</span><button type="button" class="manual-merge-link" data-manual-merge-review${mode.selected.size === 2 && !busy ? "" : " disabled"}>Review merge (${mode.selected.size}/2)</button><button type="button" class="manual-merge-link" data-manual-merge-cancel>Cancel</button>`;
  } else {
    container.innerHTML = '<span class="muted">Review the catalogs before confirming this manual merge.</span>';
  }
}

export function manualMergeCheckbox(item, index) {
  const mode = state.manualAuthorMerge;
  if (mode?.phase !== "select" || item.kind !== "author") return "";
  const selected = mode.selected.has(index);
  return `<label class="manual-merge-select"><input type="checkbox" aria-label="Select ${escapeHtml(item.name)} for merge" data-manual-merge-select="${index}"${selected ? " checked" : ""}${!selected && mode.selected.size >= 2 ? " disabled" : ""}> Select</label>`;
}

export function createManualAuthorMerge({ renderSearchResults }) {
  function startManualMerge() {
    if (state.authorSearchDecisionPending || authorComparisonsLoading()) return;
    const authors = manualMergeAuthors(state.searchResults);
    if (authors.length < 2) return;
    state.manualAuthorMerge = { phase: "select", context: state.searchContext,
      originalResults: state.searchResults, authors, selected: new Set() };
    state.searchResults = authors;
    $("#search-message").textContent = "Choose authors to merge";
    renderSearchResults();
    $("[data-manual-merge-select]")?.focus();
  }

  function selectManualAuthor(index, checked) {
    const mode = state.manualAuthorMerge;
    if (mode?.phase !== "select" || !mode.authors[index]) return;
    if (!checked) mode.selected.delete(index);
    else if (mode.selected.size < 2) mode.selected.add(index);
    renderSearchResults();
    $(`[data-manual-merge-select="${index}"]`)?.focus();
  }

  function reviewManualMerge() {
    const mode = state.manualAuthorMerge;
    if (mode?.phase !== "select" || mode.selected.size !== 2 || authorComparisonsLoading()) return;
    try {
      const candidate = manualMergeCandidate([...mode.selected].map(index => mode.authors[index]));
      mode.phase = "review";
      state.searchResults = [candidate];
      $("#search-message").textContent = "Manual merge review";
      renderSearchResults();
      $("[data-author-match-comparison] summary")?.focus();
    } catch (error) { showToast(error.message); }
  }

  function cancelManualMerge() {
    const mode = state.manualAuthorMerge;
    if (!mode || state.authorSearchDecisionPending) return;
    if (state.searchContext === mode.context) state.searchResults = mode.originalResults;
    state.manualAuthorMerge = null;
    $("#search-message").textContent = `${state.searchResults.length} result${state.searchResults.length === 1 ? "" : "s"}`;
    renderSearchResults();
    $("[data-manual-merge-start]")?.focus();
  }

  return { startManualMerge, selectManualAuthor, reviewManualMerge, cancelManualMerge };
}
