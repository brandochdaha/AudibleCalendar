import { state } from "./state.mjs?v=20260922-2";
import { $, categoryItemInRange, escapeHtml, releaseDateKey } from "./format.mjs?v=20260922-2";
import { api } from "./api.mjs?v=20260922-2";
import { captureModalFocus, closeModal, openModal, restoreModalFocus } from "./modal-accessibility.mjs?v=20260922-2";
import { detailRefreshButton, renderAuthorDetails, renderSeriesDetails, renderBookDetails } from "./detail-render.mjs?v=20260922-2";

export function createDetailNavigation({ loadWatches, loadCalendar, renderCalendar }) {
  function hideDetailsModal() {
    const modal = $("#details-modal");
    if (!modal) return;
    closeModal(modal);
    modal.classList.remove("book-details-modal");
    modal.querySelector(".modal")?.classList.remove("book-details-modal");
    modal.querySelector(".modal")?.style.removeProperty("width");
    state.activeDetailItem = null;
    state.activeAuthorDetails = null;
    state.activeSeriesDetails = null;
    state.activeBookDetails = null;
  }

  async function closeDetails() {
    const previous = state.detailModalHistory.pop();
    if (previous) {
      const previousItem = previous.item || previous;
      await openDetailsForItem(previousItem, { pushHistory: false });
      restoreModalFocus("#details-modal", previous.focusTarget);
      return;
    }
    hideDetailsModal();
  }

  function openDetails(index) {
    const item = state.searchResults[index];
    if (item) openDetailsForItem({ ...item, detailOrigin: "search" });
  }

  function detailCacheKey(item) {
    const groupKey = item?.kind === "author"
      ? (item.author_group_members || []).map((member) => member?.asin).filter(Boolean).sort().join(",")
      : "";
    return `${state.region}:${item?.kind || ""}:${item?.asin || ""}:${groupKey}`;
  }

  function invalidateAllDetails() {
    state.detailCache.clear();
    state.detailCacheEpoch += 1;
  }

  function renderDetailsPayload(item, details) {
    if (item.kind === "author") renderAuthorDetails(details);
    else if (item.kind === "series") renderSeriesDetails(details);
    else renderBookDetails(details);
  }

  function setRefreshButtonState(pending, error = false) {
    const button = document.querySelector("[data-details-refresh]");
    if (!button) return;
    const label = pending
      ? "Refreshing details from Audible"
      : error
        ? "Retry refresh from Audible"
        : "Refresh details from Audible";
    button.disabled = pending;
    button.classList.toggle("is-pending", pending);
    button.classList.toggle("is-error", error);
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
    if (pending) button.setAttribute("aria-busy", "true");
    else button.removeAttribute("aria-busy");
  }

  function mergeCategoryBookDetails(item, details) {
    if (item?.kind !== "book" || !details?.book) return;
    const categoryItem = state.categoryItems.find((candidate) => String(candidate.asin || "") === String(item.asin || ""));
    if (!categoryItem) return;
    const previousRange = categoryItemInRange(categoryItem);
    Object.assign(categoryItem, details.book, { category_in_range: previousRange });
    // A detail response may contain a newly corrected date. Keep the category
    // marker tied to the selected window while preserving the cached base row.
    const start = releaseDateKey(state.categoryResult?.start);
    const end = releaseDateKey(state.categoryResult?.end);
    const releaseDate = releaseDateKey(categoryItem.release_date);
    if (start && end && releaseDate) {
      categoryItem.category_in_range = releaseDate >= start && releaseDate <= end;
    }
    if (state.releaseMode === "category") renderCalendar();
  }

  async function fetchDetails(item, refresh = false) {
    const key = detailCacheKey(item);
    const cacheEpoch = state.detailCacheEpoch;
    const existingRequest = state.detailRequests.get(key);
    if (existingRequest) {
      if (!refresh) return existingRequest;
      await existingRequest.catch(() => {});
    }

    const params = new URLSearchParams({ asin: item.asin, name: item.name || item.title || "" });
    if (refresh) params.set("refresh", "true");
    let endpoint = "/api/author-details";
    if (item.kind === "series") {
      endpoint = "/api/series-details";
      if (item.seed_asin) params.set("seed_asin", item.seed_asin);
    } else if (item.kind === "book") {
      endpoint = "/api/book-details";
    } else {
      for (const member of item.author_group_members || []) {
        if (!member?.asin || !member?.name) continue;
        params.append("member_asin", member.asin);
        params.append("member_name", member.name);
      }
    }
    const request = (async () => {
      try {
        const details = await api(`${endpoint}?${params.toString()}`);
        mergeCategoryBookDetails(item, details);
        // Detail hydration can discover missing author/series relationships
        // and promote a standalone follow into a series follow. These local
        // watch/calendar reloads make no Audible calls.
        await Promise.all([loadWatches(), loadCalendar()]);
        if (!details.error && cacheEpoch === state.detailCacheEpoch) {
          state.detailCache.set(key, { details, cachedAt: Date.now() });
        }
        return details;
      } finally {
        state.detailRequests.delete(key);
      }
    })();
    state.detailRequests.set(key, request);
    return request;
  }

  async function loadDetailsForItem(item) {
    const cached = state.detailCache.get(detailCacheKey(item));
    if (cached && Date.now() - cached.cachedAt <= state.detailCacheMs) return cached.details;
    return fetchDetails(item);
  }

  async function openDetailsForItem(item, { pushHistory = true, refresh = false } = {}) {
    if (!item) return;
    if (pushHistory && !$("#details-modal").hidden && state.activeDetailItem) {
      state.detailModalHistory.push({
        item: { ...state.activeDetailItem },
        focusTarget: captureModalFocus("#details-modal"),
      });
    }
    state.activeDetailItem = item;
    state.activeAuthorDetails = null;
    state.activeSeriesDetails = null;
    state.activeBookDetails = null;
    const modal = $("#details-modal");
    modal.classList.toggle("book-details-modal", item.kind === "book");
    modal.querySelector(".modal")?.classList.toggle("book-details-modal", item.kind === "book");
    modal.querySelector(".modal")?.style.removeProperty("width");
    openModal(modal, { initialFocus: "#details-modal-close" });
    const key = detailCacheKey(item);
    const cached = state.detailCache.get(key);
    const isFresh = !refresh && cached && Date.now() - cached.cachedAt <= state.detailCacheMs;
    if (cached) {
      renderDetailsPayload(item, cached.details);
      mergeCategoryBookDetails(item, cached.details);
    }
    else $("#details-modal-content").innerHTML = `<div class="modal-loading"><span>Loading details…</span>${detailRefreshButton({ pending: true })}</div>`;
    setRefreshButtonState(!isFresh);
    if (isFresh) return;
    try {
      const details = await fetchDetails(item, refresh);
      if (detailCacheKey(state.activeDetailItem) !== key) return;
      renderDetailsPayload(item, details);
      setRefreshButtonState(false, Boolean(details?.error));
    } catch (error) {
      if (detailCacheKey(state.activeDetailItem) === key) {
        if (cached) renderDetailsPayload(item, { ...cached.details, error: error.message });
        else $("#details-modal-content").innerHTML = `<div class="modal-error"><span>${escapeHtml(error.message)}</span>${detailRefreshButton({ error: true })}</div>`;
        setRefreshButtonState(false, true);
      }
    }
  }

  function refreshDetails() {
    return openDetailsForItem(state.activeDetailItem, { pushHistory: false, refresh: true });
  }

  function openWatchDetailsFromElement(element) {
    if (!element) return false;
    let authorGroup;
    if (element.dataset.watchDetailsGroup) {
      try {
        authorGroup = JSON.parse(element.dataset.watchDetailsGroup);
      } catch (_error) {
        authorGroup = undefined;
      }
    }
    openDetailsForItem({
      kind: element.dataset.watchDetailsKind,
      asin: element.dataset.watchDetailsAsin,
      name: element.dataset.watchDetailsName,
      detailOrigin: element.dataset.detailOrigin,
      ...(Array.isArray(authorGroup) && authorGroup.length > 1 ? { author_group_members: authorGroup } : {}),
    });
    return true;
  }

  return { hideDetailsModal, closeDetails, openDetails, refreshDetails, invalidateAllDetails, openDetailsForItem, openWatchDetailsFromElement, loadDetailsForItem };
}
