// On-demand catalog evidence for a search-result merge suggestion.
import { state } from "./state.mjs?v=20260922-2";
import { escapeHtml } from "./format.mjs?v=20260922-2";

function seriesKey(series) {
  return series.asin ? `asin:${series.asin}` : `name:${String(series.name || "").toLowerCase().trim()}`;
}

function detailLink(kind, item) {
  const name = item.name || item.title || "Unnamed release";
  if (!item.asin) return escapeHtml(name);
  return `<button type="button" class="detail-link-button" data-watch-details data-watch-details-kind="${kind}" data-watch-details-asin="${escapeHtml(item.asin)}" data-watch-details-name="${escapeHtml(name)}" data-detail-origin="search">${escapeHtml(name)}</button>`;
}

export function comparisonDetailItem(member) {
  const sources = new Map((member.source_identities || []).map(source => [source.asin, source]));
  sources.set(member.asin, member);
  const groups = member.source_groups || [member.source_asins || [member.asin]];
  const representatives = groups.map(group => {
    const asin = group.includes(member.asin) ? member.asin : group[0];
    return { asin, name: sources.get(asin)?.name || member.name };
  });
  return { kind: "author", asin: member.asin, name: member.name,
    ...(representatives.length > 1 ? { author_group_members: representatives } : {}) };
}

export function authorComparisonsLoading() {
  return state.searchResults.some(candidate => candidate.comparison?.some(entry => entry?.loading));
}

export function renderAuthorComparison(candidate, index) {
  const shared = candidate.shared_series || [];
  const sharedKeys = new Set(shared.map(seriesKey));
  const columns = (candidate.members || []).map((member, memberIndex) => {
    const entry = candidate.comparison?.[memberIndex];
    const groups = entry?.details?.series || [];
    const series = new Map((member.series || []).map(item => [seriesKey(item), item]));
    groups.filter(group => group.asin).forEach(group => series.set(seriesKey(group), group));
    const other = [...series.values()].filter(item => !sharedKeys.has(seriesKey(item)));
    const bookGroups = groups.map(group => {
      const books = [...new Map((group.books || []).map(book => [book.asin || book.title, book])).values()];
      if (!books.length) return "";
      return `<div class="author-comparison-book-group"><h6>${group.asin ? detailLink("series", group) : "Standalone books"}${sharedKeys.has(seriesKey(group)) ? ' <span class="muted">· shared series</span>' : ""}</h6><ul>${books.map(book => `<li>${detailLink("book", book)}</li>`).join("")}</ul></div>`;
    }).join("");
    const error = entry?.error || entry?.details?.error;
    const loading = !entry || entry.loading;
    return `<section class="author-comparison-author"><h4>${escapeHtml(member.name)}</h4>
      <h5>Other series</h5>${other.length ? `<ul>${other.map(item => `<li>${detailLink("series", item)}</li>`).join("")}</ul>` : `<p class="muted">${loading ? "None in the search results; checking the catalog…" : "No other series returned by the catalog."}</p>`}
      <h5>Books by series</h5>${bookGroups}
      ${loading ? '<p role="status">Loading books and series…</p>' : !bookGroups ? '<p class="muted">No book details returned by the catalog.</p>' : ""}
      ${error ? `<p class="author-comparison-warning" role="status">Catalog may be incomplete: ${escapeHtml(error)}</p><button type="button" class="detail-link-button" data-author-comparison-retry="${index}" data-author-comparison-member="${memberIndex}"${loading ? " disabled" : ""}>Retry loading catalog</button>` : ""}
    </section>`;
  }).join("");
  return `<h4>Shared series</h4>${shared.length ? `<ul class="author-comparison-shared">${shared.map(series => `<li>${detailLink("series", series)}</li>`).join("")}</ul>` : '<p class="muted">No shared series found in these search results.</p>'}
    <p class="muted">${candidate.manual_merge ? "This is your selection, not an automatic match. Confirm only if these listings represent the same author." : "Compare catalog listings before deciding. A shared series can also mean co-authorship."}</p>
    <div class="author-comparison-columns">${columns}</div>`;
}

export function createAuthorComparison({ loadDetailsForItem }) {
  function paint(candidate, index) {
    if (state.searchResults[index] !== candidate) return;
    const content = document.querySelector(`[data-author-comparison-content="${index}"]`);
    if (content) content.innerHTML = renderAuthorComparison(candidate, index);
  }

  function updateDecisionButtons() {
    const disabled = state.authorSearchDecisionPending || authorComparisonsLoading();
    document.querySelectorAll("[data-author-match-decision], [data-manual-merge-start]").forEach(button => {
      button.disabled = disabled;
      if (disabled) button.setAttribute("aria-busy", "true");
      else button.removeAttribute("aria-busy");
    });
  }

  async function loadMember(candidate, index, memberIndex) {
    candidate.comparison ||= [];
    if (candidate.comparison[memberIndex]?.loading) return;
    const entry = { ...candidate.comparison[memberIndex], loading: true, error: null };
    candidate.comparison[memberIndex] = entry;
    paint(candidate, index);
    updateDecisionButtons();
    try {
      entry.details = await loadDetailsForItem(comparisonDetailItem(candidate.members[memberIndex]));
    } catch (error) {
      if (error.name !== "AbortError") entry.error = error.message;
    } finally {
      entry.loading = false;
      paint(candidate, index);
      updateDecisionButtons();
    }
  }

  function toggleComparison(index, open) {
    const candidate = state.searchResults[index];
    if (candidate?.kind !== "author-match") return;
    candidate.comparisonOpen = open;
    if (!open || state.authorSearchDecisionPending) return;
    candidate.members.forEach((_member, memberIndex) => {
      if (!candidate.comparison?.[memberIndex]) void loadMember(candidate, index, memberIndex);
    });
  }

  function retryComparison(index, memberIndex) {
    const candidate = state.searchResults[index];
    if (!state.authorSearchDecisionPending && candidate?.kind === "author-match" && candidate.members[memberIndex]) {
      void loadMember(candidate, index, memberIndex);
    }
  }

  return { toggleComparison, retryComparison };
}
