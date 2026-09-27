// Frontend display and date helpers.
import { state } from "./state.mjs?v=20260922-2";

export const $ = (selector) => document.querySelector(selector);
export const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
export const imageSrc = (value, refresh = false) => {
  if (!value) return "";
  const query = `url=${encodeURIComponent(value)}`;
  const version = refresh ? (state.imageRefreshVersions.get(value) || Date.now()) : state.imageRefreshVersions.get(value);
  const refreshQuery = refresh ? "&refresh=1" : "";
  const versionQuery = version ? `&v=${version}` : "";
  return `/api/image?${query}${refreshQuery}${versionQuery}`;
};
export const iso = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
export const dateDisplayLocale = () => state.dateDisplay === "uk" ? "en-GB" : "en-US";
export const zonedDateKey = (date = new Date()) => {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: state.timeZone, year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${values.year}-${values.month}-${values.day}`;
};
export const todayKey = () => zonedDateKey();
export const todayInTimeZone = () => new Date(`${todayKey()}T12:00:00`);
export const monthLabel = (date) => state.dateDisplay === "iso"
  ? `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`
  : date.toLocaleDateString(dateDisplayLocale(), { month: "long", year: "numeric" });
export const validReleaseDate = (value) => {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T12:00:00`);
  return !Number.isNaN(parsed.getTime()) && iso(parsed) === value;
};
export const safeAudibleUrl = (value) => {
  try {
    const url = new URL(value);
    const domains = ["com", "co.uk", "ca", "com.au", "de", "fr", "es", "it", "co.jp", "co.in", "com.br"];
    return url.protocol === "https:" && !url.username && !url.password && (!url.port || url.port === "443")
      && domains.some((domain) => url.hostname === `www.audible.${domain}` || url.hostname === `audible.${domain}`)
      ? url.href : "";
  } catch { return ""; }
};
export const prettyDate = (value) => {
  if (!validReleaseDate(value)) return "Date unknown";
  const date = new Date(`${value.slice(0, 10)}T12:00:00`);
  if (Number.isNaN(date.getTime())) return "Date unknown";
  if (state.dateDisplay === "iso") return iso(date);
  return date.toLocaleDateString(dateDisplayLocale(), { month: "short", day: "numeric", year: "numeric" });
};
export const prettyDateTime = (value, { hourCycle } = {}) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Date unknown";
  if (state.dateDisplay === "iso") {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: state.timeZone, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(date);
    const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
    return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}`;
  }
  return date.toLocaleString(dateDisplayLocale(), { timeZone: state.timeZone, dateStyle: "medium", timeStyle: "short", hourCycle });
};
export const PLACEHOLDER_RELEASE_DATE = "2200-01-01";
export const releaseDateKey = (value) => String(value || "").trim().slice(0, 10);
export const isDisplayableBook = (book) => {
  const releaseDate = releaseDateKey(book?.release_date);
  return validReleaseDate(book?.release_date) && releaseDate !== PLACEHOLDER_RELEASE_DATE;
};
export const displayableBooks = (books) => (books || []).filter(isDisplayableBook);
export function categoryItemInRange(item) {
  if (state.releaseMode !== "category") return true;
  if (typeof item?.category_in_range === "boolean") return item.category_in_range;
  const releaseDate = releaseDateKey(item?.release_date);
  const start = releaseDateKey(state.categoryResult?.start);
  const end = releaseDateKey(state.categoryResult?.end);
  return !start || !end || (releaseDate >= start && releaseDate <= end);
}

export function categoryItemOutsideRange(item) {
  return state.releaseMode === "category" && !categoryItemInRange(item);
}

export function categoryOutsideClass(item) {
  return categoryItemOutsideRange(item) ? " category-out-of-range" : "";
}
export const releaseCountdown = (value) => {
  const dateValue = String(value || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateValue)) return "";
  const release = new Date(`${dateValue}T12:00:00`);
  const today = todayInTimeZone();
  if (Number.isNaN(release.getTime())) return "";
  const days = Math.round((release.getTime() - today.getTime()) / 86400000);
  if (days < 0) return "";
  if (days === 0) return "Releasing today";
  return `Releasing in ${days} day${days === 1 ? "" : "s"}`;
};
export const showToast = (message) => {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 3200);
};
export const initials = (value) => String(value || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
