// Settings-modal state and persistence.
import { state } from "./state.mjs?v=20260922-2";
import { $, escapeHtml, showToast, todayInTimeZone } from "./format.mjs?v=20260922-2";
import { api, invalidateRequests } from "./api.mjs?v=20260922-2";
import { closeModal, openModal } from "./modal-accessibility.mjs?v=20260922-2";
import { populateStorageSettings, readStorageSettings, bindClearCache } from "./storage-settings.mjs?v=20260923-4";
import { timeZoneSettings } from "./time-zone.mjs?v=20261010-2";


export function createSettingsController({
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
}) {
  const settingInputIds = {
    region: "setting-region",
    english_only: "setting-english-only",
    audnexus_enabled: "setting-audnexus",
    watchlist_chips_always_visible: "setting-watchlist-chips-always",
    header_sync_summary_visible: "setting-header-sync-summary",
    header_sync_dates_visible: "setting-header-sync-dates",
    header_sync_date_style: "setting-header-sync-date-style",
    close_settings_on_save: "setting-close-settings-on-save",
    automatic_sync_enabled: "setting-automatic-sync",
    sync_interval_hours: "setting-sync-interval",
    favourites_sync_enabled: "setting-favourites-sync",
    favourites_sync_interval_hours: "setting-favourites-sync-interval",
    date_display: "setting-date-display",
    time_zone: "setting-time-zone",
    default_release_scope: "setting-default-release-scope",
    default_category_id: "setting-default-category",
    default_category_window: "setting-default-category-window",
    default_category_days: "setting-default-category-days",
    cache_detail_minutes: "setting-detail-minutes",
    cache_search_minutes: "setting-search-minutes",
    cache_author_hours: "setting-author-hours",
    cache_series_hours: "setting-series-hours",
    cache_upcoming_hours: "setting-upcoming-hours",
    cache_far_future_hours: "setting-far-future-hours",
    cache_released_days: "setting-released-days",
    author_merge_min_shared_series: "setting-author-merge-min-shared-series",
  };
  const authInputIds = {
    username: "setting-auth-username",
    password: "setting-auth-password",
  };
  const settingsTabs = new Set(["catalogue", "authors", "app", "category", "sync", "users", "database", "cache"]);
  let activeSettingsTab = "catalogue";

  function selectSettingsTab(tabName) {
    activeSettingsTab = settingsTabs.has(tabName) ? tabName : "catalogue";
    document.querySelectorAll("[data-settings-tab]").forEach((tab) => {
      const active = tab.dataset.settingsTab === activeSettingsTab;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
      tab.tabIndex = active ? 0 : -1;
    });
    document.querySelectorAll("[data-settings-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.settingsPanel !== activeSettingsTab;
    });
    if (activeSettingsTab === "authors") loadAuthorManagement?.();
  }

  function updateSettingsDependencies() {
    const automaticSync = $("#setting-automatic-sync");
    const syncInterval = $("#setting-sync-interval");
    const favouritesSync = $("#setting-favourites-sync");
    const favouritesInterval = $("#setting-favourites-sync-interval");
    const favouritesSyncLabel = $("#setting-favourites-sync-label");
    const favouritesIntervalLabel = $("#setting-favourites-sync-interval-label");
    const fixedWindow = $("#setting-default-category-window")?.value === "fixed";
    const categoryDays = $("#setting-default-category-days");
    const categoryDaysLabel = $("#setting-default-category-days-label");
    const followedSyncEnabled = Boolean(automaticSync?.checked);
    const favouritesSyncDisabled = followedSyncEnabled;
    if (syncInterval) syncInterval.disabled = !followedSyncEnabled;
    if (favouritesSync) favouritesSync.disabled = favouritesSyncDisabled;
    if (favouritesInterval) favouritesInterval.disabled = favouritesSyncDisabled || !favouritesSync?.checked;
    if (favouritesSyncLabel) {
      favouritesSyncLabel.classList.toggle("is-disabled", favouritesSyncDisabled);
      favouritesSyncLabel.setAttribute("aria-disabled", String(favouritesSyncDisabled));
    }
    if (favouritesIntervalLabel) {
      const disabled = favouritesSyncDisabled || !favouritesSync?.checked;
      favouritesIntervalLabel.classList.toggle("is-disabled", disabled);
      favouritesIntervalLabel.setAttribute("aria-disabled", String(disabled));
    }
    if (categoryDays) categoryDays.disabled = !fixedWindow;
    if (categoryDaysLabel) {
      categoryDaysLabel.classList.toggle("is-disabled", !fixedWindow);
      categoryDaysLabel.setAttribute("aria-disabled", String(!fixedWindow));
    }
  }

  function updateDatabaseImportState() {
    const input = $("#setting-database-file");
    const button = $("#database-import");
    if (!button) return;
    const hasFile = Boolean(input?.files?.length);
    button.disabled = !hasFile;
    button.classList.toggle("button-primary", hasFile);
    button.classList.toggle("button-secondary", !hasFile);
  }

  function renderUsers(payload) {
    const list = $("#user-list");
    if (!list) return;
    const users = payload?.users || [];
    list.innerHTML = users.length
      ? users.map((user) => [
        '<div class="user-row">',
        '<div class="user-row-details">',
        '<strong>', escapeHtml(user.username), '</strong>',
        '<small>', escapeHtml(user.role), ' · ', user.active ? "Active" : "Disabled", user.password_is_default ? " · Default password" : "", '</small>',
        '</div>',
        '<div class="user-row-actions">',
        '<button class="button button-secondary" type="button" data-user-action="password" data-user-id="', escapeHtml(user.id), '" data-user-name="', escapeHtml(user.username), '">Set password</button>',
        user.id === payload.current_user_id ? "" : '<button class="button button-secondary" type="button" data-user-action="active" data-user-id="' + escapeHtml(user.id) + '" data-user-name="' + escapeHtml(user.username) + '" data-user-active="' + (user.active ? "true" : "false") + '">' + (user.active ? "Disable" : "Enable") + '</button>',
        '</div>',
        '</div>',
      ].join("")).join("")
      : '<p class="user-list-empty">No other users have been created.</p>';
  }

  async function loadUserManagement(currentUser) {
    const panel = $("#user-management");
    if (!panel) return;
    const isAdmin = Boolean(currentUser?.is_admin);
    panel.hidden = !isAdmin;
    if (!isAdmin) return;
    try {
      const payload = await api("/api/users");
      renderUsers({ ...payload, current_user_id: currentUser.id });
    } catch (error) {
      const message = $("#user-management-message");
      if (message) message.textContent = error.message;
    }
  }

  async function createManagedUser() {
    const username = $("#new-user-username")?.value.trim();
    const password = $("#new-user-password")?.value;
    const message = $("#user-management-message");
    if (!username || !password) {
      if (message) message.textContent = "Enter a username and temporary password.";
      return;
    }
    if (message) message.textContent = "Creating user…";
    try {
      await api("/api/users", { method: "POST", body: JSON.stringify({ username, password }) });
      $("#new-user-username").value = "";
      $("#new-user-password").value = "";
      if (message) message.textContent = "User created.";
      const currentUser = await api("/api/auth/session");
      const payload = await api("/api/users");
      renderUsers({ ...payload, current_user_id: currentUser.user_id });
    } catch (error) {
      if (message) message.textContent = error.message;
    }
  }

  async function updateManagedUser(userId, body, successMessage) {
    const message = $("#user-management-message");
    try {
      await api("/api/users/" + encodeURIComponent(userId), { method: "PUT", body: JSON.stringify(body) });
      if (message) message.textContent = successMessage;
      const currentUser = await api("/api/auth/session");
      const payload = await api("/api/users");
      renderUsers({ ...payload, current_user_id: currentUser.user_id });
    } catch (error) {
      if (message) message.textContent = error.message;
    }
  }

  async function handleUserManagementClick(event) {
    const button = event.target.closest("[data-user-action]");
    if (!button) return;
    const userId = button.dataset.userId;
    if (button.dataset.userAction === "active") {
      const active = button.dataset.userActive !== "true";
      if (!active && !window.confirm("Disable " + (button.dataset.userName || "this user") + "?")) return;
      await updateManagedUser(userId, { active }, active ? "User enabled." : "User disabled.");
      return;
    }
    const password = window.prompt("Set a new password for " + (button.dataset.userName || "this user") + ".");
    if (password === null) return;
    if (!password) {
      const message = $("#user-management-message");
      if (message) message.textContent = "Password cannot be empty.";
      return;
    }
    await updateManagedUser(userId, { password }, "Password updated.");
  }

  async function signOut() {
    await api("/api/auth/logout", { method: "POST" });
    window.location.reload();
  }

  function populateSettingsForm(payload, applyState = true) {
    populateStorageSettings(payload.storage_policy, payload.storage_usage);
    const { values, timeZoneOptions } = timeZoneSettings(payload);
    const region = $("#setting-region");
    if (region && payload.region_options) {
      region.innerHTML = payload.region_options.map((option) => `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`).join("");
    }
    const dateDisplay = $("#setting-date-display");
    if (dateDisplay && payload.date_display_options) {
      dateDisplay.innerHTML = payload.date_display_options.map((option) => `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`).join("");
    }
    const timeZone = $("#setting-time-zone");
    if (timeZone && payload.time_zone_options) {
      timeZone.innerHTML = timeZoneOptions.map((option) => `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`).join("");
    }
    const defaultCategory = $("#setting-default-category");
    if (defaultCategory && payload.category_options) defaultCategory.innerHTML = categoryOptionsHtml(payload.category_options);
    Object.entries(settingInputIds).forEach(([key, id]) => {
      const input = $(`#${id}`);
      if (!input || values[key] === undefined) return;
      if (input.type === "checkbox") input.checked = Boolean(values[key]);
      else input.value = values[key];
    });
    if (payload.auth) {
      const username = $(`#${authInputIds.username}`);
      const password = $(`#${authInputIds.password}`);
      if (username) username.value = payload.auth.username || "";
      if (password) password.value = "";
      const confirmation = $("#setting-auth-password-confirm");
      if (confirmation) confirmation.value = "";
      const warning = $("#settings-auth-warning");
      if (warning) {
        warning.textContent = payload.auth.password_is_default
          ? "Choose a new password of at least 12 characters to finish account setup."
          : "Leave the password blank to keep the current password.";
        warning.classList.toggle("is-warning", Boolean(payload.auth.password_is_default));
      }
    }
    const summary = $("#settings-user-summary");
    if (summary && payload.user) {
      summary.textContent = "Signed in as " + payload.user.username + " (" + payload.user.role + ").";
    }
    if (applyState) {
      state.settings = values;
      state.region = values.region || state.region;
      state.dateDisplay = values.date_display || state.dateDisplay;
      state.timeZone = values.time_zone || state.timeZone;
      if (values.cache_detail_minutes) state.detailCacheMs = Number(values.cache_detail_minutes) * 60 * 1000;
    }
    updateSettingsDependencies();
    updateDatabaseImportState();
  }

  async function loadSettingsPage() {
    const message = $("#settings-message");
    message.textContent = "Loading settings…";
    renderSyncStatus();
    try {
      const payload = await api("/api/settings");
      populateSettingsForm(payload);
      if (!state.setupRequired) await loadUserManagement(payload.user);
      message.textContent = "";
    } catch (error) {
      message.textContent = error.message;
    }
  }

  function showSettingsPage(tabName) {
    closeSearch();
    selectSettingsTab(typeof tabName === "string" ? tabName : activeSettingsTab);
    updateDatabaseImportState();
    openModal("#settings-modal", { initialFocus: "#settings-close" });
    loadSettingsPage();
    if (!state.setupRequired) pollSyncStatus();
  }

  function closeSettings() {
    closeModal("#settings-modal");
  }

  function togglePasswordVisibility() {
    const password = $("#setting-auth-password");
    const confirmation = $("#setting-auth-password-confirm");
    const toggle = $("#setting-auth-password-toggle");
    if (!password || !confirmation || !toggle) return;
    const visible = password.type === "text";
    password.type = visible ? "password" : "text";
    confirmation.type = visible ? "password" : "text";
    toggle.textContent = visible ? "Show" : "Hide";
    toggle.setAttribute("aria-pressed", String(!visible));
  }

  function updateExportProgress(message, startedAt, phase = "Preparing export archive") {
    const elapsedSeconds = Math.floor((Date.now() - startedAt) / 1000);
    const elapsed = elapsedSeconds < 60
      ? `${elapsedSeconds}s`
      : `${Math.floor(elapsedSeconds / 60)}m ${elapsedSeconds % 60}s`;
    message.textContent = `${phase}… ${elapsed} elapsed.`;
  }

  function exportFilename(response) {
    const disposition = response.headers.get("Content-Disposition") || "";
    const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    if (encoded) return decodeURIComponent(encoded);
    const quoted = disposition.match(/filename="([^"]+)"/i)?.[1];
    if (quoted) return quoted;
    const plain = disposition.match(/filename=([^;]+)/i)?.[1]?.trim();
    return plain || `audiblecalendar-${state.region}-export.zip`;
  }

  async function exportDatabase() {
    const button = $("#database-export");
    const message = $("#settings-message");
    if (!button || !message || button.disabled) return;
    const startedAt = Date.now();
    const progressTimer = window.setInterval(() => updateExportProgress(message, startedAt), 1000);
    button.disabled = true;
    button.classList.add("is-pending");
    button.setAttribute("aria-busy", "true");
    button.textContent = "Exporting…";
    updateExportProgress(message, startedAt);
    try {
      const response = await fetch("/api/database/export?region=" + encodeURIComponent(state.region), {
        credentials: "same-origin",
        headers: { "X-Audible-Region": state.region },
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || "Database export failed");
      }
      updateExportProgress(message, startedAt, "Downloading export archive");
      const archive = await response.blob();
      const downloadUrl = URL.createObjectURL(archive);
      const link = document.createElement("a");
      link.href = downloadUrl;
      link.download = exportFilename(response);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
      message.textContent = "Export complete. Download started.";
    } catch (error) {
      message.textContent = `Export failed: ${error.message}`;
    } finally {
      window.clearInterval(progressTimer);
      button.disabled = false;
      button.classList.remove("is-pending");
      button.removeAttribute("aria-busy");
      button.textContent = "Export archive";
    }
  }

  async function importDatabase() {
    const input = $("#setting-database-file");
    const button = $("#database-import");
    const message = $("#settings-message");
    const file = input?.files?.[0];
    if (!file) {
      message.textContent = "Choose an archive first.";
      return;
    }
    if (!window.confirm("Replace your current region’s library, cached covers and account settings with this archive? Other regions and your login will be kept.")) return;
    button.disabled = true;
    message.textContent = "Importing archive…";
    try {
      const response = await fetch("/api/database/import", {
        method: "PUT",
        headers: { "Content-Type": "application/octet-stream", "X-Audible-Region": state.region },
        body: file,
        credentials: "same-origin",
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "Database import failed");
      message.textContent = "Imported. Reloading…";
      window.setTimeout(() => window.location.reload(), 700);
    } catch (error) {
      message.textContent = error.message;
      updateDatabaseImportState();
    }
  }

  async function saveSettings(event) {
    event.preventDefault();
    const payload = { storage_policy: readStorageSettings() };
    Object.entries(settingInputIds).forEach(([key, id]) => {
      const input = $(`#${id}`);
      if (!input) return;
      payload[key] = input.type === "checkbox" ? input.checked : (input.type === "number" ? Number(input.value) : input.value);
    });
    const username = $("#setting-auth-username");
    const password = $("#setting-auth-password");
    const confirmation = $("#setting-auth-password-confirm");
    const message = $("#settings-message");
    if ((password?.value || confirmation?.value) && password?.value !== confirmation?.value) {
      message.textContent = "The new passwords do not match.";
      confirmation?.focus();
      return;
    }
    if (username?.value.trim()) payload.auth_username = username.value.trim();
    if (password?.value) payload.auth_password = password.value;
    message.textContent = "Saving settings…";
    try {
      const result = await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
      if (state.setupRequired || (result.values?.region && result.values.region !== state.region)) {
        invalidateRequests();
        window.location.reload();
        return;
      }
      const previousScope = state.settings?.default_release_scope;
      const previousTimeZone = state.timeZone;
      populateSettingsForm(result);
      if (!state.calendarPreferencesSaved && result.values?.default_release_scope !== previousScope) {
        state.releaseScope = result.values.default_release_scope;
        state.recentItems = null;
      }
      if (state.timeZone !== previousTimeZone) {
        const today = todayInTimeZone();
        state.cursor = new Date(today.getFullYear(), today.getMonth(), 1);
        state.historyLoadedFrom = null;
        state.historyLoadedUntil = null;
      }
      invalidateAllDetails();
      applyCategoryDefaults(true);
      if (state.releaseMode === "category") await loadCategoryCache();
      await Promise.all([loadWatches(), loadCalendar()]);
      if (activeSettingsTab === "authors") await loadAuthorManagement?.({ refreshCandidates: true });
      await pollSyncStatus({ refreshOnRevision: false });
      message.textContent = "Settings saved.";
      showToast("Settings saved");
      if (Boolean(result.values?.close_settings_on_save)) closeSettings();
    } catch (error) {
      message.textContent = error.message;
    }
  }

  async function resetSettings() {
    const message = $("#settings-message");
    message.textContent = "Loading defaults…";
    try {
      const payload = await api("/api/settings");
      populateSettingsForm({
        values: { ...payload.defaults, region: payload.values.region },
        storage_policy: payload.storage_defaults,
        storage_usage: payload.storage_usage,
        region_options: payload.region_options,
        date_display_options: payload.date_display_options,
        time_zone_options: payload.time_zone_options,
        category_options: payload.category_options,
      }, false);
      message.textContent = "Defaults loaded. Save settings to apply them.";
    } catch (error) {
      message.textContent = error.message;
    }
  }

  $("#new-user-submit")?.addEventListener("click", createManagedUser);
  $("#user-list")?.addEventListener("click", handleUserManagementClick);
  $("#session-signout")?.addEventListener("click", signOut);
  bindClearCache({ api, onCleared: () => { invalidateRequests(); invalidateAllDetails(); } });

  return {
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
  };
}
