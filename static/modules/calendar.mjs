// Calendar, list, day-modal, and upcoming/recent rendering.
import { exportCategoryCsv } from "./calendar-export.mjs?v=20260922-2";
import { state } from "./state.mjs?v=20260922-2";
import {
  $, categoryItemInRange, categoryItemOutsideRange, categoryOutsideClass, displayableBooks, escapeHtml,
  imageSrc, initials, iso, showToast, isDisplayableBook, monthLabel, prettyDate, releaseDateKey,
  todayInTimeZone, todayKey,
} from "./format.mjs?v=20260922-2";
import { canonicalAuthorName, collapseAuthors, displayableAuthors, formatAuthorNames } from "./identity-display.mjs?v=20260922-2";
import { api } from "./api.mjs?v=20260922-2";
import { closeModal, openModal } from "./modal-accessibility.mjs?v=20260922-2";

export function createCalendarController({ authorChip, renderWatchlist, seriesChip }) {
  let calendarRequest = 0;
  let recentRequest = 0;
  const upcomingViewFragments = new Map();
  const upcomingViewMarkup = new Map();
  let activeUpcomingViewKey = null;

  function renderUpcomingView(key, markup) {
    const list = $("#upcoming-list");
    if (!list) return;
    if (activeUpcomingViewKey !== key) {
      if (activeUpcomingViewKey) {
        const previousView = document.createDocumentFragment();
        while (list.firstChild) previousView.append(list.firstChild);
        upcomingViewFragments.set(activeUpcomingViewKey, previousView);
      }
      const nextView = upcomingViewFragments.get(key) || document.createDocumentFragment();
      list.replaceChildren(nextView);
      upcomingViewFragments.delete(key);
      activeUpcomingViewKey = key;
    }
    if (upcomingViewMarkup.get(key) !== markup) {
      list.innerHTML = markup;
      upcomingViewMarkup.set(key, markup);
    }
  }

  function clearUpcomingViews() {
    calendarRequest += 1;
    recentRequest += 1;
    upcomingViewFragments.clear();
    upcomingViewMarkup.clear();
    activeUpcomingViewKey = null;
    $("#upcoming-list")?.replaceChildren();
  }

  function calendarRange() {
    const first = new Date(state.cursor.getFullYear(), state.cursor.getMonth(), 1);
    const start = new Date(first);
    start.setDate(first.getDate() - first.getDay());
    // The calendar always renders six complete weeks below. Keep the loaded
    // and filtered range aligned with every cell, including the extra
    // trailing days after the month's final week.
    const end = new Date(start);
    end.setDate(start.getDate() + 41);
    return { start: iso(start), end: iso(end) };
  }

  function upcomingRange() {
    const start = todayInTimeZone();
    const end = new Date(start);
    end.setDate(end.getDate() + 365);
    return { start: iso(start), end: iso(end) };
  }

  async function loadCalendar({ reuse = false } = {}) {
    const request = ++calendarRequest;
    const range = calendarRange();
    const nextRange = upcomingRange();
    const fetchEnd = range.end > nextRange.end ? range.end : nextRange.end;
    const previousYear = todayInTimeZone();
    previousYear.setFullYear(previousYear.getFullYear() - 1);
    const desiredStart = state.showHistory ? (range.start < iso(previousYear) ? range.start : iso(previousYear)) : todayKey();
    const fetchStart = state.historyLoadedFrom && state.historyLoadedFrom < desiredStart ? state.historyLoadedFrom : desiredStart;
    // Fetch history only when requested; retain ranges already visited.
    if (!reuse || !state.historyLoadedUntil || state.historyLoadedUntil < fetchEnd || !state.historyLoadedFrom || state.historyLoadedFrom > fetchStart) {
      const result = await api(`/api/calendar?start=${fetchStart}&end=${fetchEnd}&include_past=true`);
      if (request !== calendarRequest) return;
      state.historyItems = result.items || [];
      state.historyLoadedUntil = fetchEnd;
      state.historyLoadedFrom = fetchStart;
    }
    const allItems = state.historyItems;
    const today = todayKey();
    state.historyItems = allItems;
    state.upcomingItems = allItems.filter((item) => {
      const releaseDate = releaseDateKey(item.release_date);
      return isDisplayableBook(item) && releaseDate >= today && releaseDate <= nextRange.end;
    });
    recentRequest += 1;
    state.recentScope = null;
    renderCalendar();
    renderUpcoming({ fetchRecent: false });
    if (state.upcomingMode === "recent") await loadRecent();
  }

  function activeCalendarItems() {
    if (state.releaseMode === "category") return displayableBooks(state.categoryItems);
    const range = calendarRange();
    const today = todayKey();
    // Apply the current view's filters on every render, including toggle
    // changes and returning from a historical month without another fetch.
    return displayableBooks(state.historyItems)
      .filter((item) => {
        const releaseDate = releaseDateKey(item.release_date);
        return releaseDate >= range.start && releaseDate <= range.end
          && (state.showHistory || releaseDate >= today);
      })
      .filter((item) => state.releaseScope !== "favourites" || item.is_favourite);
  }

  function categoryListItems() {
    return displayableBooks(state.categoryItems)
      .filter(categoryItemInRange)
      .sort((a, b) => (a.release_date || "").localeCompare(b.release_date || "") || (a.title || "").localeCompare(b.title || ""));
  }

  function renderCalendarList() {
    const list = $("#calendar-list");
    if (!list) return;
    const today = todayKey();
    const sourceItems = state.releaseMode === "category"
      ? state.categoryItems
      : state.showHistory ? state.historyItems : state.upcomingItems;
    const items = state.releaseMode === "category"
      ? categoryListItems()
      : [...sourceItems]
        .filter((item) => isDisplayableBook(item)
          && (state.releaseScope !== "favourites" || item.is_favourite)
          && (state.showHistory || releaseDateKey(item.release_date) >= today))
        .sort((a, b) => (a.release_date || "").localeCompare(b.release_date || "") || (a.title || "").localeCompare(b.title || ""));
    if (!items.length) {
      const scopeLabel = state.releaseScope === "favourites" ? "favorite" : "followed";
      list.innerHTML = `<div class="empty-state">${state.releaseMode === "category" ? "Load a category overview to see its releases." : state.showHistory ? `No cached ${scopeLabel} releases are available.` : `No upcoming ${scopeLabel} releases are scheduled.`}</div>`;
      if (state.showHistory && state.releaseMode === "calendar") list.innerHTML += '<button type="button" class="button button-secondary" data-earlier-history>Load an earlier year</button>';
      return;
    }
    const months = [];
    const monthGroups = new Map();
    items.forEach((item) => {
      const key = item.release_date.slice(0, 7);
      if (!monthGroups.has(key)) {
        const group = { key, label: monthLabel(new Date(`${key}-01T12:00:00`)), items: [] };
        monthGroups.set(key, group);
        months.push(group);
      }
      monthGroups.get(key).items.push(item);
    });
    const renderItem = (item) => {
      const authorChips = collapseAuthors(displayableAuthors(item.authors || [])).map(authorChip).filter(Boolean);
      const seriesChips = (item.series || []).map(seriesChip).filter(Boolean);
      const chips = [...authorChips, ...seriesChips];
      const chipRow = chips.length ? `<div class="release-list-chips">${chips.join("")}</div>` : "";
      const noCover = categoryItemOutsideRange(item) && !item.image_url;
      const cover = noCover ? "" : calendarCoverMarkup(item, "calendar-day-cover release-list-cover");
      const title = escapeHtml(item.title);
      return `<article class="release-list-card${categoryOutsideClass(item)}${noCover ? " category-no-cover" : ""}">${cover}<div class="release-list-card-copy"><p class="release-list-date">${escapeHtml(prettyDate(item.release_date))}</p><button class="release-list-title-button" type="button" data-calendar-book-asin="${escapeHtml(item.asin)}" data-calendar-book-name="${title}">${title}</button>${chipRow}</div></article>`;
    };
    list.innerHTML = months.map((group) => `<section class="release-list-month"><h3 class="release-list-month-heading">${escapeHtml(group.label)}</h3><div class="release-list-month-items">${group.items.map(renderItem).join("")}</div></section>`).join("");
    if (state.showHistory && state.releaseMode === "calendar") {
      list.innerHTML += '<button type="button" class="button button-secondary" data-earlier-history>Load an earlier year</button>';
    }
  }

  async function loadWatches() {
    state.watches = await api("/api/watches");
    renderWatchlist();
  }

  function renderCalendar() {
    const currentDate = todayInTimeZone();
    const currentMonth = new Date(currentDate.getFullYear(), currentDate.getMonth(), 1);
    if (!state.showHistory && state.cursor < currentMonth) state.cursor = currentMonth;
    const categoryLabel = state.categoryResult?.category?.label || state.categoryResult?.category?.name || "Category overview";
    $("#calendar-title").textContent = state.releaseMode === "watchlist"
      ? "Your watchlist"
      : state.releaseMode === "category" && state.displayMode === "calendar"
        ? monthLabel(state.cursor)
        : state.releaseMode === "category"
          ? categoryLabel
          : state.displayMode === "list"
            ? (state.showHistory ? "Loaded releases" : "Upcoming releases")
            : monthLabel(state.cursor);
    $("#watchlist-view").hidden = state.releaseMode !== "watchlist";
    $("#calendar").hidden = state.releaseMode === "watchlist" || state.displayMode !== "calendar";
    $("#calendar-list").hidden = state.releaseMode === "watchlist" || state.displayMode !== "list";
    $(".calendar-controls").hidden = state.releaseMode === "watchlist" || state.displayMode === "list";
    const releaseTabTools = $(".release-tab-tools");
    if (releaseTabTools) {
      const toolsHidden = state.releaseMode === "watchlist";
      // Hide calendar-only tools while keeping the shared scope toggle in the
      // release tab row when Watchlist is active.
      releaseTabTools.classList.toggle("is-hidden", toolsHidden);
      releaseTabTools.setAttribute("aria-hidden", toolsHidden ? "true" : "false");
    }
    const releaseScopeToggle = $("#release-scope-toggle");
    if (releaseScopeToggle) {
      releaseScopeToggle.hidden = state.releaseMode === "category";
    }
    const historyToggle = $("#history-toggle");
    if (historyToggle) {
      historyToggle.hidden = state.releaseMode !== "calendar";
      historyToggle.classList.toggle("is-active", state.showHistory);
      historyToggle.setAttribute("aria-pressed", state.showHistory ? "true" : "false");
      historyToggle.title = state.showHistory ? "Show current and upcoming releases only" : "Show previous releases";
    }
    const categoryExport = $("#category-export");
    if (categoryExport) {
      const isCategoryList = state.releaseMode === "category" && state.displayMode === "list";
      categoryExport.hidden = !isCategoryList;
      categoryExport.disabled = !state.categoryResult;
    }
    const cursorMonth = new Date(state.cursor.getFullYear(), state.cursor.getMonth(), 1);
    const monthNavigation = state.releaseMode === "calendar" || state.releaseMode === "category";
    const canShowEarlier = state.releaseMode === "calendar" && state.showHistory
      ? true
      : cursorMonth > currentMonth;
    $("#previous-month").disabled = !monthNavigation || !canShowEarlier;
    document.querySelectorAll("[data-release-mode]").forEach((button) => {
      const active = button.dataset.releaseMode === state.releaseMode;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
      button.tabIndex = active ? 0 : -1;
    });
    document.querySelectorAll("[data-display-mode]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.displayMode === state.displayMode);
    });
    document.querySelectorAll("[data-release-scope]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.releaseScope === state.releaseScope);
    });
    if (state.displayMode === "list") {
      renderCalendarList();
      return;
    }
    const range = calendarRange();
    const start = new Date(`${range.start}T12:00:00`);
    const currentDay = todayKey();
    const events = new Map();
    activeCalendarItems().forEach((item) => {
      const key = item.release_date?.slice(0, 10);
      if (key) events.set(key, [...(events.get(key) || []), item]);
    });
    state.calendarDays = Object.fromEntries(events);
    const weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    let html = '<div class="calendar-grid">';
    html += weekdays.map((day) => `<div class="weekday">${day}</div>`).join("");
    for (let index = 0; index < 42; index += 1) {
      const day = new Date(start);
      day.setDate(start.getDate() + index);
      const key = iso(day);
      const outside = day.getMonth() !== state.cursor.getMonth();
      const today = key === currentDay;
      const dayItems = events.get(key) || [];
      const categoryOutsideDay = state.releaseMode === "category"
        && dayItems.length
        && dayItems.every(categoryItemOutsideRange);
      let dayEvents = "";
      if (categoryOutsideDay) {
        dayEvents = renderCategoryOutOfRangeDay(dayItems, key);
      } else if (dayItems.length) {
        const tileCount = Math.min(dayItems.length, 4);
        const visibleItems = dayItems.length > 4 ? dayItems.slice(0, 3) : dayItems;
        const tiles = visibleItems.map((item) => {
          const title = escapeHtml(item.title);
          const image = item.image_url ? `<img src="${escapeHtml(imageSrc(item.image_url))}" alt="" loading="lazy" />` : "";
          return `<button class="calendar-event${categoryOutsideClass(item)}" type="button" data-calendar-book-asin="${escapeHtml(item.asin)}" data-calendar-book-name="${title}" title="${title}" aria-label="${title}">${image}<span class="calendar-event-label">${title}</span></button>`;
        });
        if (dayItems.length > 4) {
          tiles.push(`<button class="calendar-event calendar-more${dayItems.some(categoryItemOutsideRange) ? " category-out-of-range" : ""}" type="button" data-calendar-more="${key}" aria-label="Show all ${dayItems.length} releases on this day">+${dayItems.length - 3} more</button>`);
        }
        dayEvents = `<div class="day-events day-events-${tileCount}">${tiles.join("")}</div>`;
      }
      html += `<div class="day ${outside ? "outside" : ""} ${today ? "today" : ""}${categoryOutsideDay ? " category-outside-day" : ""}"><span class="day-number">${day.getDate()}</span>${dayEvents}</div>`;
    }
    html += "</div>";
    $("#calendar").innerHTML = html;
  }

  function renderCategoryOutOfRangeDay(items, key) {
    const coverItem = items.find((item) => item.image_url);
    const buttonForBook = (item, content, className = "") => {
      const title = escapeHtml(item.title);
      return `<button class="calendar-event ${className} category-out-of-range" type="button" data-calendar-book-asin="${escapeHtml(item.asin)}" data-calendar-book-name="${title}" title="${title}" aria-label="${title}">${content}</button>`;
    };
    const tiles = [];
    if (coverItem) {
      const title = escapeHtml(coverItem.title);
      const image = `<img src="${escapeHtml(imageSrc(coverItem.image_url))}" alt="" loading="lazy" />`;
      tiles.push(buttonForBook(coverItem, `${image}<span class="calendar-event-label">${title}</span>`, "category-cover-event"));
      if (items.length > 1) {
        tiles.push(`<button class="calendar-event calendar-more category-out-of-range category-category-more" type="button" data-calendar-more="${escapeHtml(key)}" aria-label="Show all ${items.length} releases on this day">+${items.length - 1} more</button>`);
      }
    } else {
      items.slice(0, 2).forEach((item) => {
        const title = escapeHtml(item.title);
        tiles.push(buttonForBook(item, `<span class="calendar-category-text">${title}</span>`, "category-text-event"));
      });
      if (items.length > 2) {
        tiles.push(`<button class="calendar-event calendar-more category-out-of-range category-category-more" type="button" data-calendar-more="${escapeHtml(key)}" aria-label="Show all ${items.length} releases on this day">+${items.length - 2} more</button>`);
      }
    }
    const summaryType = coverItem ? "category-cover-summary" : "category-text-summary";
    const rowCount = coverItem ? Math.min(items.length, 2) : Math.min(items.length, 3);
    return `<div class="day-events day-events-category-summary ${summaryType} category-summary-${rowCount} category-out-of-range">${tiles.join("")}</div>`;
  }

  function closeCalendarDay() {
    closeModal("#calendar-day-modal");
  }

  function calendarCoverMarkup(item, className = "calendar-day-cover", eager = false) {
    const image = item.image_url
     ? `<img src="${escapeHtml(imageSrc(item.image_url))}" alt=""${eager ? "" : " loading=\"lazy\""} />`
      : escapeHtml(initials(item.title));
    return `<span class="${className}">${image}</span>`;
  }

  function openCalendarDay(key) {
    const items = state.calendarDays[key] || [];
    if (!items.length) return;
    const count = items.length;
    const releases = items.map((item) => {
      const title = escapeHtml(item.title);
      const authors = item.authors?.length ? formatAuthorNames(item.authors) : "";
      const series = item.series?.[0]?.name;
      const detail = [authors, series].filter(Boolean).join(" · ");
      const noCover = categoryItemOutsideRange(item) && !item.image_url;
      const cover = noCover ? "" : calendarCoverMarkup(item, "calendar-day-cover", true);
      return `<button class="calendar-day-release${categoryOutsideClass(item)}${noCover ? " category-no-cover" : ""}" type="button" data-calendar-book-asin="${escapeHtml(item.asin)}" data-calendar-book-name="${title}">${cover}<span class="calendar-day-release-copy"><strong>${title}</strong>${detail ? `<small>${escapeHtml(detail)}</small>` : ""}</span></button>`;
    }).join("");
    $("#calendar-day-modal-content").innerHTML = `
      <div class="calendar-day-heading">
        <p class="eyebrow">RELEASES</p>
        <h2 id="calendar-day-modal-title">${escapeHtml(prettyDate(key))}</h2>
        <p class="muted">${count} release${count === 1 ? "" : "s"} scheduled for this day.</p>
      </div>
      <div class="calendar-day-list">${releases}</div>`;
    openModal("#calendar-day-modal", { initialFocus: "#calendar-day-modal-close" });
  }

  function renderUpcoming({ fetchRecent = true } = {}) {
    const today = todayKey();
    const recent = state.upcomingMode === "recent";
    const viewKey = `${state.releaseScope}:${recent ? "recent" : "next"}`;
    if (fetchRecent && recent && state.recentScope !== state.releaseScope) {
      state.recentScope = state.releaseScope;
      loadRecent().catch((error) => showToast(error.message));
    }
    const candidates = [...displayableBooks(recent ? (state.recentItems || state.historyItems) : state.upcomingItems)]
      .filter((item) => state.releaseScope !== "favourites" || item.is_favourite)
      .filter((item) => {
        const releaseDate = releaseDateKey(item.release_date);
        return releaseDate && (recent ? releaseDate < today : releaseDate >= today);
      })
      .sort((a, b) => recent
        ? (b.release_date || "").localeCompare(a.release_date || "")
        : (a.release_date || "").localeCompare(b.release_date || ""))
      .slice(0, 5);
    // Keep all five release tiles, but avoid repeating the same date chip when
    // several books launch together. The first tile for a date carries its
    // label; the following covers remain visible without one.
    const seenDates = new Set();
    const items = candidates.map((item) => {
      const dateKey = item.release_date.slice(0, 10);
      const showDate = !seenDates.has(dateKey);
      seenDates.add(dateKey);
      return { ...item, showDate };
    });
    $("#upcoming-title").textContent = recent ? "Last" : "Next";
    const modeToggle = $("#upcoming-mode-toggle");
    modeToggle.setAttribute("aria-label", recent ? "Show next releases" : "Show last releases");
    modeToggle.setAttribute("aria-pressed", recent ? "true" : "false");
    modeToggle.title = recent ? "Show next releases" : "Show last releases";
    if (!items.length) {
      renderUpcomingView(viewKey, recent
        ? `<div class="empty-state">No previous ${state.releaseScope === "favourites" ? "favorite " : ""}releases are cached. Toggle History to load past releases.</div>`
        : `<div class="empty-state">No upcoming ${state.releaseScope === "favourites" ? "favorite " : ""}releases are scheduled. Add an author, series or book to start building your watchlist.</div>`);
      return;
    }
    const markup = items.map((item) => {
      const authorChips = collapseAuthors(displayableAuthors(item.authors || [])).map(authorChip).filter(Boolean);
      const seriesChips = (item.series || []).map(seriesChip).filter(Boolean);
      const authorRow = authorChips.length ? `<div class="upcoming-chip-row upcoming-author-row">${authorChips.join("")}</div>` : "";
      const seriesRow = seriesChips.length ? `<div class="upcoming-chip-row upcoming-series-row">${seriesChips.join("")}</div>` : "";
      const title = escapeHtml(item.title);
      const cover = item.image_url
        ? `<img src="${escapeHtml(imageSrc(item.image_url))}" alt="" loading="lazy" />`
        : `<span class="upcoming-cover-fallback">AQ</span>`;
      const date = item.showDate ? `<div class="release-date upcoming-release-date">${escapeHtml(prettyDate(item.release_date))}</div>` : "";
      return `<article class="upcoming-card" tabindex="0" role="button" data-upcoming-book-asin="${escapeHtml(item.asin)}" data-upcoming-book-name="${title}" aria-label="${title}"><div class="upcoming-cover">${cover}${date}<div class="upcoming-overlay"><h3>${title}</h3>${authorRow}${seriesRow}</div></div></article>`;
    }).join("");
    renderUpcomingView(viewKey, markup);
  }

  function toggleHistory() {
    state.showHistory = !state.showHistory;
    renderCalendar();
    if (state.showHistory) loadCalendar({ reuse: true }).catch((error) => showToast(error.message));
  }

  async function loadRecent() {
    const request = ++recentRequest;
    const scope = state.releaseScope;
    state.recentScope = scope;
    const yesterday = todayInTimeZone();
    yesterday.setDate(yesterday.getDate() - 1);
    try {
      const result = await api(`/api/calendar?start=0001-01-01&end=${iso(yesterday)}&include_past=true&descending=true&limit=5&favourites_only=${scope === "favourites"}`);
      if (request !== recentRequest || scope !== state.releaseScope) return;
      state.recentItems = result.items || [];
      renderUpcoming();
    } catch (error) {
      if (request !== recentRequest || scope !== state.releaseScope) return;
      state.recentScope = null;
      throw error;
    }
  }

  async function loadEarlierHistory() {
    const start = new Date(`${state.historyLoadedFrom || todayKey()}T12:00:00`);
    start.setFullYear(start.getFullYear() - 1);
    state.historyLoadedFrom = iso(start);
    await loadCalendar();
  }

  function toggleUpcomingMode() {
    state.upcomingMode = state.upcomingMode === "recent" ? "next" : "recent";
    renderUpcoming();
  }

  return {
    calendarRange,
    closeCalendarDay,
    clearUpcomingViews,
    loadCalendar,
    loadWatches,
    openCalendarDay,
    exportCategoryCsv: () => exportCategoryCsv(categoryListItems()),
    loadEarlierHistory,
    renderCalendar,
    renderUpcoming,
    toggleHistory,
    toggleUpcomingMode,
  };
}
