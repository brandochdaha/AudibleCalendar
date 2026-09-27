import { state } from "./state.mjs?v=20260922-2";

const storageKey = () => `audiblecalendar:calendar-view:${state.userId}`;

export function restoreCalendarPreferences(userId, defaultScope) {
  state.userId = userId;
  state.releaseScope = defaultScope || "favourites";
  state.calendarPreferencesSaved = false;
  if (userId == null) return;
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey()));
    if (!saved || typeof saved !== "object") return;
    if (typeof saved.showHistory === "boolean") state.showHistory = saved.showHistory;
    if (["calendar", "list"].includes(saved.displayMode)) state.displayMode = saved.displayMode;
    if (["followed", "favourites"].includes(saved.releaseScope)) {
      state.releaseScope = saved.releaseScope;
      state.calendarPreferencesSaved = true;
    }
  } catch { /* Storage may be unavailable or contain invalid data. */ }
}

export function saveCalendarPreferences() {
  state.calendarPreferencesSaved = true;
  if (state.userId == null) return;
  try {
    localStorage.setItem(storageKey(), JSON.stringify({
      showHistory: state.showHistory,
      displayMode: state.displayMode,
      releaseScope: state.releaseScope,
    }));
  } catch { /* Keep the controls usable when browser storage is unavailable. */ }
}
