// Background and manual catalog sync UI.
import { state } from "./state.mjs?v=20260922-2";
import { $, dateDisplayLocale, prettyDateTime, showToast, zonedDateKey } from "./format.mjs?v=20260922-2";
import { api } from "./api.mjs?v=20260922-2";

export function syncHeaderDate(value, now = new Date()) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const time = date.toLocaleTimeString(dateDisplayLocale(), {
    timeZone: state.timeZone, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });
  if (state.settings?.header_sync_dates_visible === false) return time;
  const key = zonedDateKey(date);
  const today = zonedDateKey(now);
  const [year, month, day] = key.split("-");
  let label = state.dateDisplay === "iso" ? `${month}-${day}`
    : state.dateDisplay === "uk" ? `${Number(day)}/${Number(month)}` : `${Number(month)}/${Number(day)}`;
  if (year !== today.slice(0, 4)) label = state.dateDisplay === "iso" ? key : `${label}/${year}`;
  if (state.settings?.header_sync_date_style !== "short") {
    // Compare calendar days in the selected zone, including across DST changes.
    const days = (Date.parse(`${key}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86400000;
    if (days === 0) label = "Today";
    else if (days === 1) label = "Tomorrow";
    else if (days === -1) label = "Yesterday";
    else if (Math.abs(days) < 7) label = date.toLocaleDateString(dateDisplayLocale(), { timeZone: state.timeZone, weekday: "short" });
  }
  return `${label} ${time}`;
}


export function createSyncController({
  invalidateAllDetails,
  loadCalendar,
  loadWatches,
  updateCalendarSourceControls,
}) {
  function updateSyncButton(syncing) {
    const button = $("#sync-button");
    if (button) {
      button.disabled = syncing;
      button.setAttribute("aria-label", syncing ? "Syncing…" : "Sync");
      button.title = syncing ? "Syncing…" : "Sync";
      button.classList.toggle("is-syncing", syncing);
    }
    const scheduledButton = $("#settings-scheduled-sync");
    if (scheduledButton) {
      scheduledButton.disabled = syncing;
      scheduledButton.textContent = syncing ? "Syncing…" : "Run scheduled sync now";
    }
  }

  function syncMetricsSummary(metrics, { includeZeroRequests = false } = {}) {
    if (!metrics) return "";
    const requests = Number(metrics.network_requests || 0);
    const parts = [];
    if (requests || includeZeroRequests) {
      const audible = Number(metrics.audible_requests || 0);
      const audnexus = Number(metrics.audnexus_requests || 0);
      if (audible && audnexus) parts.push(`${requests} upstream requests (${audible} Audible, ${audnexus} Audnexus)`);
      else if (audnexus) parts.push(`${audnexus} Audnexus ${audnexus === 1 ? "request" : "requests"}`);
      else parts.push(`${audible} Audible ${audible === 1 ? "request" : "requests"}`);
    }
    const cacheHits = Number(metrics.cache_hits || 0);
    const reused = Number(metrics.sync_deduplications || 0);
    const fallbacks = Number(metrics.stale_fallbacks || 0);
    if (cacheHits) parts.push(`${cacheHits} cache ${cacheHits === 1 ? "hit" : "hits"}`);
    if (reused) parts.push(`${reused} in-run ${reused === 1 ? "reuse" : "reuses"}`);
    if (fallbacks) parts.push(`${fallbacks} stale ${fallbacks === 1 ? "fallback" : "fallbacks"}`);
    return parts.join(" · ");
  }

  function updateHeaderSyncVisibility(status = state.syncStatus) {
    const summary = $("#header-sync-summary");
    const search = $("#search-form");
    if (!summary || !search || !status) return;
    if (state.settings?.header_sync_summary_visible === false) {
      const last = $("#header-last-sync");
      const next = $("#header-next-sync");
      if (last) last.textContent = "";
      if (next) next.textContent = "";
      summary.hidden = true;
      return;
    }
    summary.hidden = false;
    if (window.innerWidth <= 900) return;
    const searchRect = search.getBoundingClientRect();
    const summaryRect = summary.getBoundingClientRect();
    const overlapsSearch = searchRect.right + 12 > summaryRect.left;
    const overflowsWindow = summaryRect.right > window.innerWidth - 12;
    summary.hidden = overlapsSearch || overflowsWindow;
  }

  function renderHeaderSyncSummary(status = state.syncStatus) {
    const summary = $("#header-sync-summary");
    const last = $("#header-last-sync");
    const next = $("#header-next-sync");
    if (!summary || !last || !next) return;
    if (state.settings?.header_sync_summary_visible === false) {
      last.textContent = "";
      next.textContent = "";
      summary.hidden = true;
      return;
    }
    if (!status) {
      last.textContent = "";
      next.textContent = "";
      summary.hidden = true;
      return;
    }
    const lastValue = status.last_success_at ? new Date(status.last_success_at) : null;
    const nextValue = status.next_scheduled_at ? new Date(status.next_scheduled_at) : null;
    const automaticSyncActive = status.automatic_sync_scope !== undefined
      ? Boolean(status.automatic_sync_scope)
      : Boolean(status.automatic_sync_enabled);
    last.textContent = `Last: ${lastValue ? syncHeaderDate(status.last_success_at) : "—"}`;
    if (status.in_progress) next.textContent = "Next: Running…";
    else if (!automaticSyncActive) next.textContent = "Next: Off";
    else next.textContent = `Next: ${nextValue ? syncHeaderDate(status.next_scheduled_at) : "—"}`;
    summary.title = [
      status.last_success_at ? `Last successful sync ${prettyDateTime(status.last_success_at, { hourCycle: "h23" })}` : "No successful sync yet",
      status.in_progress ? "Sync currently running" : status.next_scheduled_at ? `Next automatic sync ${prettyDateTime(status.next_scheduled_at, { hourCycle: "h23" })}` : "Automatic sync is off",
    ].join(" · ");
    summary.hidden = false;
    window.requestAnimationFrame(() => updateHeaderSyncVisibility(status));
  }

  function syncProgressText(status) {
    const progress = status?.progress;
    if (!status?.in_progress) return "";
    if (!progress || progress.phase === "preparing") return "Preparing catalog sync…";
    const total = Number(progress.total_targets || 0);
    const completed = Number(progress.completed_targets || 0);
    const position = total ? `${Math.min(completed + 1, total)} of ${total}` : "local catalog";
    const kind = progress.current_kind ? `${progress.current_kind} ` : "";
    const name = progress.current_name || "Preparing targets";
    const step = progress.current_step
      ? ` · ${progress.current_step}${progress.current_page ? ` page ${progress.current_page}` : ""}`
      : "";
    const startedAt = new Date(progress.started_at).getTime();
    const elapsedSeconds = Number.isFinite(startedAt) ? Math.max(0, Math.floor((Date.now() - startedAt) / 1000)) : 0;
    const elapsed = elapsedSeconds < 60
      ? `${elapsedSeconds}s`
      : `${Math.floor(elapsedSeconds / 60)}m ${elapsedSeconds % 60}s`;
    const metrics = syncMetricsSummary(progress.metrics, { includeZeroRequests: true });
    return `Syncing ${position}: ${kind}${name}${step} · ${elapsed} elapsed${metrics ? ` · ${metrics}` : ""}`;
  }

  function renderSyncProgress(status = state.syncStatus) {
    const message = $("#calendar-message");
    if (!message) return;
    if (status?.in_progress && state.releaseMode !== "category") {
      message.dataset.syncProgress = "true";
      message.hidden = false;
      message.textContent = syncProgressText(status);
      return;
    }
    if (message.dataset.syncProgress === "true") {
      delete message.dataset.syncProgress;
      updateCalendarSourceControls();
    }
  }

  function renderSyncStatus(status = state.syncStatus) {
    renderHeaderSyncSummary(status);
    const message = $("#settings-sync-status");
    if (message) {
      if (!status) {
        message.textContent = "Checking sync schedule…";
      } else {
        const parts = [];
        if (status.scheduler_error) parts.push(status.scheduler_error);
        if (status.last_error) parts.push(status.last_error);
        if (status.in_progress) {
          parts.push(syncProgressText(status));
        } else if (status.last_status === "error") {
          parts.push(`Last sync failed${status.last_completed_at ? ` ${prettyDateTime(status.last_completed_at)}` : ""}.`);
        } else if (status.last_status === "partial") {
          parts.push(`Last sync completed with some errors${status.last_completed_at ? ` ${prettyDateTime(status.last_completed_at)}` : ""}.`);
        } else if (status.last_success_at) {
          parts.push(`Last successful sync ${prettyDateTime(status.last_success_at)}.`);
        } else {
          parts.push("No successful sync has completed yet.");
        }
        const automaticSyncActive = status.automatic_sync_scope !== undefined
          ? Boolean(status.automatic_sync_scope)
          : Boolean(status.automatic_sync_enabled);
        if (!automaticSyncActive) {
          parts.push("Automatic sync is off.");
        } else if (status.next_scheduled_at && !status.in_progress) {
          parts.push(`Next automatic sync ${prettyDateTime(status.next_scheduled_at)}.`);
        }
        const lastMetrics = syncMetricsSummary(status.last_metrics, { includeZeroRequests: true });
        if (lastMetrics && !status.in_progress) parts.push(`Last run: ${lastMetrics}.`);
        message.textContent = parts.join(" ");
      }
    }
    if (!state.manualSyncInProgress) updateSyncButton(Boolean(status?.in_progress));
    if (status?.in_progress) $("#sync-button").title = syncProgressText(status);
    renderSyncProgress(status);
  }

  async function pollSyncStatus({ refreshOnRevision = true } = {}) {
    if (!state.authenticated) return null;
    if (state.syncStatusRequest) return state.syncStatusRequest;
    state.syncStatusRequest = (async () => {
      try {
        const status = await api("/api/sync/status");
        const revision = Number(status.catalogue_revision || 0);
        const previousRevision = state.catalogueRevision;
        state.syncStatus = status;
        renderSyncStatus(status);
        if (previousRevision === null || !refreshOnRevision || revision === previousRevision) {
          state.catalogueRevision = revision;
          return status;
        }
        invalidateAllDetails();
        await Promise.all([loadWatches(), loadCalendar()]);
        state.catalogueRevision = revision;
        return status;
      } catch (_error) {
        return null;
      } finally {
        state.syncStatusRequest = null;
      }
    })();
    return state.syncStatusRequest;
  }

  function startSyncStatusPolling() {
    if (state.syncStatusTimer) return;
    state.syncStatusTimer = window.setInterval(() => {
      if (!document.hidden) pollSyncStatus();
    }, 5000);
  }

  window.addEventListener("audiblecalendar-auth-required", () => {
    if (!state.syncStatusTimer) return;
    window.clearInterval(state.syncStatusTimer);
    state.syncStatusTimer = null;
  });

  window.addEventListener("resize", () => updateHeaderSyncVisibility());

  async function syncNow({ scheduled = false } = {}) {
    state.manualSyncInProgress = true;
    updateSyncButton(true);
    renderSyncProgress({ in_progress: true, progress: { phase: "preparing" } });
    const progressTimer = window.setInterval(() => pollSyncStatus(), 1000);
    try {
      const path = scheduled ? "/api/sync/scheduled" : "/api/sync";
      const result = await api(path, { method: "POST" });
      invalidateAllDetails();
      const metrics = syncMetricsSummary(result.metrics, { includeZeroRequests: true });
      showToast(`${result.message || "Sync complete"}${metrics ? ` · ${metrics}` : ""}`);
      await Promise.all([loadWatches(), loadCalendar()]);
      await pollSyncStatus({ refreshOnRevision: false });
    } catch (error) {
      showToast(error.message);
      await pollSyncStatus();
    } finally {
      window.clearInterval(progressTimer);
      state.manualSyncInProgress = false;
      updateSyncButton(Boolean(state.syncStatus?.in_progress));
      renderSyncProgress(state.syncStatus);
    }
  }

  return {
    pollSyncStatus,
    renderSyncStatus,
    startSyncStatusPolling,
    syncNow,
    syncScheduledNow: () => syncNow({ scheduled: true }),
  };
}
