// Author identity normalization used consistently throughout the UI.
import { state } from "./state.mjs?v=20260922-2";

// Source ownership is established by the server. Name formatting below is
// only for display and must not override a user's identity decisions.
export function authorNameKey(value) {
  return String(value || "")
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim()
    .replace(/\s+/g, " ");
};

export function authorNameFingerprint(value) {
  const tokens = authorNameKey(value).split(" ").filter(Boolean);
  if (!tokens.length) return "";
  // Collapse an exact repeated phrase, but do not collapse arbitrary
  // repeated words in a legitimate name (for example, "John John Smith").
  for (let width = Math.floor(tokens.length / 2); width >= 1; width -= 1) {
    if (tokens.length % width) continue;
    const pattern = tokens.slice(0, width);
    const repeated = tokens.every((token, index) => token === pattern[index % width]);
    if (repeated && tokens.length / width > 1) return pattern.join("");
  }
  return tokens.join("");
}

export function compactInitials(value) {
  return String(value || "").trim().replace(/\b([A-Za-z])\.\s+(?=[A-Za-z]\.)/g, "$1.");
}

function namesDifferOnlyByMiddleInitials(left, right) {
  const leftTokens = authorNameKey(left).split(" ").filter(Boolean);
  const rightTokens = authorNameKey(right).split(" ").filter(Boolean);
  if (leftTokens.length < 2 || rightTokens.length < 2 || leftTokens.length === rightTokens.length) return false;

  const longer = leftTokens.length > rightTokens.length ? leftTokens : rightTokens;
  const shorter = leftTokens.length > rightTokens.length ? rightTokens : leftTokens;
  if (longer[0] !== shorter[0] || longer.at(-1) !== shorter.at(-1)) return false;

  return [...longer.slice(1, -1), ...shorter.slice(1, -1)].every((token) => token.length === 1);
}

export const HIDDEN_AUTHOR_ROLE_SUFFIX = /\s*-\s*(?:translator|editor|artist|contributor)\s*$/i;
export const isHiddenAuthorRoleName = (value) => HIDDEN_AUTHOR_ROLE_SUFFIX.test(String(value || "").trim());
export const isDisplayableAuthor = (author) => {
  if (author?.name && isHiddenAuthorRoleName(author.name)) return false;
  const names = [
    author?.name,
    ...(author?.aliases || []),
    ...(author?.display_name || "").split("/").map((name) => name.trim()),
  ].filter(Boolean);
  return names.some((name) => !isHiddenAuthorRoleName(name));
};
export const displayableAuthors = (authors) => (authors || []).filter(isDisplayableAuthor);

export function compactAuthorDisplay(value) {
  const names = String(value || "")
    .split("/")
    .map((name) => name.trim())
    .filter((name) => name && !isHiddenAuthorRoleName(name))
    .map((name) => compactInitials(name));
  const seen = new Set();
  const unique = names.filter((name) => {
    const key = authorNameKey(name);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (unique.length < 2) return unique.join(" / ");
  const visible = [unique[0]];
  unique.slice(1).forEach((name) => {
    const isDuplicate = visible.some((candidate) => (
      authorNameFingerprint(candidate) === authorNameFingerprint(name)
      || namesDifferOnlyByMiddleInitials(candidate, name)
    ));
    if (!isDuplicate) visible.push(name);
  });
  return visible.join(" / ");
}

function authorGroupMembers(author) {
  return author?.author_group_members || author?.author?.author_group_members || [];
}

function authorSourceAsins(author) {
  return new Set([
    author?.asin,
    ...(author?.source_asins || []),
    ...authorGroupMembers(author).flatMap((member) => [member?.asin, ...(member?.source_asins || [])]),
  ].filter(Boolean));
}

function authorSeparationSources(author) {
  return new Set([
    ...(author?.separate_source_asins || []),
    ...authorGroupMembers(author).flatMap((member) => member?.separate_source_asins || []),
  ].filter(Boolean));
}

function authorsSeparate(left, right) {
  const leftSources = authorSourceAsins(left);
  const rightSources = authorSourceAsins(right);
  const leftBlocked = authorSeparationSources(left);
  const rightBlocked = authorSeparationSources(right);
  return [...leftSources].some((asin) => rightBlocked.has(asin))
    || [...rightSources].some((asin) => leftBlocked.has(asin));
}

export function canonicalAuthorRecord(author) {
  if (!author) return {};
  const watched = state.watches.authors || [];
  const canonicalId = author.canonical_id ?? author.id;
  const exact = watched.find((item) => (
    !authorsSeparate(item, author) && (
      (canonicalId != null && String(item.id) === String(canonicalId))
      || (author.asin && (item.asin === author.asin || (item.source_asins || []).includes(author.asin)))
    )
  ));
  return exact || author;
}

export function canonicalAuthorName(author) {
  const canonical = canonicalAuthorRecord(author);
  return compactAuthorDisplay(
    canonical.display_name || author?.display_name || canonical.name || author?.name || "Unknown author"
  );
}

export function authorIdentityMatches(left, right) {
  if (authorsSeparate(left, right)) return false;
  const leftSources = authorSourceAsins(left);
  if ([...authorSourceAsins(right)].some((asin) => leftSources.has(asin))) return true;
  const leftId = left?.canonical_id ?? left?.id;
  const rightId = right?.canonical_id ?? right?.id;
  if (leftId != null && rightId != null) return String(leftId) === String(rightId);
  return false;
}

export function collapseAuthors(authors) {
  const collapsed = [];
  const records = (authors || []).filter(Boolean).map((author) => {
    const canonical = canonicalAuthorRecord(author);
    const merged = { ...canonical, ...author };
    // Keep catalogue follow-state fields from the source row, but use the
    // canonical identity and name for display and modal navigation.
    merged.id = canonical.id ?? author.id;
    merged.canonical_id = canonical.canonical_id ?? canonical.id ?? author.canonical_id;
    merged.asin = canonical.asin || author.asin;
    merged.name = canonical.name || author.name;
    if (canonical.is_tracked) merged.is_followed = true;
    return merged;
  });
  records.forEach((merged) => {
    const existing = collapsed.find((item) => authorIdentityMatches(item, merged));
    if (!existing) {
      collapsed.push(merged);
      return;
    }
    // Prefer the stable Audible row if a name-derived row arrived first.
    if (String(existing.asin || "").startsWith("name:") && !String(merged.asin || "").startsWith("name:")) {
      const followState = { is_followed: existing.is_followed, is_following_series: existing.is_following_series };
      Object.assign(existing, merged, followState);
    }
    // Preserve a positive follow state if either source row carried it.
    existing.is_followed = Boolean(existing.is_followed || merged.is_followed);
    existing.is_following_series = Boolean(existing.is_following_series || merged.is_following_series);
  });
  return collapsed;
}

export function formatAuthorNames(authors) {
  return collapseAuthors(displayableAuthors(authors)).map(canonicalAuthorName).filter(Boolean).join(", ") || "Unknown author";
}
