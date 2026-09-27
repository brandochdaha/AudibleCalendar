import { state } from "./state.mjs?v=20260922-2";
import { $, displayableBooks, escapeHtml, imageSrc, initials, prettyDate, releaseCountdown, safeAudibleUrl } from "./format.mjs?v=20260922-2";
import { canonicalAuthorName, canonicalAuthorRecord, collapseAuthors, displayableAuthors } from "./identity-display.mjs?v=20260922-2";
import { followedAuthor, followedSeries, followedBook, followButton, favouriteButton, followedRecord, authorChip, seriesChip, standaloneBookFavouriteButton } from "./watch-display.mjs?v=20260922-2";

export function detailRefreshButton({ pending = false, error = false } = {}) {
  const label = pending
    ? "Refreshing details from Audible"
    : error
      ? "Retry refresh from Audible"
      : "Refresh details from Audible";
  const classes = `icon-button details-refresh-button${pending ? " is-pending" : ""}${error ? " is-error" : ""}`;
  const stateAttributes = pending ? ' disabled aria-busy="true"' : "";
  return `<button id="details-refresh" class="${classes}" type="button" data-details-refresh aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}"${stateAttributes}><span class="details-refresh-icon" aria-hidden="true">↻</span></button>`;
}

function modalBookCover(book, className = "author-book-cover") {
  const image = book?.image_url
    ? `<img src="${escapeHtml(imageSrc(book.image_url))}" alt="" />`
    : `<span class="author-book-cover-fallback">${escapeHtml(initials(book?.title))}</span>`;
  return `<div class="${className}">${image}</div>`;
}

export function renderAuthorDetails(details) {
  state.activeAuthorDetails = details;
  const author = details.author || {};
  const groupMembers = details.author_group_members || author.author_group_members || [];
  const identityMembers = author.source_identities?.length > 1 ? author.source_identities : groupMembers;
  const groupAliases = details.author_group_aliases || author.author_group_aliases || [];
  const authorWatch = [author, ...groupMembers].map(followedAuthor).find(Boolean);
  const authorFollowing = groupMembers.length > 1
    ? groupMembers.every((member) => Boolean(followedAuthor(member)))
    : Boolean(authorWatch || details.author_followed);
  const authorDisplayName = canonicalAuthorName(author);
  const metadata = (details.metadata || []).map((item) => `<div><dt>${escapeHtml(item.label)}</dt><dd>${escapeHtml(item.value)}</dd></div>`).join("");
  const groups = (details.series || []).map((group, groupIndex) => {
    const visibleBooks = displayableBooks(group.books);
    if (!visibleBooks.length) return "";
    const books = visibleBooks.map((book) => {
      // The server computes selection for this specific author. Do not infer
      // it from a global book row, which may belong to another author.
      if (!group.asin && typeof book.is_selected !== "boolean") book.is_selected = Boolean(followedBook(book));
      const sequence = book.series_sequence ? `Book ${escapeHtml(book.series_sequence)}` : "";
      const subtitle = book.subtitle ? escapeHtml(book.subtitle) : "";
      const secondary = [sequence, subtitle].filter(Boolean).join(" · ");
      const title = `<button class="detail-link-button author-book-title" type="button" data-open-book data-book-asin="${escapeHtml(book.asin)}" data-book-name="${escapeHtml(book.title)}">${escapeHtml(book.title)}</button>`;
      const standaloneFavourite = !group.asin
        ? standaloneBookFavouriteButton(book, followedBook(book))
        : "";
      const standaloneControl = !group.asin
        ? followButton(
          book.is_selected ? "Unfollow book" : "Follow book",
          Boolean(book.is_selected),
          `data-author-book-asin="${escapeHtml(book.asin)}" data-author-book-action="${book.is_selected ? "deselect" : "select"}"`,
          "detail-follow-series",
        )
        : "";
      return `<li class="author-book">${modalBookCover(book)}<div>${title}${secondary ? `<p>${secondary}</p>` : ""}</div><div class="author-book-end"><div class="author-book-date">${escapeHtml(prettyDate(book.release_date))}</div>${standaloneFavourite}${standaloneControl}</div></li>`;
    }).join("");
    const groupWatch = group.asin
      ? followedRecord("series", group.asin, group.name)
      : null;
    const groupFollowing = typeof group.is_selected === "boolean"
      ? group.is_selected
      : Boolean(groupWatch || followedSeries(group, visibleBooks[0]));
    group.is_selected = groupFollowing;
    const seriesFavourite = groupWatch ? favouriteButton("series", groupWatch) : "";
    const seriesControl = group.asin
      ? followButton(
        group.is_selected ? "Unfollow series" : "Follow series",
        Boolean(group.is_selected),
        `data-author-series-index="${groupIndex}" data-author-series-action="${group.is_selected ? "deselect" : "select"}"`,
        "detail-follow-series",
      )
      : "";
    const seriesActions = seriesFavourite || seriesControl
      ? `<div class="series-heading-actions">${seriesFavourite}${seriesControl}</div>`
      : "";
    const seriesHeading = group.asin
      ? `<button class="detail-link-button series-heading-title" type="button" data-open-series data-series-asin="${escapeHtml(group.asin)}" data-series-name="${escapeHtml(group.name)}" data-series-seed-asin="${escapeHtml(visibleBooks[0].asin)}">${escapeHtml(group.name)}</button>`
      : escapeHtml(group.name);
    return `<article class="series-group"><div class="series-heading"><div><h4>${seriesHeading}</h4><span>${visibleBooks.length} book${visibleBooks.length === 1 ? "" : "s"}</span></div>${seriesActions}</div><ul class="author-book-list">${books}</ul></article>`;
  }).join("");
  const warning = details.error ? `<p class="details-blurb">Some catalog pages could not be loaded: ${escapeHtml(details.error)}</p>` : "";
  const image = author.image_url ? `<img src="${escapeHtml(imageSrc(author.image_url))}" alt="" />` : escapeHtml(initials(authorDisplayName));
  const blurb = author.description ? `<p class="details-blurb">${escapeHtml(author.description)}</p>` : "";
  const visibleBookCount = (details.series || []).reduce((count, group) => count + displayableBooks(group.books).length, 0);
  const books = visibleBookCount ? `${visibleBookCount} book${visibleBookCount === 1 ? "" : "s"}` : "No books found";
  const visibleGroups = (details.series || []).filter((group) => displayableBooks(group.books).length);
  const releaseSelections = new Map();
  visibleGroups.forEach((group) => {
    const groupSelected = Boolean(group.asin && group.is_selected);
    displayableBooks(group.books).forEach((book) => {
      const selected = group.asin ? groupSelected : Boolean(book.is_selected);
      releaseSelections.set(book.asin, Boolean(releaseSelections.get(book.asin)) || selected);
    });
  });
  const allReleasesFollowed = Boolean(
    authorFollowing
    && releaseSelections.size > 0
    && [...releaseSelections.values()].every(Boolean),
  );
  const allAction = allReleasesFollowed ? "none" : "all";
  const allLabel = allReleasesFollowed ? "Unfollow all author releases" : "Follow all author releases";
  const allAttributes = `data-author-series-all data-author-series-all-action="${allAction}" aria-pressed="${allReleasesFollowed}"`;
  const followAll = `<div class="author-series-all-control"><div>${detailRefreshButton({ error: Boolean(details.error) })}</div>${authorFollowing ? followButton(allLabel, allReleasesFollowed, allAttributes, "detail-follow-series") : ""}</div>`;
  const authorFollowLabel = authorFollowing ? "Unfollow author" : "Follow author";
  const authorFollowAttributes = `data-follow-detail data-follow-kind="author" data-following="${authorFollowing ? "true" : "false"}" data-follow-asin="${escapeHtml(author.asin)}" data-follow-name="${escapeHtml(author.name)}" data-follow-author-group="${groupMembers.length > 1 ? "true" : "false"}"`;
  const authorFavourite = authorFollowing ? favouriteButton("author", authorWatch, "author", true) : "";
  const identityNote = identityMembers.length > 1
    ? `<p class="author-identity-note">Also credited as ${escapeHtml(groupAliases.join(", ") || "other catalog identities")} · ${identityMembers.length} catalog identities are included.</p>`
    : "";
  const identityListings = identityMembers.length > 1
    ? `<section class="details-section author-identity-listings"><h3>Catalog listings</h3><div class="series-authors">${identityMembers.map((member) => `<div class="series-author-row"><button class="detail-link-button" type="button" data-open-author data-author-asin="${escapeHtml(member.asin)}" data-author-name="${escapeHtml(member.name)}">${escapeHtml(member.name)}</button><span class="details-asin">${escapeHtml(member.asin)}</span></div>`).join("")}</div></section>`
    : "";
  const identityManagementLink = identityMembers.length > 1
    ? `<a class="detail-link-button author-identity-management-link" href="#settings-panel-authors" data-open-author-management>Manage author merges</a>`
    : "";
  $("#details-modal-content").innerHTML = `
    <div class="details-hero">
      <div class="author-avatar">${image}</div>
      <div><p class="eyebrow">AUTHOR</p><h2 id="details-modal-title">${escapeHtml(authorDisplayName)}</h2><p class="details-asin">${escapeHtml(author.asin)}</p></div>
    </div>
    ${identityNote}
    ${identityListings}
    ${identityManagementLink}
    ${blurb}
    ${metadata ? `<section class="details-section"><h3>More about this author</h3><dl class="detail-metadata">${metadata}</dl></section>` : ""}
    <section class="details-section"><h3>Books <span class="muted">· ${books}</span></h3><div class="series-groups">${groups || '<div class="empty-state">No books were found for this author in the public catalog.</div>'}${followAll}</div></section>
    ${warning}
    <div class="modal-actions author-modal-actions">${authorFavourite}${followButton(authorFollowLabel, authorFollowing, authorFollowAttributes)}</div>`;
}

export function renderSeriesDetails(details) {
  state.activeSeriesDetails = details;
  const series = details.series || {};
  const visibleBooks = displayableBooks(details.books);
  const firstBook = visibleBooks[0] || {};
  const seriesCover = firstBook.image_url
    ? `<img src="${escapeHtml(imageSrc(firstBook.image_url))}" alt="" />`
    : escapeHtml(initials(series.name));
  const seriesAuthors = collapseAuthors(displayableAuthors(details.authors || []));
  const showAuthorFollow = state.activeDetailItem?.detailOrigin === "search";
  const authors = seriesAuthors.map((author) => {
    const displayName = canonicalAuthorName(author);
    const authorMembers = author.author_group_members || [];
    const authorFollowing = authorMembers.length > 1
      ? authorMembers.every((member) => Boolean(followedAuthor(member)))
      : Boolean(followedAuthor(author));
    const followLabel = authorFollowing ? "Unfollow author" : "Follow author";
    const followAttributes = `data-follow-author data-following="${authorFollowing ? "true" : "false"}" data-author-asin="${escapeHtml(author.asin)}" data-author-name="${escapeHtml(author.name)}"`;
    const followControl = showAuthorFollow
      ? followButton(followLabel, authorFollowing, followAttributes, "detail-follow-author")
      : "";
    return `<div class="series-author-row"><button class="detail-link-button" type="button" data-open-author data-author-asin="${escapeHtml(author.asin)}" data-author-name="${escapeHtml(author.name)}">${escapeHtml(displayName)}</button>${followControl}</div>`;
  }).join("");
  const books = visibleBooks.map((book) => {
   const sequence = book.series_sequence ? `Book ${escapeHtml(book.series_sequence)}` : "";
   const title = book.audible_url
      ? `<button class="detail-link-button" type="button" data-open-book data-book-asin="${escapeHtml(book.asin)}" data-book-name="${escapeHtml(book.title)}">${escapeHtml(book.title)}</button>`
     : `<span>${escapeHtml(book.title)}</span>`;
    return `<li class="author-book">${modalBookCover(book)}<div>${title}${sequence ? `<p>${sequence}</p>` : ""}</div><div class="author-book-date">${escapeHtml(prettyDate(book.release_date))}</div></li>`;
  }).join("");
  const warning = details.error ? `<p class="details-blurb">Some catalog pages could not be loaded: ${escapeHtml(details.error)}</p>` : "";
  const seriesWatch = followedRecord("series", series.asin, series.name);
  const seriesFollowing = Boolean(seriesWatch || followedSeries(series, firstBook));
  const followLabel = seriesFollowing ? "Unfollow series" : "Follow series";
  const followAttributes = `data-follow-detail data-follow-kind="series" data-following="${seriesFollowing ? "true" : "false"}" data-follow-asin="${escapeHtml(series.asin)}" data-follow-name="${escapeHtml(series.name)}"`;
  const seriesFavourite = seriesWatch ? favouriteButton("series", seriesWatch, "series", true) : "";
  $("#details-modal-content").innerHTML = `
    <div class="details-hero">
      <div class="author-avatar series-cover">${seriesCover}</div>
      <div><p class="eyebrow">SERIES</p><h2 id="details-modal-title">${escapeHtml(series.name)}</h2><p class="details-asin">${escapeHtml(series.asin)}</p></div>
    </div>
    ${authors ? `<section class="details-section"><h3>Author${seriesAuthors.length === 1 ? "" : "s"}</h3><div class="series-authors">${authors}</div></section>` : ""}
    <section class="details-section"><h3>Books <span class="muted">· ${visibleBooks.length}</span></h3><div class="series-groups">${books ? `<article class="series-group"><ul class="author-book-list">${books}</ul></article>` : '<div class="empty-state">No English books were found for this series in the public catalog.</div>'}</div></section>
    ${warning}
    <div class="modal-actions series-modal-actions">${detailRefreshButton({ error: Boolean(details.error) })}${seriesFavourite}${followButton(followLabel, seriesFollowing, followAttributes)}</div>`;
}

export function renderBookDetails(details) {
  state.activeBookDetails = details;
  const book = details.book || {};
  const showSearchFollowShortcuts = state.activeDetailItem?.detailOrigin === "search";
  const image = book.image_url
    ? `<img src="${escapeHtml(imageSrc(book.image_url))}" alt="" />`
    : `<span class="book-details-cover-fallback">${escapeHtml(initials(book.title))}</span>`;
  const bookAuthors = collapseAuthors(displayableAuthors(book.authors || []));
  const validSeries = (book.series || []).filter((item) => item?.asin && item?.name);
  const authorRows = bookAuthors.map((author) => {
    const chip = authorChip(author);
    if (!chip) return "";
    if (!showSearchFollowShortcuts || followedAuthor(author)) {
      return `<div class="details-chip-row">${chip}</div>`;
    }
    const canonical = canonicalAuthorRecord(author);
    const followAttributes = `data-follow-author data-following="false" data-author-asin="${escapeHtml(canonical.asin || author.asin)}" data-author-name="${escapeHtml(canonical.name || author.name)}"`;
    return `<div class="details-chip-row">${chip}${followButton("Follow author", false, followAttributes, "detail-follow-author")}</div>`;
  }).filter(Boolean).join("");
  const seriesRows = validSeries.map((series) => {
    const chip = seriesChip(series);
    if (!chip) return "";
    const following = Boolean(followedSeries(series, book));
    if (!showSearchFollowShortcuts || following) {
      return `<div class="details-chip-row">${chip}</div>`;
    }
    const followAttributes = `data-follow-detail data-follow-kind="series" data-following="false" data-follow-asin="${escapeHtml(series.asin)}" data-follow-name="${escapeHtml(series.name)}"`;
    return `<div class="details-chip-row">${chip}${followButton("Follow series", false, followAttributes, "detail-follow-series")}</div>`;
  }).join("");
  const relationshipChips = `${authorRows}${seriesRows}`;
  const followControls = validSeries.length
    ? validSeries.map((series) => {
      const following = Boolean(followedSeries(series, book));
      if (showSearchFollowShortcuts && !following) return "";
      const seriesWatch = followedRecord("series", series.asin, series.name);
      const noun = validSeries.length > 1 ? series.name : "series";
      const label = `${following ? "Unfollow" : "Follow"} ${noun}`;
      const attributes = `data-follow-detail data-follow-kind="series" data-following="${following ? "true" : "false"}" data-follow-asin="${escapeHtml(series.asin)}" data-follow-name="${escapeHtml(series.name)}"`;
      const favourite = seriesWatch ? favouriteButton("series", seriesWatch, "series", true) : "";
      return `${favourite}${followButton(label, following, attributes)}`;
    }).join("")
    : (() => {
      const following = Boolean(followedBook(book));
      const attributes = `data-follow-detail data-follow-kind="book" data-following="${following ? "true" : "false"}" data-follow-asin="${escapeHtml(book.asin)}" data-follow-name="${escapeHtml(book.title)}"`;
      const bookWatch = followedBook(book);
      const favourite = standaloneBookFavouriteButton(book, bookWatch, true);
      return `${favourite}${followButton(`${following ? "Unfollow" : "Follow"} book`, following, attributes)}`;
    })();
  const countdown = releaseCountdown(book.release_date);
  const seriesPosition = (book.series || [])
    .map((item) => {
      const sequence = item.sequence ?? item.sequence_number;
      if (sequence === undefined || sequence === null || sequence === "") return "";
      const position = Number.isFinite(Number(sequence)) && Number(sequence) % 1 === 0 ? String(Number(sequence)) : String(sequence);
      return (book.series || []).length > 1 ? `${item.name}: Book ${position}` : `Book ${position}`;
    })
    .filter(Boolean)
    .join(", ");
  const metadataItems = [
    ...(book.asin ? [{ label: "ASIN", value: book.asin }] : []),
    ...(details.metadata || []),
    ...(seriesPosition ? [{ label: "Series position", value: seriesPosition }] : []),
    { label: "Release date", value: prettyDate(book.release_date) },
  ];
  const metadata = metadataItems.map((item) => `<div><dt>${escapeHtml(item.label)}</dt><dd>${escapeHtml(item.value)}</dd></div>`).join("");
  const warning = details.error ? `<p class="details-blurb">The latest catalog details could not be loaded: ${escapeHtml(details.error)}</p>` : "";
  const followActionMarkup = `<div class="modal-actions book-modal-actions">${detailRefreshButton({ error: Boolean(details.error) })}${followControls || ""}</div>`;
  const audibleUrl = safeAudibleUrl(book.audible_url);
  const audibleLink = audibleUrl
    ? `<div class="detail-metadata-audible"><dt>Audible</dt><dd><a class="audible-icon-link" href="${escapeHtml(audibleUrl)}" target="_blank" rel="noreferrer" aria-label="Open on Audible" title="Open on Audible"><img src="/assets/external-link.svg" alt="" /></a></dd></div>`
    : "";
  $("#details-modal-content").innerHTML = `
    <div class="details-hero book-details-hero">
      <div class="book-details-cover" title="Shift-click to refresh cover art">${image}</div>
      <div class="book-details-heading"><h2 id="details-modal-title">${escapeHtml(book.title)}</h2>${book.subtitle ? `<p class="details-subtitle">${escapeHtml(book.subtitle)}</p>` : ""}${relationshipChips ? `<div class="details-relationships">${relationshipChips}</div>` : ""}${countdown ? `<p class="details-release-countdown">${escapeHtml(countdown)}</p>` : ""}</div>
    </div>
    <p id="book-description" class="details-blurb book-description is-collapsed" data-description-expand aria-expanded="false">${escapeHtml(book.description || "No description was provided by the available sources.")}</p>
    <section class="details-section"><h3>Book details</h3><dl class="detail-metadata">${metadata}${audibleLink}</dl></section>
    ${warning}
    ${followActionMarkup}`;
  fitBookDetailsModal();
  prepareBookDescription();
}

export function refreshBookCover() {
  const book = state.activeBookDetails?.book;
  const image = $("#details-modal-content .book-details-cover img");
  if (!book?.image_url || !image) return;
  state.imageRefreshVersions.set(book.image_url, Date.now());
  image.src = imageSrc(book.image_url, true);
}

export function fitBookDetailsModal() {
  const detailsModal = $("#details-modal");
  const modal = detailsModal?.querySelector(".modal");
  const metadata = modal?.querySelector(".detail-metadata");
  if (!modal || !metadata || state.activeDetailItem?.kind !== "book") return;
  if (window.matchMedia("(max-width: 900px)").matches) {
    modal.style.removeProperty("width");
    return;
  }

  // Measure the metadata row at its intrinsic width, rather than the
  // viewport-sized fallback used while the modal is loading.
  const previousWidth = metadata.style.width;
  metadata.style.width = "max-content";
  const contentWidth = metadata.getBoundingClientRect().width;
  metadata.style.width = previousWidth;

  const styles = getComputedStyle(modal);
  const horizontalChrome =
    parseFloat(styles.paddingLeft || "0") +
    parseFloat(styles.paddingRight || "0") +
    parseFloat(styles.borderLeftWidth || "0") +
    parseFloat(styles.borderRightWidth || "0");
  const viewportWidth = Math.max(320, window.innerWidth - 48);
  const desiredWidth = Math.ceil(contentWidth + horizontalChrome);
  modal.style.width = String(Math.min(viewportWidth, desiredWidth)) + "px";
}

function prepareBookDescription() {
  const description = $("#book-description");
  if (!description) return;
  description.classList.remove("is-collapsed");
  const fullHeight = description.scrollHeight;
  description.classList.add("is-collapsed");
  const expandable = fullHeight > description.clientHeight + 1;
  description.classList.toggle("is-expandable", expandable);
  if (expandable) {
    description.tabIndex = 0;
    description.setAttribute("role", "button");
    description.setAttribute("aria-label", "Expand book description");
    description.title = "Click to expand";
  } else {
    description.removeAttribute("tabindex");
    description.removeAttribute("role");
    description.removeAttribute("aria-label");
    description.removeAttribute("title");
  }
}

export function toggleBookDescription(description) {
  if (!description?.classList.contains("is-expandable")) return;
  const expanded = description.classList.toggle("is-expanded");
  description.classList.toggle("is-collapsed", !expanded);
  description.setAttribute("aria-expanded", expanded ? "true" : "false");
  description.setAttribute("aria-label", expanded ? "Collapse book description" : "Expand book description");
  description.title = expanded ? "Click to collapse" : "Click to expand";
}
