// Installation-wide cleanup controls, available only to the administrator.
const fields = {
  cleanup_enabled: "setting-cleanup-enabled",
  cache_days: "setting-cache-retention-days",
  artwork_expiry_enabled: "setting-artwork-expiry-enabled",
  artwork_days: "setting-artwork-retention-days",
  response_cache_mb: "setting-response-cache-mb",
  artwork_cache_mb: "setting-artwork-cache-mb",
  backup_keep: "setting-backup-keep",
};
let clearing = false;

export function populateStorageSettings(policy, usage) {
  if (policy !== undefined) {
    const group = document.querySelector("#settings-storage-policy");
    group.hidden = !policy;
    group.disabled = !policy;
    if (policy) {
      for (const [key, id] of Object.entries(fields)) {
        const input = document.getElementById(id);
        if (input.type === "checkbox") input.checked = policy[key];
        else input.value = policy[key];
      }
    }
    updateClearCacheButton();
  }
  populateStorageUsage(usage);
}

export function readStorageSettings() {
  if (document.querySelector("#settings-storage-policy").disabled) return undefined;
  return Object.fromEntries(Object.entries(fields).map(([key, id]) => {
    const input = document.getElementById(id);
    return [key, input.type === "checkbox" ? input.checked : Number(input.value)];
  }));
}

export function bindClearCache({ api, onCleared }) {
  const button = document.querySelector("#settings-clear-cache");
  const message = document.querySelector("#settings-clear-cache-message");
  const cleanup = document.querySelector("#setting-cleanup-enabled");
  const artworkExpiry = document.querySelector("#setting-artwork-expiry-enabled");
  cleanup.addEventListener("change", updateClearCacheButton);
  artworkExpiry.addEventListener("change", updateClearCacheButton);
  updateClearCacheButton();

  button.addEventListener("click", async () => {
    if (clearing || document.querySelector("#settings-storage-policy").disabled) return;
    const selection = {
      cleanup_enabled: cleanup.checked,
      artwork_expiry_enabled: artworkExpiry.checked,
    };
    if (!selection.cleanup_enabled && !selection.artwork_expiry_enabled) return;
    const selected = [];
    if (selection.cleanup_enabled) selected.push("cached responses and detail snapshots");
    if (selection.artwork_expiry_enabled) selected.push("cover images, including imported covers");
    const selectionDescription = selected.length === 1
      ? selected[0]
      : `${selected.slice(0, -1).join(", ")} and ${selected.at(-1)}`;
    if (!window.confirm(`Clear ${selectionDescription} for ALL accounts and libraries?\n\nBooks, follows, favorites, settings, accounts and backups will be kept.${selection.artwork_expiry_enabled ? " Deleted imported covers may require the original export to restore." : ""}\n\nOnline data will be downloaded again as needed.`)) return;
    clearing = true;
    updateClearCacheButton();
    message.textContent = "Clearing cache…";
    try {
      const result = await api("/api/cache/clear", { method: "POST", body: JSON.stringify(selection) });
      onCleared();
      populateStorageUsage(result.storage_usage);
      const removed = [];
      if (selection.cleanup_enabled) removed.push(`${result.responses_removed} cached responses and detail snapshots`);
      if (selection.artwork_expiry_enabled) removed.push(`${result.artwork_removed} cover images`);
      message.textContent = `Cache cleared: ${removed.join(" and ")} removed.`;
    } catch (error) {
      message.textContent = error.message;
    } finally {
      clearing = false;
      updateClearCacheButton();
    }
  });
}

function populateStorageUsage(usage) {
  if (!usage) return;
  const responseSize = document.querySelector("#settings-response-cache-current");
  const artworkSize = document.querySelector("#settings-artwork-cache-current");
  const note = document.querySelector("#settings-storage-usage-note");
  if (responseSize) responseSize.textContent = formatBytes(usage.response_bytes);
  if (artworkSize) artworkSize.textContent = formatBytes(usage.artwork_bytes);
  if (note && usage.checked_at) {
    const checkedAt = new Date(usage.checked_at);
    if (!Number.isNaN(checkedAt.getTime())) {
      note.textContent = `Totals across all accounts and libraries. Response size counts cached payloads and keys; image size sums stored cover files. Measured at ${checkedAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}.`;
    }
  }
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "Unavailable";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length);
  return `${(bytes / (1024 ** exponent)).toFixed(1)} ${units[exponent - 1]}`;
}

function updateClearCacheButton() {
  const button = document.querySelector("#settings-clear-cache");
  const group = document.querySelector("#settings-storage-policy");
  if (!button || !group) return;
  const cleanup = document.querySelector("#setting-cleanup-enabled");
  const artworkExpiry = document.querySelector("#setting-artwork-expiry-enabled");
  button.disabled = clearing || group.disabled || (!cleanup.checked && !artworkExpiry.checked);
}
