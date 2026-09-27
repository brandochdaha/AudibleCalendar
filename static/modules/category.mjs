// Category selection, cache loading, and explicit Audible refresh flow.
import { state } from "./state.mjs?v=20260922-2";
import { $, escapeHtml, prettyDateTime, showToast, todayInTimeZone } from "./format.mjs?v=20260922-2";
import { api } from "./api.mjs?v=20260922-2";

export function createCategoryController({
  invalidateAllDetails,
  pollSyncStatus,
  renderCalendar,
}) {
  function categoryOptionsHtml(categories) {
    return (categories || []).map((group) => {
      const options = [`<option value="${escapeHtml(group.id)}">${escapeHtml(group.name)} (all)</option>`]
        .concat((group.children || []).map((child) => `<option value="${escapeHtml(child.id)}">${escapeHtml(child.name)}</option>`));
      return `<optgroup label="${escapeHtml(group.name)}">${options.join("")}</optgroup>`;
    }).join("");
  }

  function categoryExists(categoryId) {
    return state.categories.some((group) => group.id === categoryId || (group.children || []).some((child) => child.id === categoryId));
  }

  function remainingMonthDays() {
    const today = todayInTimeZone();
    const start = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    const monthEnd = new Date(today.getFullYear(), today.getMonth() + 1, 0);
    return Math.max(1, Math.round((monthEnd - start) / 86400000));
  }

  function applyCategoryDefaults(force = false) {
    const select = $("#category-select");
    const input = $("#category-days");
    const preferredCategory = state.settings?.default_category_id || "18580606011";
    if (select && (force || !select.value) && categoryExists(preferredCategory)) select.value = preferredCategory;
    if (input && (force || !input.value)) {
      input.value = String(state.settings?.default_category_window === "fixed"
        ? Number(state.settings?.default_category_days || 30)
        : remainingMonthDays());
    }
  }

  function populateCategorySelect() {
    const select = $("#category-select");
    if (!select || !state.categories.length) return;
    select.innerHTML = categoryOptionsHtml(state.categories);
    applyCategoryDefaults(true);
  }

  async function loadCategories() {
    const result = await api("/api/categories");
    state.categories = result.categories || [];
    if (!state.settings?.default_category_id) state.settings.default_category_id = result.default_id;
    populateCategorySelect();
  }

  function categorySelection() {
    return {
      categoryId: $("#category-select")?.value || "",
      days: Number($("#category-days")?.value),
    };
  }

  function applyCategoryResult(result) {
    state.categoryItems = result.items || [];
    state.categoryResult = result;
    state.releaseMode = "category";
    state.cursor = new Date(`${result.start}T12:00:00`);
  }

  function updateCategoryLastChecked(result) {
    const lastChecked = $("#category-last-checked");
    if (!lastChecked) return;
    if (!result) {
      lastChecked.hidden = true;
      lastChecked.textContent = "";
      return;
    }
    const checkedAt = result.cached_at
      ? prettyDateTime(result.cached_at)
      : "now";
    lastChecked.hidden = false;
    lastChecked.textContent = `Last checked ${checkedAt}`;
  }

  function clearCategoryOverview() {
    state.categoryItems = [];
    state.categoryResult = null;
    updateCalendarSourceControls();
    renderCalendar();
    if (state.releaseMode === "category") loadCategoryCache();
  }

  function updateCalendarSourceControls() {
    const categoryControls = $("#category-overview-controls");
    if (categoryControls) categoryControls.hidden = state.releaseMode !== "category";
    updateCategoryLastChecked(state.releaseMode === "category" ? state.categoryResult : null);
    const message = $("#calendar-message");
    if (!message) return;
    if (state.releaseMode === "calendar") {
      message.textContent = "";
      message.hidden = true;
   } else if (state.releaseMode === "watchlist") {
      message.hidden = true;
      message.textContent = "";
    } else if (!state.categoryResult) {
      message.hidden = false;
      message.textContent = "Choose a category and day window to see saved results. Use Refresh from Audible to check for updates. Only manual refreshes update this calendar.";
    } else {
      message.hidden = true;
      message.textContent = "";
    }
  }

  async function loadCategoryCache() {
    const { categoryId, days } = categorySelection();
    const requestId = ++state.categoryCacheRequest;
    if (!categoryId || !Number.isInteger(days) || days < 1 || days > 3650) {
      updateCalendarSourceControls();
      renderCalendar();
      return;
    }
    const message = $("#calendar-message");
    message.hidden = false;
    message.textContent = "Loading saved category results…";
    try {
      const result = await api(`/api/category-upcoming?category_id=${encodeURIComponent(categoryId)}&days=${days}`);
      if (requestId !== state.categoryCacheRequest || state.releaseMode !== "category") return;
      if (!result.cached) {
        state.categoryItems = [];
        state.categoryResult = null;
        updateCalendarSourceControls();
        renderCalendar();
        return;
      }
      applyCategoryResult(result);
      updateCalendarSourceControls();
      renderCalendar();
    } catch (error) {
      if (requestId !== state.categoryCacheRequest || state.releaseMode !== "category") return;
      state.categoryItems = [];
      state.categoryResult = null;
      message.hidden = false;
      message.textContent = `Saved category results could not be loaded: ${error.message}`;
      renderCalendar();
    }
  }

  async function refreshCategoryOverview() {
    const { categoryId, days } = categorySelection();
    const requestId = ++state.categoryCacheRequest;
    if (!categoryId || !Number.isInteger(days) || days < 1 || days > 3650) {
      showToast("Choose a category and a day window from 1 to 3650 days");
      return;
    }
    const button = $("#category-refresh");
    const message = $("#calendar-message");
    await pollSyncStatus();
    const sourceBaseline = Number(state.syncStatus?.source_metrics?.network_requests || 0);
    const startedAt = Date.now();
    const elapsedLabel = () => {
      const seconds = Math.floor((Date.now() - startedAt) / 1000);
      return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    };
    const updateProgress = () => {
      const requests = Math.max(0, Number(state.syncStatus?.source_metrics?.network_requests || 0) - sourceBaseline);
      const requestLabel = requests ? ` ${requests} upstream ${requests === 1 ? "page" : "pages"} requested.` : "";
      message.hidden = false;
      message.textContent = `Refreshing category releases… ${elapsedLabel()} elapsed.${requestLabel} Audible may be paging through many results; rate limiting can make this take longer.`;
    };
    button.disabled = true;
    button.textContent = "Refreshing…";
    updateProgress();
    const progressTimer = window.setInterval(async () => {
      await pollSyncStatus();
      updateProgress();
    }, 1000);
    try {
      const result = await api("/api/category-upcoming", {
        method: "POST",
        body: JSON.stringify({ category_id: categoryId, days, force: true }),
      });
      if (requestId !== state.categoryCacheRequest || state.releaseMode !== "category") return;
      applyCategoryResult(result);
      // Category checks are one of the two explicit freshness signals for
      // persistent book/series details. The server returns its saved payload
      // when nothing changed, so this only makes the next modal read local
      // cached data rather than holding an old in-memory rendering.
      invalidateAllDetails();
      updateCalendarSourceControls();
      renderCalendar();
      showToast("Category overview refreshed");
    } catch (error) {
      if (requestId === state.categoryCacheRequest && state.releaseMode === "category") {
        message.hidden = false;
        message.textContent = `Category refresh failed after ${elapsedLabel()}: ${error.message}`;
        showToast(error.message);
      }
    } finally {
      window.clearInterval(progressTimer);
      button.disabled = false;
      button.textContent = "Refresh from Audible";
    }
  }

  return {
    applyCategoryDefaults,
    categoryOptionsHtml,
    clearCategoryOverview,
    loadCategories,
    loadCategoryCache,
    refreshCategoryOverview,
    updateCalendarSourceControls,
  };
}
