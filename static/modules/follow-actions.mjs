import { createUnfollowActions } from "./unfollow-actions.mjs?v=20260922-2";
import { createBookAuthorPrompt } from "./book-author-prompt.mjs?v=20260922-2";
import { state } from "./state.mjs?v=20260922-2";
import { $, escapeHtml, showToast } from "./format.mjs?v=20260922-2";
import { compactAuthorDisplay, compactInitials, displayableAuthors } from "./identity-display.mjs?v=20260922-2";
import { api } from "./api.mjs?v=20260922-2";
import { authorMembers, updateAuthorReleases } from "./author-group-actions.mjs?v=20260922-2";
import { closeModal, openModal } from "./modal-accessibility.mjs?v=20260922-2";
import { followedAuthor, followedBook, activeStandaloneBook } from "./watch-display.mjs?v=20260922-2";
import { renderAuthorDetails, renderSeriesDetails, renderBookDetails } from "./detail-render.mjs?v=20260922-2";

export function createFollowActions({ loadWatches, loadCalendar, closeDetails }) {
  const pendingFavouriteKeys = new Set();
  const pendingFollowKeys = new Set();
  let viewRefreshQueue = Promise.resolve();
  let viewRefreshVersion = 0;
  const { closeBookAuthorPrompt, requestBookAuthorPrompt, confirmBookAuthorPrompt } = createBookAuthorPrompt();
  const { closeAuthorUnfollowConfirmation, requestAuthorUnfollow, confirmAuthorUnfollow,
    closeItemUnfollowConfirmation, requestItemUnfollow, confirmItemUnfollow, removeWatch } = createUnfollowActions({ closeDetails, refreshFollowViews });

  function actionKey(kind, id) {
    return `${kind}:${id}`;
  }

  function activeDetailSnapshot() {
    const item = state.activeDetailItem;
    return item ? { kind: item.kind, asin: item.asin } : null;
  }

  function isActiveDetail(snapshot) {
    const active = state.activeDetailItem;
    return Boolean(snapshot && active && snapshot.kind === active.kind && snapshot.asin === active.asin);
  }

  function renderActiveDetails(snapshot) {
    if (!isActiveDetail(snapshot)) return;
    if (state.activeAuthorDetails) renderAuthorDetails(state.activeAuthorDetails);
    else if (state.activeSeriesDetails) renderSeriesDetails(state.activeSeriesDetails);
    else if (state.activeBookDetails) renderBookDetails(state.activeBookDetails);
  }

  function refreshFollowViews(snapshot) {
    state.detailCache.clear();
    state.detailCacheEpoch += 1;
    const version = ++viewRefreshVersion;
    viewRefreshQueue = viewRefreshQueue
      .catch(() => {})
      .then(async () => {
        await Promise.all([loadWatches(), loadCalendar()]);
        if (version === viewRefreshVersion && isActiveDetail(snapshot) && snapshot.kind === "author") {
          const params = new URLSearchParams({ asin: snapshot.asin, name: state.activeAuthorDetails?.author?.name || "" });
          const groupMembers = state.activeDetailItem?.author_group_members
            || state.activeAuthorDetails?.author_group_members
            || state.activeAuthorDetails?.author?.author_group_members
            || [];
          for (const member of groupMembers) {
            if (!member?.asin || !member?.name) continue;
            params.append("member_asin", member.asin);
            params.append("member_name", member.name);
          }
          const details = await api(`/api/author-details?${params}`);
          if (version === viewRefreshVersion && isActiveDetail(snapshot)) state.activeAuthorDetails = details;
        }
        if (version === viewRefreshVersion) renderActiveDetails(snapshot);
      })
      .catch((error) => {
        if (version === viewRefreshVersion) showToast(`Saved, but the other views could not be refreshed: ${error.message}`);
      });
    return viewRefreshQueue;
  }

  function setFavouriteButtonState(button, favourite, pending = false) {
    if (!button) return;
    const label = button.dataset.favouriteLabel || button.dataset.favouriteKind || "item";
    const title = favourite ? `Remove ${label} from favorites` : `Add ${label} to favorites`;
    const labelled = button.classList.contains("favourite-button-labeled");
    button.classList.toggle("is-favourite", favourite);
    button.classList.toggle("is-pending", pending);
    button.setAttribute("aria-pressed", String(favourite));
    button.setAttribute("aria-busy", String(pending));
    button.setAttribute("aria-label", title);
    button.title = pending ? "Saving…" : title;
    button.textContent = labelled
      ? `${favourite ? "★ Unfavorite" : "☆ Favorite"} ${label}`
      : (favourite ? "★" : "☆");
    button.disabled = pending;
  }

  function setFollowButtonState(button, following, pending = false) {
    if (!button) return;
    const noun = button.dataset.followNoun ?? button.textContent.trim().replace(/^(?:following|unfollowing|un)?follow(?:ing)?…?\s*/i, "");
    button.classList.toggle("button-primary", !following);
    button.classList.toggle("button-secondary", following);
    button.classList.toggle("is-following", following);
    button.classList.toggle("is-pending", pending);
    button.dataset.following = String(following);
    button.setAttribute("aria-busy", String(pending));
    const verb = pending
      ? `${following ? "Following" : "Unfollowing"}`
      : `${following ? "Unfollow" : "Follow"}`;
    button.textContent = noun ? `${verb} ${noun}${pending ? "…" : ""}` : `${verb}${pending ? "…" : ""}`;
    button.disabled = pending;
  }

  function updateWatchRecord(kind, id, favourite) {
    const collectionName = { author: "authors", series: "series", book: "books" }[kind];
    for (const record of state.watches[collectionName] || []) {
      if (String(record.id) === String(id)) record.is_favourite = favourite;
    }
  }

  function addOptimisticWatch(item, result) {
    const collectionName = { author: "authors", series: "series", book: "books" }[item.kind];
    const collection = state.watches[collectionName];
    if (!collection || !result?.id) return;
    const directlyTracked = !item.author_scope_asins?.length;
    const existing = collection.find((record) => String(record.id) === String(result.id)
      || (item.asin && record.asin === item.asin));
    if (existing) {
      Object.assign(existing, item, result, {
        is_tracked: directlyTracked,
        ...(item.kind === "book" ? { is_individually_followed: directlyTracked } : {}),
      });
      return;
    }
    collection.push({
      ...item,
      ...result,
      is_tracked: directlyTracked,
      is_favourite: false,
      ...(item.kind === "book" ? { is_individually_followed: directlyTracked } : {}),
      follow_all: item.kind === "author" ? true : undefined,
      series_filter_active: false,
    });
  }

  function markAuthorBookSelected(item) {
    if (item.kind !== "book" || !item.author_scope_asins?.length) return;
    for (const group of state.activeAuthorDetails?.series || []) {
      for (const book of group.books || []) {
        if (book.asin === item.asin) book.is_selected = true;
      }
    }
  }

  async function addWatch(item, button = null) {
    if (!item) return;
    const key = actionKey(item.kind, item.asin || item.name || item.title);
    if (pendingFollowKeys.has(key)) return false;
    const snapshot = activeDetailSnapshot();
    pendingFollowKeys.add(key);
    setFollowButtonState(button, true, true);
    try {
      const followAuthorAsins = item.kind === "book"
        ? await requestBookAuthorPrompt(item)
        : null;
      if (item.kind === "book" && followAuthorAsins === null) {
        setFollowButtonState(button, false, false);
        return false;
      }
      const requestItem = followAuthorAsins === null
        ? item
        : { ...item, follow_author_asins: followAuthorAsins };
      const result = await api("/api/watch", { method: "POST", body: JSON.stringify(requestItem) });
      addOptimisticWatch(item, result);
      markAuthorBookSelected(item);
      setFollowButtonState(button, true, false);
      void refreshFollowViews(snapshot);
      showToast(`Following ${item.name || item.title}`);
      return true;
    } catch (error) {
      setFollowButtonState(button, false, false);
      showToast(error.message);
      return false;
    } finally {
      pendingFollowKeys.delete(key);
    }
  }

  async function addAuthorGroupWatch(group, button = null) {
    const members = (group?.author_group_members || []).filter((member) => (
      member?.asin && member?.name
    ));
    if (!members.length) return addWatch(group, button);
    const key = actionKey("author-group", members.map((member) => member.asin).sort().join("|"));
    if (pendingFollowKeys.has(key)) return false;
    const snapshot = activeDetailSnapshot();
    pendingFollowKeys.add(key);
    setFollowButtonState(button, true, true);
    try {
      const result = await api("/api/watch/author-group", {
        method: "POST",
        body: JSON.stringify({ members }),
      });
      const outcomes = result.members || [];
      const optimisticIds = new Set();
      members.forEach((member, index) => {
        const outcome = outcomes.find((candidate) => (
          candidate.requested_asin === member.asin || candidate.asin === member.asin
        )) || outcomes[index];
        if (!outcome) return;
        const outcomeId = outcome.id == null ? "" : String(outcome.id);
        if (outcomeId && optimisticIds.has(outcomeId)) return;
        if (outcomeId) optimisticIds.add(outcomeId);
        addOptimisticWatch({ ...member, kind: "author" }, outcome);
      });
      setFollowButtonState(button, true, false);
      void refreshFollowViews(snapshot);
      showToast(`Following ${group.name || "author"} (${members.length} identities)`);
      return true;
    } catch (error) {
      setFollowButtonState(button, false, false);
      showToast(error.message);
      return false;
    } finally {
      pendingFollowKeys.delete(key);
    }
  }

  async function toggleFavourite(kind, id, currentlyFavourite, button = null) {
    const key = actionKey(kind, id);
    if (pendingFavouriteKeys.has(key)) return false;
    const nextFavourite = !currentlyFavourite;
    const snapshot = activeDetailSnapshot();
    pendingFavouriteKeys.add(key);
    updateWatchRecord(kind, id, nextFavourite);
    setFavouriteButtonState(button, nextFavourite, true);
    try {
      await api("/api/favourite", {
        method: "PUT",
        body: JSON.stringify({ kind, entity_id: Number(id), is_favourite: nextFavourite }),
      });
      setFavouriteButtonState(button, nextFavourite, false);
      void refreshFollowViews(snapshot);
      return true;
    } catch (error) {
      updateWatchRecord(kind, id, currentlyFavourite);
      setFavouriteButtonState(button, currentlyFavourite, false);
      showToast(error.message);
      return false;
    } finally {
      pendingFavouriteKeys.delete(key);
    }
  }

  async function favouriteStandaloneBook(asin, button = null) {
    const book = activeStandaloneBook(asin);
    if (!book) return false;
    const existing = followedBook(book);
    if (existing?.id) {
      return toggleFavourite("book", existing.id, Boolean(existing.is_favourite), button);
    }
    const key = actionKey("book", asin);
    if (pendingFavouriteKeys.has(key)) return false;
    const snapshot = activeDetailSnapshot();
    pendingFavouriteKeys.add(key);
    setFavouriteButtonState(button, true, true);
    try {
      const item = {
        kind: "book",
        ...book,
        ...(state.activeAuthorDetails?.author?.asin
          ? { author_scope_asins: authorMembers(state.activeAuthorDetails).map((member) => member.asin) }
          : {}),
      };
      const followAuthorAsins = await requestBookAuthorPrompt(item);
      if (followAuthorAsins === null) {
        setFavouriteButtonState(button, false, false);
        return false;
      }
      const result = await api("/api/watch", {
        method: "POST",
        body: JSON.stringify({ ...item, follow_author_asins: followAuthorAsins }),
      });
      const followedId = result?.id;
      if (!followedId) throw new Error("This standalone book could not be followed.");
      addOptimisticWatch(item, result);
      markAuthorBookSelected(item);
      await api("/api/favourite", {
        method: "PUT",
        body: JSON.stringify({ kind: "book", entity_id: Number(followedId), is_favourite: true }),
      });
      updateWatchRecord("book", followedId, true);
      setFavouriteButtonState(button, true, false);
      void refreshFollowViews(snapshot);
      showToast(`Favorited ${book.title}`);
      return true;
    } catch (error) {
      setFavouriteButtonState(button, false, false);
      showToast(error.message);
      return false;
    } finally {
      pendingFavouriteKeys.delete(key);
    }
  }

  async function updateAuthorSeries(action, groupIndex = null, button = null) {
    const details = state.activeAuthorDetails;
    if (!details?.author) return false;
    const group = groupIndex === null ? null : (details.series || [])[groupIndex];
    if (!["select", "deselect", "all", "none"].includes(action)) return false;
    if (!["all", "none"].includes(action) && (!group?.asin || !group.name)) return false;
    const key = actionKey("author-series", `${details.author.asin}:${action}:${group?.asin || "all"}`);
    if (pendingFollowKeys.has(key)) return false;
    const snapshot = activeDetailSnapshot();
    pendingFollowKeys.add(key);
    const pendingFollowing = action === "select" || action === "all";
    const wasFollowing = action === "deselect" || action === "none";
    setFollowButtonState(button, pendingFollowing, true);
    try {
      await updateAuthorReleases(api, details, {
          action,
          series_asin: group?.asin,
          series_name: group?.name,
          books: group?.books || [],
      });
      if (action === "all") {
        details.series_filter_active = false;
        details.author_follow_all = true;
        (details.series || []).forEach((item) => {
          item.is_selected = Boolean(item.asin);
          (item.books || []).forEach((book) => { book.is_selected = true; });
        });
      } else if (action === "none") {
        details.series_filter_active = true;
        details.author_follow_all = false;
        (details.series || []).forEach((item) => {
          item.is_selected = false;
          (item.books || []).forEach((book) => { book.is_selected = false; });
        });
      } else {
        group.is_selected = action === "select";
        details.series_filter_active = true;
      }
      renderAuthorDetails(details);
      void refreshFollowViews(snapshot);
      const message = action === "all"
        ? "Following all author releases"
        : action === "none"
          ? "Unfollowed all author releases"
          : `${action === "deselect" ? "Unfollowed" : "Following"} ${group.name}`;
      showToast(message);
      return true;
    } catch (error) {
      setFollowButtonState(button, wasFollowing, false);
      showToast(error.message);
      return false;
    } finally {
      pendingFollowKeys.delete(key);
    }
  }

  async function updateAuthorBook(action, book, button = null) {
    const details = state.activeAuthorDetails;
    if (!details?.author || !book?.asin || !["select", "deselect"].includes(action)) return false;
    const key = actionKey("author-book", `${details.author.asin}:${book.asin}:${action}`);
    if (pendingFollowKeys.has(key)) return false;
    const snapshot = activeDetailSnapshot();
    pendingFollowKeys.add(key);
    const pendingFollowing = action === "select";
    const wasFollowing = action === "deselect";
    setFollowButtonState(button, pendingFollowing, true);
    try {
      await updateAuthorReleases(api, details, {
          action,
          release_kind: "book",
          book_asin: book.asin,
          book,
      });
      book.is_selected = pendingFollowing;
      details.series_filter_active = true;
      renderAuthorDetails(details);
      void refreshFollowViews(snapshot);
      showToast(`${action === "deselect" ? "Unfollowed" : "Following"} ${book.title}`);
      return true;
    } catch (error) {
      setFollowButtonState(button, wasFollowing, false);
      showToast(error.message);
      return false;
    } finally {
      pendingFollowKeys.delete(key);
    }
  }

  return {
    addWatch,
    addAuthorGroupWatch,
    toggleFavourite,
    favouriteStandaloneBook,
    closeBookAuthorPrompt,
    confirmBookAuthorPrompt,
    closeAuthorUnfollowConfirmation,
    requestAuthorUnfollow,
    confirmAuthorUnfollow,
    closeItemUnfollowConfirmation,
    requestItemUnfollow,
    confirmItemUnfollow,
    updateAuthorSeries,
    updateAuthorBook,
    removeWatch,
  };
}
