// Event delegation for detail navigation and follow actions.
import { state } from "./state.mjs?v=20260922-2";
import { showToast } from "./format.mjs?v=20260922-2";
import { followedAuthor, followedRecord } from "./watch-display.mjs?v=20260922-2";
import { renderAuthorDetails, refreshBookCover, toggleBookDescription } from "./detail-render.mjs?v=20260922-2";

export function createDetailClickHandler({ closeAllModals, refreshDetails, favouriteStandaloneBook, toggleFavourite, openWatchDetailsFromElement, openDetailsForItem, requestAuthorUnfollow, addAuthorGroupWatch, closeDetails, addWatch, updateAuthorSeries, updateAuthorBook, requestItemUnfollow }) {
  return (event) => {
    if (event.target.id === "details-modal") {
      closeAllModals();
      return;
    }
    const refresh = event.target.closest("[data-details-refresh]");
    if (refresh) {
      refreshDetails();
      return;
    }
    const standaloneFavourite = event.target.closest("[data-favourite-standalone-book-asin]");
    if (standaloneFavourite) {
      favouriteStandaloneBook(standaloneFavourite.dataset.favouriteStandaloneBookAsin, standaloneFavourite);
      return;
    }
    const favourite = event.target.closest("[data-favourite-id]");
    if (favourite) {
      toggleFavourite(
        favourite.dataset.favouriteKind,
        favourite.dataset.favouriteId,
        favourite.getAttribute("aria-pressed") === "true",
        favourite,
      );
      return;
    }
    const cover = event.target.closest(".book-details-cover");
    if (cover && event.shiftKey) {
      refreshBookCover();
      return;
    }
    const chip = event.target.closest("[data-watch-details]");
    if (openWatchDetailsFromElement(chip)) return;
    const seriesLink = event.target.closest("[data-open-series]");
    if (seriesLink) {
      openDetailsForItem({ kind: "series", asin: seriesLink.dataset.seriesAsin, name: seriesLink.dataset.seriesName, seed_asin: seriesLink.dataset.seriesSeedAsin });
      return;
    }
    const authorLink = event.target.closest("[data-open-author]");
    if (authorLink) {
      const seriesAuthor = (state.activeSeriesDetails?.authors || []).find((author) => author?.asin === authorLink.dataset.authorAsin);
      openDetailsForItem({
        kind: "author",
        asin: authorLink.dataset.authorAsin,
        name: authorLink.dataset.authorName,
        ...(seriesAuthor?.author_group_members?.length ? { author_group_members: seriesAuthor.author_group_members } : {}),
      });
      return;
    }
    const bookLink = event.target.closest("[data-open-book]");
    if (bookLink) {
      openDetailsForItem({ kind: "book", asin: bookLink.dataset.bookAsin, name: bookLink.dataset.bookName });
      return;
    }
    const description = event.target.closest("[data-description-expand]");
    if (description) {
      toggleBookDescription(description);
      return;
    }
    const followAuthor = event.target.closest("[data-follow-author]");
    if (followAuthor) {
      const following = followAuthor.dataset.following === "true";
      const seriesAuthor = (state.activeSeriesDetails?.authors || []).find((author) => author?.asin === followAuthor.dataset.authorAsin);
      const authorTarget = {
        asin: followAuthor.dataset.authorAsin,
        name: followAuthor.dataset.authorName,
        ...(seriesAuthor?.author_group_members?.length ? { author_group_members: seriesAuthor.author_group_members } : {}),
      };
      if (following) {
        const author = followedAuthor(authorTarget);
        if (author?.id) requestAuthorUnfollow(author);
      } else if (authorTarget.author_group_members?.length > 1) {
        addAuthorGroupWatch({ ...authorTarget, kind: "author" }, followAuthor)
          .then((succeeded) => { if (succeeded) closeDetails(); });
      } else {
        addWatch({ kind: "author", ...authorTarget }, followAuthor)
          .then((succeeded) => { if (succeeded) closeDetails(); });
      }
      return;
    }
    const authorSeries = event.target.closest("[data-author-series-index]");
    if (authorSeries) {
      const groupIndex = Number(authorSeries.dataset.authorSeriesIndex);
      const action = authorSeries.dataset.authorSeriesAction;
      updateAuthorSeries(action, groupIndex, authorSeries);
      return;
    }
    const authorBook = event.target.closest("[data-author-book-asin]");
    if (authorBook) {
      const asin = authorBook.dataset.authorBookAsin;
      const book = (state.activeAuthorDetails?.series || [])
        .flatMap((group) => group.books || [])
        .find((item) => item.asin === asin);
      if (!book) return;
      if (authorBook.dataset.authorBookAction === "deselect") {
        updateAuthorBook("deselect", book, authorBook);
      } else {
        const members = state.activeAuthorDetails?.author_group_members;
        const authorScope = state.activeAuthorDetails?.author?.asin
          ? { author_scope_asins: members?.length ? members.map((member) => member.asin) : [state.activeAuthorDetails.author.asin] }
          : {};
        addWatch({ kind: "book", ...book, ...authorScope }, authorBook).then(async (succeeded) => {
          if (succeeded && state.activeAuthorDetails) renderAuthorDetails(state.activeAuthorDetails);
        });
      }
      return;
    }
    const allAuthorSeries = event.target.closest("[data-author-series-all]");
    if (allAuthorSeries) {
      updateAuthorSeries(allAuthorSeries.dataset.authorSeriesAllAction || "all", null, allAuthorSeries);
      return;
    }
    const follow = event.target.closest("[data-follow-detail]");
    if (follow && state.activeDetailItem) {
      const followKind = follow.dataset.followKind || state.activeDetailItem.kind;
      const following = follow.dataset.following === "true";
      if (following) {
        if (followKind === "author") {
          requestAuthorUnfollow({
            asin: follow.dataset.followAsin,
            name: follow.dataset.followName,
            author_group_members: state.activeAuthorDetails?.author_group_members,
          });
          return;
        }
        const record = followedRecord(followKind, follow.dataset.followAsin, follow.dataset.followName);
        if (record?.id) {
          requestItemUnfollow(followKind, record);
          return;
        }
        showToast(`This ${followKind} follow could not be found.`);
        return;
      }
      const groupMembers = state.activeDetailItem.author_group_members
        || state.activeAuthorDetails?.author_group_members
        || state.activeAuthorDetails?.author?.author_group_members
        || [];
      if (followKind === "author" && groupMembers.length > 1) {
        const group = { ...state.activeDetailItem, author_group_members: groupMembers };
        addAuthorGroupWatch(group, follow).then((succeeded) => { if (succeeded) closeDetails(); });
        return;
      }
      let item = state.activeDetailItem;
      if (item.kind === "series" && state.activeSeriesDetails) {
        item = {
          ...item,
          name: state.activeSeriesDetails.series?.name || item.name,
          authors: state.activeSeriesDetails.authors || [],
          books: state.activeSeriesDetails.books || [],
        };
      }
      if (item.kind === "book" && followKind === "series") {
        const book = state.activeBookDetails?.book;
        const series = (book?.series || []).find((entry) => entry?.asin === follow.dataset.followAsin)
          || (book?.series || []).find((entry) => entry?.asin && entry?.name);
        if (book && series) {
          item = {
            kind: "series",
            asin: series.asin,
            name: series.name,
            seed_asin: book.asin,
            authors: book.authors || [],
            books: [book],
          };
        }
      }
      addWatch(item, follow).then((succeeded) => { if (succeeded) closeDetails(); });
    }
  };
}
