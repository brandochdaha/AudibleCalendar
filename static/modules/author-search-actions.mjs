import { state } from "./state.mjs?v=20260922-2";
import { showToast } from "./format.mjs?v=20260922-2";
import { api } from "./api.mjs?v=20260922-2";
import { authorComparisonsLoading } from "./author-comparison.mjs?v=20260922-2";

export function createAuthorSearchActions({ renderSearchResults, reloadSearchResults, invalidateAllDetails, loadWatches, loadCalendar }) {
  async function decideAuthorSearchMatch(index, decision) {
    const candidate = state.searchResults[index];
    if (candidate?.kind !== "author-match" || state.authorSearchDecisionPending || authorComparisonsLoading()) return;
    const context = state.searchContext;
    const results = state.searchResults;
    state.authorSearchDecisionPending = true;

    candidate.pending_decision = decision;
    renderSearchResults();
    try {
      await api("/api/author-search-decision", {
        method: "POST",
        body: JSON.stringify({
          decision,
          manual: Boolean(candidate.manual_merge),
          override_separation: Boolean(candidate.manual_merge && candidate.overrides_separation),
          primary_asin: candidate.primary_asin,
          members: (candidate.members || []).map((member) => ({
            asin: member.asin,
            name: member.name,
            source_asins: member.source_asins || [member.asin],
            source_groups: member.source_groups,
          })),
        }),
      });
    } catch (error) {
      delete candidate.pending_decision;
      if (error.name !== "AbortError") {
        try {
          if (state.searchContext === context) await reloadSearchResults(context);
        } catch (_refreshError) { /* Preserve the original save error. */ }
        showToast(`Could not save author decision: ${error.message}`);
      }
      state.authorSearchDecisionPending = false;
      renderSearchResults();
      return;
    }

    // Every overlapping card depends on the changed ownership. Never reuse a
    // client-side proposed merge, or splice into a newer search's results.
    if (state.searchResults === results) {
      state.searchResults = [];
      state.manualAuthorMerge = null;
    }
    renderSearchResults();
    invalidateAllDetails();
    try {
      await Promise.all([
        loadWatches(), loadCalendar(),
        state.searchContext === context ? reloadSearchResults(context) : Promise.resolve(),
      ]);
      showToast(decision === "merge" ? "Authors merged" : "Authors kept separate");
    } catch (error) {
      if (error.name !== "AbortError") showToast(`Decision saved, but views could not be refreshed: ${error.message}`);
    } finally {
      state.authorSearchDecisionPending = false;
      renderSearchResults();
    }
  }

  return { decideAuthorSearchMatch };
}
