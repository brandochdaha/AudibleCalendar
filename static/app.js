import { createDetailClickHandler } from "./modules/detail-events.mjs?v=20260922-2";
import { createFollowActions } from "./modules/follow-actions.mjs?v=20260922-2";
import { createAuthorReviews } from "./modules/author-reviews.mjs?v=20260922-3";
import { createAuthorSearchActions } from "./modules/author-search-actions.mjs?v=20260922-2";
import { createAuthorComparison } from "./modules/author-comparison.mjs?v=20260922-2";
import { createManualAuthorMerge } from "./modules/manual-author-merge.mjs?v=20260922-2";
import { createAuthorManagementController } from "./modules/author-management.mjs?v=20260923-4";
import { createDetailNavigation } from "./modules/detail-navigation.mjs?v=20260927-1";
import { renderAuthorDetails, refreshBookCover, fitBookDetailsModal, toggleBookDescription } from "./modules/detail-render.mjs?v=20260922-2";
import { followedAuthor, followedBook, followedRecord, authorChip, seriesChip, renderWatchlist, renderSearchResults, doSearch, reloadSearchResults } from "./modules/watch-display.mjs?v=20260922-2";
import { state, resetState } from "./modules/state.mjs?v=20260922-2";
import { $, showToast, todayInTimeZone } from "./modules/format.mjs?v=20260922-2";

import { api, invalidateRequests } from "./modules/api.mjs?v=20260922-2";
import { createAuthController } from "./modules/auth.mjs?v=20260922-2";
import { createCalendarController } from "./modules/calendar.mjs?v=20260923-8";
import { createCategoryController } from "./modules/category.mjs?v=20260923-5";
import { closeAllModalLayers, closeModal, handleTablistKeydown } from "./modules/modal-accessibility.mjs?v=20260922-2";
import { createSettingsController } from "./modules/settings.mjs?v=20261010-2";
import { createSyncController } from "./modules/sync-ui.mjs?v=20261010-1";
import { restoreCalendarPreferences, saveCalendarPreferences } from "./modules/calendar-preferences.mjs?v=20260927-2";

const {
  calendarRange,
  closeCalendarDay,
  clearUpcomingViews,
  exportCategoryCsv,
  loadCalendar,
  loadEarlierHistory,
  loadWatches,
  openCalendarDay,
  renderCalendar,
  renderUpcoming,
  toggleHistory,
  toggleUpcomingMode,
} = createCalendarController({ authorChip, renderWatchlist, seriesChip });

const { hideDetailsModal, closeDetails, openDetails, refreshDetails, invalidateAllDetails, openDetailsForItem, openWatchDetailsFromElement, loadDetailsForItem } = createDetailNavigation({ loadWatches, loadCalendar, renderCalendar });
const { toggleComparison, retryComparison } = createAuthorComparison({ loadDetailsForItem });
const { startManualMerge, selectManualAuthor, reviewManualMerge, cancelManualMerge } = createManualAuthorMerge({ renderSearchResults });
const { closeAuthorReviews } = createAuthorReviews({ invalidateAllDetails, loadWatches, loadCalendar });
const { decideAuthorSearchMatch } = createAuthorSearchActions({ renderSearchResults, reloadSearchResults, invalidateAllDetails, loadWatches, loadCalendar });
const { closeAuthorSplitConfirmation, loadAuthorManagement } = createAuthorManagementController({ invalidateAllDetails, loadWatches, loadCalendar });

const { addWatch, addAuthorGroupWatch, toggleFavourite, favouriteStandaloneBook, closeBookAuthorPrompt, confirmBookAuthorPrompt, closeAuthorUnfollowConfirmation, requestAuthorUnfollow, confirmAuthorUnfollow, closeItemUnfollowConfirmation, requestItemUnfollow, confirmItemUnfollow, updateAuthorSeries, updateAuthorBook, removeWatch } = createFollowActions({ loadWatches, loadCalendar, closeDetails });

const {
  applyCategoryDefaults,
  categoryOptionsHtml,
  clearCategoryOverview,
  loadCategories,
  loadCategoryCache,
  refreshCategoryOverview,
  updateCalendarSourceControls,
} = createCategoryController({
  invalidateAllDetails,
  pollSyncStatus: (...args) => pollSyncStatus(...args),
  renderCalendar,
});

function closeAllModals() {
  closeAllModalLayers(() => {
    state.detailModalHistory = [];
    hideDetailsModal();
    closeSearch();
    closeSettings();
    closeCalendarDay();
    closeBookAuthorPrompt(null);
    closeAuthorUnfollowConfirmation();
    closeItemUnfollowConfirmation();
    closeAuthorReviews();
    closeAuthorSplitConfirmation();
  });
}

function closeSearch() {
  closeModal("#search-results-panel");
}

const {
  pollSyncStatus,
  renderSyncStatus,
  startSyncStatusPolling,
  syncNow,
  syncScheduledNow,
} = createSyncController({
  invalidateAllDetails,
  loadCalendar,
  loadWatches,
  updateCalendarSourceControls,
});

const {
  closeSettings,
  exportDatabase,
  importDatabase,
  resetSettings,
  saveSettings,
  selectSettingsTab,
  showSettingsPage,
  togglePasswordVisibility,
  updateDatabaseImportState,
  updateSettingsDependencies,
} = createSettingsController({
  applyCategoryDefaults,
  categoryOptionsHtml,
  closeSearch,
  invalidateAllDetails,
  loadCalendar,
  loadCategoryCache,
  loadWatches,
  loadAuthorManagement,
  pollSyncStatus,
  renderSyncStatus,
});

function shiftMonth(amount) {
  const today = todayInTimeZone();
  const currentMonth = new Date(today.getFullYear(), today.getMonth(), 1);
  const nextMonth = new Date(state.cursor.getFullYear(), state.cursor.getMonth() + amount, 1);
  if ((!state.showHistory || state.releaseMode !== "calendar") && nextMonth < currentMonth) return;
  state.cursor = nextMonth;
  if (state.releaseMode === "category") renderCalendar();
  else loadCalendar({ reuse: true }).catch((error) => showToast(error.message));
}

function setReleaseMode(mode) {
  state.releaseMode = mode;
  updateCalendarSourceControls();
  renderCalendar();
  if (mode === "category") loadCategoryCache();
  else state.categoryCacheRequest += 1;
}

async function boot(session) {
  state.authenticated = true;
  state.setupRequired = Boolean(session?.password_is_default);
  if (state.setupRequired) {
    state.region = (await api("/api/config")).region;
    showSettingsPage("users");
    return;
  }
  try {
    const config = await api("/api/config");
    state.region = config.region || "us";
    state.dateDisplay = config.date_display || "us";
    state.timeZone = config.time_zone || "America/Los_Angeles";
    restoreCalendarPreferences(session?.user_id, config.default_release_scope);
    const today = todayInTimeZone();
    state.cursor = new Date(today.getFullYear(), today.getMonth(), 1);
    state.settings = {
      automatic_sync_enabled: config.automatic_sync_enabled,
      sync_interval_hours: config.sync_interval_hours,
      favourites_sync_enabled: config.favourites_sync_enabled,
      favourites_sync_interval_hours: config.favourites_sync_interval_hours,
      watchlist_chips_always_visible: config.watchlist_chips_always_visible,
      header_sync_summary_visible: config.header_sync_summary_visible,
      header_sync_dates_visible: config.header_sync_dates_visible,
      header_sync_date_style: config.header_sync_date_style,
      close_settings_on_save: config.close_settings_on_save,
      date_display: state.dateDisplay,
      time_zone: state.timeZone,
      default_release_scope: config.default_release_scope || "favourites",
      default_category_id: config.default_category_id,
      default_category_window: config.default_category_window,
      default_category_days: config.default_category_days,
    };
    if (config.cache?.detail_minutes) state.detailCacheMs = Number(config.cache.detail_minutes) * 60 * 1000;
    await Promise.all([loadWatches(), loadCalendar(), loadCategories()]);
  } catch (error) {
    showToast(error.message);
  } finally {
    await pollSyncStatus({ refreshOnRevision: false });
    if (state.authenticated) startSyncStatusPolling();
  }
}

const auth = createAuthController({
  onAuthenticated: boot,
  onSignedOut() {
    invalidateRequests();
    closeAllModals();
    resetState();
    clearUpcomingViews();
    renderWatchlist();
    renderCalendar();
    renderUpcoming();
    for (const selector of ["#details-modal-content", "#search-results", "#setting-auth-username", "#setting-auth-password", "#setting-auth-password-confirm", "#user-list", "#session-summary"]) {
      const element = $(selector);
      if (element) { element.textContent = ""; if ("value" in element) element.value = ""; }
    }
  },
});

$("#search-form").addEventListener("submit", doSearch);
$("#previous-month").addEventListener("click", () => shiftMonth(-1));
$("#next-month").addEventListener("click", () => shiftMonth(1));
$("#today-button").addEventListener("click", () => {
  const today = todayInTimeZone();
  const currentMonth = new Date(today.getFullYear(), today.getMonth(), 1);
  const cursorMonth = new Date(state.cursor.getFullYear(), state.cursor.getMonth(), 1);
  if (cursorMonth.getTime() === currentMonth.getTime()) return;
  state.cursor = currentMonth;
  if (state.releaseMode === "category" || state.releaseMode === "watchlist") renderCalendar();
  else loadCalendar({ reuse: true }).catch((error) => showToast(error.message));
});
document.querySelectorAll("[data-release-mode]").forEach((button) => {
  button.addEventListener("click", () => setReleaseMode(button.dataset.releaseMode));
});
$(".release-tabs").addEventListener("keydown", (event) => handleTablistKeydown(event, "[data-release-mode]"));
document.querySelectorAll("[data-display-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    state.displayMode = button.dataset.displayMode;
    saveCalendarPreferences();
    renderCalendar();
  });
});
document.querySelectorAll("[data-release-scope]").forEach((button) => {
  button.addEventListener("click", () => {
    state.releaseScope = button.dataset.releaseScope === "favourites" ? "favourites" : "followed";
    saveCalendarPreferences();
    document.querySelectorAll("[data-release-scope]").forEach((candidate) => {
      candidate.classList.toggle("is-active", candidate.dataset.releaseScope === state.releaseScope);
    });
    renderCalendar();
    renderUpcoming();
    renderWatchlist();
  });
});
$("#category-refresh").addEventListener("click", refreshCategoryOverview);
$("#category-select").addEventListener("change", clearCategoryOverview);
$("#category-days").addEventListener("change", clearCategoryOverview);
$("#category-export").addEventListener("click", exportCategoryCsv);
$("#sync-button").addEventListener("click", syncNow);
$("#settings-scheduled-sync").addEventListener("click", syncScheduledNow);
$("#history-toggle").addEventListener("click", () => {
  toggleHistory();
  saveCalendarPreferences();
});
$("#upcoming-mode-toggle").addEventListener("click", toggleUpcomingMode);
$("#settings-button").addEventListener("click", showSettingsPage);
$("#settings-close").addEventListener("click", closeSettings);
$("#settings-form").addEventListener("submit", saveSettings);
document.querySelectorAll("[data-settings-tab]").forEach((button) => {
  button.addEventListener("click", () => selectSettingsTab(button.dataset.settingsTab));
});
$("#settings-tabs").addEventListener("keydown", (event) => handleTablistKeydown(event, "[data-settings-tab]"));
$("#settings-reset").addEventListener("click", resetSettings);
$("#setting-auth-password-toggle").addEventListener("click", togglePasswordVisibility);
$("#database-export").addEventListener("click", exportDatabase);
$("#database-import").addEventListener("click", importDatabase);
$("#setting-database-file").addEventListener("change", updateDatabaseImportState);
$("#setting-automatic-sync").addEventListener("change", updateSettingsDependencies);
$("#setting-favourites-sync").addEventListener("change", updateSettingsDependencies);
$("#setting-default-category-window").addEventListener("change", updateSettingsDependencies);
$("#search-results-close").addEventListener("click", closeSearch);
$("#manual-author-merge-controls").addEventListener("click", (event) => {
  if (event.target.closest("[data-manual-merge-start]")) startManualMerge();
  else if (event.target.closest("[data-manual-merge-review]")) reviewManualMerge();
  else if (event.target.closest("[data-manual-merge-cancel]")) cancelManualMerge();
});
$("#search-results").addEventListener("change", (event) => {
  if (event.target.matches("[data-manual-merge-select]")) {
    selectManualAuthor(Number(event.target.dataset.manualMergeSelect), event.target.checked);
  }
});
$("#search-results").addEventListener("toggle", (event) => {
  if (event.target.matches("[data-author-match-comparison]")) {
    toggleComparison(Number(event.target.dataset.authorMatchComparison), event.target.open);
  }
}, true);
$("#search-results").addEventListener("click", (event) => {
  if (event.target.closest("[data-manual-merge-cancel]")) { cancelManualMerge(); return; }
  const retry = event.target.closest("[data-author-comparison-retry]");
  if (retry) {
    retryComparison(Number(retry.dataset.authorComparisonRetry), Number(retry.dataset.authorComparisonMember));
    return;
  }
  const authorMatch = event.target.closest("[data-author-match-decision]");
  if (authorMatch) {
    decideAuthorSearchMatch(
      Number(authorMatch.dataset.authorMatchIndex),
      authorMatch.dataset.authorMatchDecision,
    );
    return;
  }
  const relation = event.target.closest("[data-watch-details]");
  if (openWatchDetailsFromElement(relation)) return;
  const follow = event.target.closest("[data-follow-search-index]");
  if (follow) {
    const index = Number(follow.dataset.followSearchIndex);
    const item = state.searchResults[index];
    if (item) {
      if (item.kind === "author") {
        const action = item.author_group_members?.length > 1
          ? addAuthorGroupWatch(item, follow)
          : addWatch(item, follow);
        action.then((succeeded) => { if (succeeded) openDetails(index); });
      } else {
        const action = item.author_group_members?.length > 1
          ? addAuthorGroupWatch(item, follow)
          : addWatch(item, follow);
        action.then((succeeded) => { if (succeeded) closeSearch(); });
      }
    }
    return;
  }
  const button = event.target.closest("[data-details-index]");
  if (button) openDetails(Number(button.dataset.detailsIndex));
});
$("#calendar").addEventListener("click", (event) => {
  const more = event.target.closest("[data-calendar-more]");
  if (more) {
    openCalendarDay(more.dataset.calendarMore);
    return;
  }
  const book = event.target.closest("[data-calendar-book-asin]");
  if (book) openDetailsForItem({ kind: "book", asin: book.dataset.calendarBookAsin, name: book.dataset.calendarBookName });
});
$("#calendar-list").addEventListener("click", (event) => {
  if (event.target.closest("[data-earlier-history]")) {
    loadEarlierHistory().catch((error) => showToast(error.message));
    return;
  }
  const details = event.target.closest("[data-watch-details]");
  if (openWatchDetailsFromElement(details)) return;
  const book = event.target.closest("[data-calendar-book-asin]");
  if (book) openDetailsForItem({ kind: "book", asin: book.dataset.calendarBookAsin, name: book.dataset.calendarBookName });
});
$("#calendar-day-modal-close").addEventListener("click", closeCalendarDay);
$("#calendar-day-modal").addEventListener("click", (event) => {
  if (event.target.id === "calendar-day-modal") {
    closeAllModals();
    return;
  }
  const book = event.target.closest("[data-calendar-book-asin]");
  if (book) openDetailsForItem({ kind: "book", asin: book.dataset.calendarBookAsin, name: book.dataset.calendarBookName });
});
$("#details-modal-close").addEventListener("click", closeDetails);
$("#search-results-panel").addEventListener("click", (event) => { if (event.target.id === "search-results-panel") closeAllModals(); });
$("#settings-modal").addEventListener("click", (event) => { if (event.target.id === "settings-modal") closeAllModals(); });
$("#follow-authors-close").addEventListener("click", () => closeBookAuthorPrompt(null));
$("#follow-authors-cancel").addEventListener("click", () => closeBookAuthorPrompt([]));
$("#follow-authors-confirm").addEventListener("click", confirmBookAuthorPrompt);
$("#follow-authors-modal").addEventListener("click", (event) => {
  if (event.target.id === "follow-authors-modal") closeAllModals();
});
$("#author-unfollow-close").addEventListener("click", closeAuthorUnfollowConfirmation);
$("#author-unfollow-cancel").addEventListener("click", closeAuthorUnfollowConfirmation);
$("#author-unfollow-confirm").addEventListener("click", confirmAuthorUnfollow);
$("#author-unfollow-modal").addEventListener("click", (event) => {
  if (event.target.id === "author-unfollow-modal") closeAllModals();
});
$("#item-unfollow-close").addEventListener("click", closeItemUnfollowConfirmation);
$("#item-unfollow-cancel").addEventListener("click", closeItemUnfollowConfirmation);
$("#item-unfollow-confirm").addEventListener("click", confirmItemUnfollow);
$("#item-unfollow-modal").addEventListener("click", (event) => {
  if (event.target.id === "item-unfollow-modal") {
    closeAllModals();
    return;
  }
  const toggle = event.target.closest("[data-unfollow-author-id]");
  if (toggle) {
    const pressed = toggle.getAttribute("aria-pressed") === "true";
    toggle.setAttribute("aria-pressed", pressed ? "false" : "true");
  }
});
const handleDetailClick = createDetailClickHandler({ closeAllModals, refreshDetails, favouriteStandaloneBook, toggleFavourite, openWatchDetailsFromElement, openDetailsForItem, requestAuthorUnfollow, addAuthorGroupWatch, closeDetails, addWatch, updateAuthorSeries, updateAuthorBook, requestItemUnfollow });
$("#details-modal").addEventListener("click", (event) => {
  const authorManagementLink = event.target.closest("[data-open-author-management]");
  if (authorManagementLink) {
    event.preventDefault();
    state.detailModalHistory = [];
    hideDetailsModal();
    showSettingsPage("authors");
    return;
  }
  handleDetailClick(event);
});
$("#details-modal").addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const description = event.target.closest("[data-description-expand]");
  if (!description) return;
  event.preventDefault();
  toggleBookDescription(description);
});
window.addEventListener("resize", fitBookDetailsModal);
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!$("#author-split-modal").hidden) closeAuthorSplitConfirmation();
  else if (!$("#author-reviews-modal").hidden) closeAuthorReviews();
  else if (!$("#follow-authors-modal").hidden) closeBookAuthorPrompt(null);
  else if (!$("#item-unfollow-modal").hidden) closeItemUnfollowConfirmation();
  else if (!$("#author-unfollow-modal").hidden) closeAuthorUnfollowConfirmation();
  else if (!$("#details-modal").hidden) closeDetails();
  else if (!$("#calendar-day-modal").hidden) closeCalendarDay();
  else if (!$("#settings-modal").hidden) closeSettings();
  else if (!$("#search-results-panel").hidden) closeSearch();
});
$("#watchlist").addEventListener("click", (event) => {
  const favourite = event.target.closest("[data-favourite-id]");
  if (favourite) {
    toggleFavourite(
      favourite.dataset.favouriteKind,
      favourite.dataset.favouriteId,
      favourite.getAttribute("aria-pressed") === "true",
      favourite,
    );
    return;
  }
  const details = event.target.closest("[data-watch-details]");
  if (openWatchDetailsFromElement(details)) return;
 const button = event.target.closest("[data-remove-id]");
  if (button) {
    const kind = button.dataset.removeKind;
    if (kind === "author") requestAuthorUnfollow({ id: Number(button.dataset.removeId) });
    else removeWatch(kind, Number(button.dataset.removeId));
  }
});
$("#upcoming-list").addEventListener("click", (event) => {
  const details = event.target.closest("[data-watch-details]");
  if (openWatchDetailsFromElement(details)) return;
  const book = event.target.closest("[data-upcoming-book-asin]");
  if (book) openDetailsForItem({ kind: "book", asin: book.dataset.upcomingBookAsin, name: book.dataset.upcomingBookName });
});
$("#upcoming-list").addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const book = event.target.closest("[data-upcoming-book-asin]");
  if (!book) return;
  event.preventDefault();
  openDetailsForItem({ kind: "book", asin: book.dataset.upcomingBookAsin, name: book.dataset.upcomingBookName });
});
$("#watch-tabs").addEventListener("click", (event) => {
  const tab = event.target.closest("[data-watch-tab]");
  if (!tab) return;
  state.watchTab = tab.dataset.watchTab;
  renderWatchlist();
});
$("#watch-tabs").addEventListener("keydown", (event) => handleTablistKeydown(event, "[data-watch-tab]"));
updateCalendarSourceControls();
auth.checkSession();
