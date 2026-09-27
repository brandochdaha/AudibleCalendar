import { state } from "./state.mjs?v=20260922-2";
import { $, displayableBooks, escapeHtml, prettyDate } from "./format.mjs?v=20260922-2";
import { authorIdentityMatches, authorNameKey, canonicalAuthorName, canonicalAuthorRecord, collapseAuthors, compactAuthorDisplay, compactInitials, displayableAuthors, isDisplayableAuthor } from "./identity-display.mjs?v=20260922-2";
import { api } from "./api.mjs?v=20260922-2";
import { openModal } from "./modal-accessibility.mjs?v=20260922-2";
import { authorComparisonsLoading, renderAuthorComparison } from "./author-comparison.mjs?v=20260922-2";
import { manualMergeCheckbox, renderManualMergeControls } from "./manual-author-merge.mjs?v=20260922-2";

function seriesIdentityMatches(left, right) {
  const leftAsin = String(left?.asin || "").trim();
  const rightAsin = String(right?.asin || "").trim();
  if (leftAsin || rightAsin) return Boolean(leftAsin && rightAsin && leftAsin === rightAsin);
  return Boolean(left?.name && right?.name && authorNameKey(left.name) === authorNameKey(right.name));
}

export function followedAuthor(author) {
  return (state.watches.authors || []).find((item) => (
    (author?.id !== undefined && String(item.id) === String(author.id))
    || (author?.asin && item.asin === author.asin)
    || authorIdentityMatches(item, author)
  ));
}

export function followedSeries(series, book = null) {
  const match = (state.watches.series || []).find((item) => (
    seriesIdentityMatches(item, series)
  ));
  if (match && (match.is_tracked || match.is_directly_tracked || match.followed_via_author || match.selected_via_author)) return match;
  // A followed author with no active series filter follows this series even
  // when the series has not yet been materialised in the local series list.
  if (book) {
    const author = collapseAuthors(book.authors || []).find((person) => followedAuthor(person));
    const authorWatch = author && followedAuthor(author);
    if (authorWatch && !authorWatch.series_filter_active) return authorWatch;
  }
  return null;
}

export function followedBook(book) {
  return (state.watches.books || []).find((item) => (
    book?.asin && item.asin === book.asin
  ));
}

export function followButton(label, following, attributes = "", extraClass = "") {
  const stateClass = following ? "button-secondary is-following" : "button-primary";
  const noun = String(label || "").replace(/^(?:un)?follow(?:ing|ed)?(?:\s+|$)/i, "").trim();
  return `<button class="button ${stateClass} follow-button ${extraClass}" type="button" data-follow-noun="${escapeHtml(noun)}" ${attributes}>${escapeHtml(label)}</button>`;
}

export function favouriteButton(kind, record, label = kind, showLabel = false) {
  const favourite = Boolean(record?.is_favourite);
  const title = favourite ? `Remove ${label} from favorites` : `Add ${label} to favorites`;
  const text = showLabel
    ? `${favourite ? "★ Unfavorite" : "☆ Favorite"} ${escapeHtml(label)}`
    : (favourite ? "★" : "☆");
  const labelClass = showLabel ? " favourite-button-labeled" : "";
  return `<button class="button button-quiet favourite-button${labelClass}${favourite ? " is-favourite" : ""}" type="button" data-favourite-kind="${escapeHtml(kind)}" data-favourite-id="${escapeHtml(record?.id)}" data-favourite-label="${escapeHtml(label)}" aria-label="${escapeHtml(title)}" aria-pressed="${favourite ? "true" : "false"}" title="${escapeHtml(title)}">${text}</button>`;
}

export function followedRecord(kind, asin, name = "") {
  if (kind === "author") return followedAuthor({ asin, name });
  if (kind === "series") {
    return (state.watches.series || []).find((item) => (
      seriesIdentityMatches(item, { asin, name })
    ));
  }
  if (kind === "book") return followedBook({ asin });
  return null;
}

function searchResultIsFollowed(item) {
  return Boolean(
    item?.is_followed
    || item?.is_tracked
    || followedRecord(item?.kind, item?.asin, item?.name || item?.title),
  );
}

function searchResultFollowControl(item, index) {
  if (searchResultIsFollowed(item)) {
    return '<span class="button button-secondary follow-button is-following search-result-follow-button" role="status">Followed</span>';
  }
  return followButton(
    "Follow",
    false,
    `data-follow-search-index="${index}"`,
    "search-result-follow-button",
  );
}

export function authorChip(author, detailOrigin = "") {
  if (!isDisplayableAuthor(author)) return "";
  const canonical = canonicalAuthorRecord(author);
  const asin = canonical?.asin || author?.asin;
  if (!asin) return "";
  const name = canonicalAuthorName(author);
  const origin = detailOrigin ? ` data-detail-origin="${escapeHtml(detailOrigin)}"` : "";
  const members = (author?.author_group_members || []).filter((member) => member?.asin && member?.name)
    .map((member) => ({ asin: member.asin, name: member.name }));
  const group = members.length > 1
    ? ` data-watch-details-group="${escapeHtml(JSON.stringify(members))}"`
    : "";
  return `<button class="watch-chip watch-chip-author" type="button" data-watch-details data-watch-details-kind="author" data-watch-details-asin="${escapeHtml(asin)}" data-watch-details-name="${escapeHtml(canonical.name || author?.name || name)}"${group}${origin}>${escapeHtml(name)}</button>`;
}

export function seriesChip(series, detailOrigin = "", showFavourite = false) {
  if (!series?.asin) return "";
  const name = series.display_name || series.name || series.title || "Unknown series";
  const origin = detailOrigin ? ` data-detail-origin="${escapeHtml(detailOrigin)}"` : "";
  const marker = showFavourite && series.is_favourite ? "★ " : "";
  return `<button class="watch-chip watch-chip-series" type="button" data-watch-details data-watch-details-kind="series" data-watch-details-asin="${escapeHtml(series.asin)}" data-watch-details-name="${escapeHtml(series.name || series.title || name)}"${origin}>${marker}${escapeHtml(name)}</button>`;
}

export function bookChip(book) {
  if (!book?.asin) return "";
  const name = book.title || book.name || "Unknown book";
  return `<button class="watch-chip watch-chip-book" type="button" data-watch-details data-watch-details-kind="book" data-watch-details-asin="${escapeHtml(book.asin)}" data-watch-details-name="${escapeHtml(name)}">${escapeHtml(name)}</button>`;
}

export function renderWatchlist() {
  const reviewButton = $("#author-reviews-button");
  if (reviewButton) {
    const count = state.watches.author_review_count || 0;
    reviewButton.hidden = count === 0;
    reviewButton.textContent = `Review author matches (${count})`;
  }
  const favouritesOnly = state.releaseScope === "favourites";
  const chipsAlwaysVisible = Boolean(state.settings?.watchlist_chips_always_visible);
  const scoped = (items) => (items || []).filter((item) => !favouritesOnly || item.is_favourite);
  const groups = {
    authors: ["Authors", scoped(collapseAuthors(displayableAuthors(state.watches.authors))), "author"],
    series: ["Series", scoped(state.watches.series), "series"],
    books: ["Books", scoped(displayableBooks(state.watches.books)), "book"],
  };
  Object.entries(groups).forEach(([tab, [, items]]) => {
    const count = $("#watch-count-" + tab);
    if (count) count.textContent = items.length;
  });
  document.querySelectorAll("[data-watch-tab]").forEach((button) => {
    const active = button.dataset.watchTab === state.watchTab;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
    button.tabIndex = active ? 0 : -1;
  });
  const [label, items, kind] = groups[state.watchTab] || groups.authors;
  $("#watchlist").setAttribute("aria-labelledby", `watch-tab-${state.watchTab}`);
  const cards = items.map((item) => {
    const displayName = kind === "author"
      ? compactAuthorDisplay(item.display_name || item.name)
      : compactInitials(item.display_name || item.name || item.title);
    const name = escapeHtml(displayName);
    const detailsName = escapeHtml(item.name || item.title);
    const titleTooltip = kind === "author" && item.follow_all
      ? ` title="${escapeHtml(item.series_filter_active ? "All series are followed by default; some are excluded" : "All series are followed")}"`
      : "";
    const groupMembers = kind === "author" ? (item.author_group_members || [])
      .filter((member) => member?.asin && member?.name)
      .map((member) => ({ asin: member.asin, name: member.name })) : [];
    const group = groupMembers.length > 1
      ? ` data-watch-details-group="${escapeHtml(JSON.stringify(groupMembers))}"`
      : "";
    const title = `<button class="watch-title-button" type="button" data-watch-details data-watch-details-kind="${kind}" data-watch-details-asin="${escapeHtml(item.asin)}" data-watch-details-name="${detailsName}"${group}${titleTooltip}>${name}</button>`;
    const inheritedBook = kind === "book" && !item.is_individually_followed;
    const remove = kind === "series"
      ? followButton("Unfollow", true, `data-remove-kind="${kind}" data-remove-id="${item.id}"`, "series-unfollow-button")
      : kind === "author"
        ? followButton("Unfollow", true, `data-remove-kind="${kind}" data-remove-id="${item.id}"`, "watch-unfollow-button")
        : inheritedBook
          ? ""
          : followButton("Unfollow", true, `data-remove-kind="${kind}" data-remove-id="${item.id}"`, "watch-unfollow-button");
   let chips = [];
   if (kind === "author") {
      const showSeriesFavourites = !(item.is_favourite && item.follow_all);
      chips = [
        ...(item.selected_series || []).map((series) => seriesChip(series, "", showSeriesFavourites)),
        ...displayableBooks(item.selected_books).map(bookChip),
      ].filter(Boolean);
   } else if (kind === "series") {
      chips = collapseAuthors(displayableAuthors(item.followed_authors || [])).map(authorChip).filter(Boolean);
   } else {
      chips = [
        ...collapseAuthors(displayableAuthors(item.authors || [])).map(authorChip),
        ...(item.series || []).map(seriesChip),
      ].filter(Boolean);
   }
   const chipRow = chips.length ? `<div class="watch-chips">${chips.join("")}</div>` : "";
    const favourite = kind === "author"
      || kind === "series"
      || (kind === "book" && !(item.series || []).length)
      ? favouriteButton(kind, item)
      : "";
    const actionControls = `${favourite}${remove}`;
    const actions = actionControls ? `<div class="watch-card-actions">${actionControls}</div>` : "";
    const cardClass = chipsAlwaysVisible ? "watch-card watch-card-chips-always" : "watch-card";
    return `<article class="${cardClass}"><div class="watch-card-content">${title}${chipRow}</div>${actions}</article>`;
  });
  const emptyLabel = label.toLowerCase();
  const emptyText = favouritesOnly ? `No favorite ${emptyLabel} yet.` : `No followed ${emptyLabel} yet.`;
  $("#watchlist").innerHTML = cards.length ? cards.join("") : `<div class="empty-state">${emptyText}</div>`;
}

export function renderSearchResults() {
  renderManualMergeControls();
  if (!state.searchResults.length) {
    $("#search-results").innerHTML = "";
    return;
  }
  $("#search-results").innerHTML = state.searchResults.map((item, index) => {
    if (item.kind === "author-match") {
      const members = (item.members || []).filter((member) => member?.name);
      const memberRows = members.map((member) => {
        const books = Number(member.book_count) || 0;
        const series = Number(member.series_count) || 0;
        return `<div class="author-match-member"><strong>${escapeHtml(compactInitials(member.name))}</strong><span>${books} book${books === 1 ? "" : "s"} · ${series} series</span></div>`;
      }).join("");
      const sharedSeries = Number(item.shared_series_count) || (item.shared_series || []).length;
      const pending = item.pending_decision;
      const disabled = state.authorSearchDecisionPending || authorComparisonsLoading() ? " disabled aria-busy=\"true\"" : "";
      const mergeLabel = pending === "merge" ? "Merging…" : "Merge authors";
      const separateLabel = pending === "separate" ? "Separating…" : "Separate authors";
      const comparison = `<details class="author-match-comparison" data-author-match-comparison="${index}"${item.comparisonOpen ? " open" : ""}><summary class="author-match-shared">${sharedSeries} shared series <span class="author-comparison-hint">· compare books and series</span></summary><div data-author-comparison-content="${index}">${renderAuthorComparison(item, index)}</div></details>`;
      const warning = item.overrides_separation ? '<p class="manual-merge-warning">You previously kept these authors separate. Confirming this merge will override that decision throughout the app.</p>' : "";
      const alternative = item.manual_merge
        ? `<button class="button button-secondary" type="button" data-manual-merge-cancel${state.authorSearchDecisionPending ? " disabled" : ""}>Cancel</button>`
        : `<button class="button button-secondary" type="button" data-author-match-index="${index}" data-author-match-decision="separate"${disabled}>${separateLabel}</button>`;
      return `<article class="result-card author-match-card"><div class="result-card-copy"><p class="author-match-eyebrow">${item.manual_merge ? "Manual author merge" : "Possible author match"}</p><div class="author-match-members">${memberRows}</div>${warning}${comparison}</div><div class="result-card-actions"><button class="button button-primary" type="button" data-author-match-index="${index}" data-author-match-decision="merge"${disabled}>${mergeLabel}</button>${alternative}</div></article>`;
    }
    const detail = item.kind === "book" ? escapeHtml(prettyDate(item.release_date)) : escapeHtml(item.asin);
    const resultName = item.kind === "author" ? compactInitials(item.name) : (item.name || item.title);
    const groupAliases = item.kind === "author" ? (item.author_group_aliases || []) : [];
    const groupMeta = item.author_group && item.author_group_member_count > 1
      ? `<p class="search-result-group-meta">Also credited as ${escapeHtml(groupAliases.join(", ") || "other catalog identities")} · ${item.author_group_member_count} identities</p>`
      : "";
    const authors = item.kind === "author" ? [] : collapseAuthors(displayableAuthors(item.authors || []));
    const series = item.kind === "series" ? [] : (item.series || []);
    const authorChips = authors.map((author) => authorChip(author, "search")).filter(Boolean);
    const seriesChips = series.map((seriesItem) => seriesChip(seriesItem, "search")).filter(Boolean);
    const relationRows = [
      authorChips.length ? `<div class="watch-chips search-result-chips">${authorChips.join("")}</div>` : "",
      seriesChips.length ? `<div class="watch-chips search-result-chips">${seriesChips.join("")}</div>` : "",
      item.relationships_incomplete ? '<span class="search-result-more">+more</span>' : "",
    ].filter(Boolean).join("");
    const relationships = relationRows ? `<div class="search-result-relations">${relationRows}</div>` : "";
    const follow = searchResultFollowControl(item, index);
    return `<article class="result-card"><div class="result-card-copy">${manualMergeCheckbox(item, index)}<h3>${escapeHtml(resultName)}</h3><p>${detail}</p>${groupMeta}${relationships}</div><div class="result-card-actions">${follow}<button class="button button-secondary" type="button" data-details-index="${index}">Details</button></div></article>`;
  }).join("");
}

function collapseSearchAuthors(items) {
  const rawAuthors = items.filter((item) => item?.kind === "author");
  const authors = collapseAuthors(displayableAuthors(rawAuthors));
  const bySourceAsin = new Map();
  authors.forEach((author) => {
    [author.asin, ...(author.source_asins || []), ...(author.author_group_members || []).map((member) => member.asin)]
      .filter(Boolean)
      .forEach((asin) => bySourceAsin.set(String(asin), author));
  });
  const emitted = new Set();
  return items.map((item) => {
    if (item?.kind === "author-match") return item;
    const author = bySourceAsin.get(String(item?.asin));
    if (!author || emitted.has(author)) return null;
    emitted.add(author);
    return author;
  }).filter(Boolean);
}

export async function doSearch(event) {
  event.preventDefault();
  const query = $("#search-input").value.trim();
  const kind = $("#search-kind").value;
  if (!query) return;
  const context = { query, kind };
  state.searchContext = context;
  state.manualAuthorMerge = null;
  openModal("#search-results-panel", { initialFocus: "#search-results-close" });
  $("#search-message").textContent = "Searching…";
  state.searchResults = [];
  renderSearchResults();
  try {
    await reloadSearchResults(context);
  } catch (error) {
    if (state.searchContext === context && error.name !== "AbortError") $("#search-message").textContent = error.message;
  }
}

export async function reloadSearchResults(context = state.searchContext) {
  if (!context) return;
  const { query, kind } = context;
  const result = await api(`/api/search?q=${encodeURIComponent(query)}&kind=${kind}`);
  if (state.searchContext !== context) return;
  state.manualAuthorMerge = null;
  const rawItems = result.items || [];
  const items = kind === "author" ? collapseSearchAuthors(rawItems) : rawItems;
  state.searchResults = kind === "book" ? displayableBooks(items) : items;
  $("#search-message").textContent = state.searchResults.length ? `${state.searchResults.length} result${state.searchResults.length === 1 ? "" : "s"}` : "No matches found.";
  renderSearchResults();
}

export function activeStandaloneBook(asin) {
  if (state.activeAuthorDetails) {
    for (const group of state.activeAuthorDetails.series || []) {
      if (group.asin) continue;
      const book = (group.books || []).find((item) => item.asin === asin);
      if (book) return book;
    }
    return null;
  }
  const book = state.activeBookDetails?.book;
  const hasSeries = (book?.series || []).some((item) => item?.asin && item?.name);
  return book?.asin === asin && !hasSeries ? book : null;
}

export function standaloneBookFavouriteButton(book, record, showLabel = false) {
  if (record) return favouriteButton("book", record, "book", showLabel);
  const label = "Favorite book (also follow this book)";
  const text = showLabel ? "☆ Favorite book" : "☆";
  const labelClass = showLabel ? " favourite-button-labeled" : "";
  return `<button class="button button-quiet favourite-button${labelClass}" type="button" data-favourite-standalone-book-asin="${escapeHtml(book.asin)}" data-favourite-label="book" aria-label="${escapeHtml(label)}" aria-pressed="false" title="${escapeHtml(label)}">${text}</button>`;
}
