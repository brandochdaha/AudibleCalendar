// Spreadsheet-safe category exports.
import { state } from "./state.mjs?v=20260922-2";
import { releaseDateKey, safeAudibleUrl } from "./format.mjs?v=20260922-2";
import { canonicalAuthorName, collapseAuthors, displayableAuthors } from "./identity-display.mjs?v=20260922-2";

function csvCell(value) {
  let text = String(value ?? "");
  if (/^[\s\u0000-\u001f]*[=+@-]/u.test(text) || /^[\t\r\n]/u.test(text)) text = "'" + text;
  return `"${text.replace(/"/g, '""')}"`;
}
export function exportCategoryCsv(items) {
  if (state.releaseMode !== "category" || !state.categoryResult) return;
  const headers = ["Title", "Release date", "Authors", "Series", "ASIN", "Audible URL", "Language", "Pre-order", "Available"];
  const rows = items.map((item) => [
    item.title,
    releaseDateKey(item.release_date),
    collapseAuthors(displayableAuthors(item.authors || [])).map(canonicalAuthorName).filter(Boolean).join(", "),
    (item.series || []).map((series) => series.display_name || series.name || series.title).filter(Boolean).join(", "),
    item.asin,
    safeAudibleUrl(item.audible_url),
    item.language,
    item.is_preorder ? "Yes" : "No",
    item.is_available == null ? "" : item.is_available ? "Yes" : "No",
  ]);
  const csv = [headers, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
  const blob = new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" });
  const downloadUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const label = state.categoryResult.category?.label || state.categoryResult.category?.name || "category";
  const safeLabel = String(label).normalize("NFKD").replace(/[^a-z0-9]+/gi, "-").replace(/^-+|-+$/g, "").toLowerCase() || "category";
  const start = releaseDateKey(state.categoryResult.start) || "start";
  const end = releaseDateKey(state.categoryResult.end) || "end";
  link.href = downloadUrl;
  link.download = `${safeLabel}-releases-${start}-to-${end}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 0);
}
