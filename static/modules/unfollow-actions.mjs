import { state } from "./state.mjs?v=20260922-2";
import { $, escapeHtml, showToast } from "./format.mjs?v=20260922-2";
import { compactAuthorDisplay, compactInitials, displayableAuthors } from "./identity-display.mjs?v=20260922-2";
import { closeModal, openModal } from "./modal-accessibility.mjs?v=20260922-2";
import { followedAuthor } from "./watch-display.mjs?v=20260922-2";
import { api } from "./api.mjs?v=20260922-2";
import { authorMembers } from "./author-group-actions.mjs?v=20260922-2";

export function createUnfollowActions({ closeDetails, refreshFollowViews }) {
  function authorUnfollowChip(item, kind) {
    const label = compactInitials(item.display_name || item.name || item.title);
    const className = kind === "series" ? "watch-chip-series" : "watch-chip-book";
    return `<span class="watch-chip ${className}">${escapeHtml(label)}</span>`;
  }

  function closeAuthorUnfollowConfirmation() {
    closeModal("#author-unfollow-modal");
    state.pendingAuthorUnfollow = null;
    const message = $("#author-unfollow-message");
    if (message) message.textContent = "";
    const confirm = $("#author-unfollow-confirm");
    if (confirm) {
      confirm.disabled = false;
      confirm.textContent = "Unfollow author";
    }
  }

  async function requestAuthorUnfollow(author) {
    const records = [...new Map(authorMembers(author).map((member) => followedAuthor(member))
      .filter((member) => member?.id).map((member) => [String(member.id), member])).values()];
    const record = records[0] || followedAuthor(author) || author;
    if (!record?.id) {
      showToast("This author follow could not be found.");
      return;
    }
    const displayName = compactAuthorDisplay(record.display_name || record.name || "this author");
    const ids = records.length ? records.map((member) => member.id) : [record.id];
    const pending = { id: record.id, ids, name: displayName };
    state.pendingAuthorUnfollow = pending;
    const modal = $("#author-unfollow-modal");
    $("#author-unfollow-content").innerHTML = '<div class="modal-loading">Checking affected releases…</div>';
    $("#author-unfollow-confirm").disabled = true;
    openModal(modal, { initialFocus: "#author-unfollow-cancel" });
    try {
      const previews = await Promise.all(ids.map((id) => api(`/api/unfollow-preview/author/${id}`)));
      if (state.pendingAuthorUnfollow !== pending) return;
      const unique = (key) => [...new Map(previews.flatMap((preview) => preview[key] || [])
        .map((item) => [`${item.kind || key}:${item.id || item.asin}`, item])).values()];
      const preview = { series: unique("series"), books: unique("books"), shared_impacts: unique("shared_impacts") };
      const series = (preview.series || []).map((item) => authorUnfollowChip(item, "series")).join("");
      const books = (preview.books || []).map((item) => authorUnfollowChip(item, "book")).join("");
      const affected = series || books;
      const shared = (preview.shared_impacts || []).flatMap((item) => {
        const otherNames = displayableAuthors(item.authors || []).filter((candidate) => !ids.some((id) => String(id) === String(candidate.id))).map((candidate) => compactAuthorDisplay(candidate.display_name || candidate.name)).filter(Boolean).join(", ");
        return otherNames ? [`${compactInitials(item.name)} (${otherNames})`] : [];
      });
      $("#author-unfollow-content").innerHTML = `
        <div class="author-unfollow-heading">
          <p class="eyebrow">UNFOLLOW AUTHOR</p>
          <h2 id="author-unfollow-modal-title">Unfollow ${escapeHtml(displayName)}?</h2>
          <p class="details-blurb">${ids.length > 1 ? "This removes follows for all aliases in this group." : "This removes only this author’s follow."} Shared books and series stay followed for other authors.</p>
        </div>
        ${affected ? `<div class="author-unfollow-items">${series ? `<section><h3>Series</h3><div class="watch-chips">${series}</div></section>` : ""}${books ? `<section><h3>Standalone books</h3><div class="watch-chips">${books}</div></section>` : ""}</div>` : `<p class="details-blurb">No followed series or standalone books are currently attached to this author.</p>`}
        ${shared.length ? `<p class="unfollow-shared-warning">Shared follows remain for other authors: ${escapeHtml(shared.join("; "))}.</p>` : ""}`;
      $("#author-unfollow-confirm").disabled = false;
    } catch (error) {
      closeAuthorUnfollowConfirmation();
      showToast(error.message);
    }
  }

  async function confirmAuthorUnfollow() {
    const pending = state.pendingAuthorUnfollow;
    if (!pending) return;
    const button = $("#author-unfollow-confirm");
    const message = $("#author-unfollow-message");
    button.disabled = true;
    button.textContent = "Unfollowing…";
    message.textContent = "";
    try {
      for (const id of pending.ids || [pending.id]) {
        await api(`/api/watch/author/${id}`, { method: "DELETE" });
      }
      closeAuthorUnfollowConfirmation();
      closeDetails();
      void refreshFollowViews(null);
      showToast(`Unfollowed ${pending.name}`);
    } catch (error) {
      button.disabled = false;
      button.textContent = "Unfollow author";
      message.textContent = error.message;
    }
  }

  function closeItemUnfollowConfirmation() {
    closeModal("#item-unfollow-modal");
    state.pendingItemUnfollow = null;
    const message = $("#item-unfollow-message");
    if (message) message.textContent = "";
    const confirm = $("#item-unfollow-confirm");
    if (confirm) {
      confirm.disabled = false;
      confirm.textContent = "Unfollow";
    }
  }

  async function requestItemUnfollow(kind, record, author = null) {
    if (!record?.id || !["series", "book"].includes(kind)) {
      showToast(`This ${kind} follow could not be found.`);
      return;
    }
    const modal = $("#item-unfollow-modal");
    const requestToken = `${kind}:${record.id}:${Date.now()}`;
    state.pendingItemUnfollow = {
      kind,
      id: record.id,
      token: requestToken,
      authorId: author?.id ? Number(author.id) : null,
      authorName: author?.display_name || author?.name || "this author",
      activeDetailItem: state.activeDetailItem ? { ...state.activeDetailItem } : null,
    };
    $("#item-unfollow-content").innerHTML = '<div class="modal-loading">Checking affected follows…</div>';
    $("#item-unfollow-confirm").disabled = true;
    openModal(modal, { initialFocus: "#item-unfollow-cancel" });
    try {
      const preview = await api(`/api/unfollow-preview/${kind}/${record.id}`);
      if (state.pendingItemUnfollow?.token !== requestToken) return;
      const target = preview.target || record;
      const name = compactInitials(target.name || target.title || record.name || record.title || `this ${kind}`);
      const affected = displayableAuthors(preview.affected_authors || []);
      const defaultAuthorId = state.pendingItemUnfollow.authorId;
      const toggles = affected.map((author) => `
        <button class="unfollow-author-toggle" type="button" aria-pressed="${defaultAuthorId && Number(author.id) === defaultAuthorId ? "true" : "false"}" data-unfollow-author-id="${author.id}">
          <span class="unfollow-author-check" aria-hidden="true">✓</span>
          <span>Remove from ${escapeHtml(compactInitials(author.display_name || author.name))}’s follows</span>
        </button>`).join("");
      const scopeText = defaultAuthorId
        ? `This removes the ${kind} from ${state.pendingItemUnfollow.authorName}’s follows. Other authors stay followed unless selected.`
        : kind === "series"
          ? `This removes the direct series follow and its ${preview.book_count || 0} known book${preview.book_count === 1 ? "" : "s"}. Author follows are separate.`
          : "This removes the direct standalone-book follow. Author follows are separate.";
      $("#item-unfollow-content").innerHTML = `
        <div>
          <p class="eyebrow">UNFOLLOW ${escapeHtml(kind.toUpperCase())}</p>
          <h2 id="item-unfollow-modal-title">Unfollow ${escapeHtml(name)}?</h2>
          <p class="details-blurb">${escapeHtml(scopeText)}</p>
        </div>
        ${toggles ? `<section class="unfollow-author-options"><h3>Also remove from author follows</h3><p class="details-blurb">Select only the authors whose relationship should be removed. Other authors stay followed.</p>${toggles}</section>` : ""}`;
      state.pendingItemUnfollow.name = name;
      const confirm = $("#item-unfollow-confirm");
      confirm.disabled = false;
      confirm.textContent = `Unfollow ${kind}`;
    } catch (error) {
      closeItemUnfollowConfirmation();
      showToast(error.message);
    }
  }

  async function confirmItemUnfollow() {
    const pending = state.pendingItemUnfollow;
    if (!pending) return;
    const button = $("#item-unfollow-confirm");
    const message = $("#item-unfollow-message");
    const authorIds = [...document.querySelectorAll("#item-unfollow-modal [data-unfollow-author-id][aria-pressed='true']")]
      .map((toggle) => Number(toggle.dataset.unfollowAuthorId));
    const targetToggle = pending.authorId
      ? document.querySelector(`#item-unfollow-modal [data-unfollow-author-id="${pending.authorId}"]`)
      : null;
    if (pending.authorId && !targetToggle && !authorIds.includes(pending.authorId)) authorIds.push(pending.authorId);
    button.disabled = true;
    button.textContent = "Unfollowing…";
    message.textContent = "";
    try {
      await api("/api/unfollow", {
        method: "POST",
        body: JSON.stringify({ kind: pending.kind, entity_id: pending.id, author_ids: authorIds, remove_direct: !pending.authorId }),
      });
      const active = pending.activeDetailItem;
      closeItemUnfollowConfirmation();
      if (active?.kind === pending.kind) closeDetails();
      else if (active?.kind === "author" || (active?.kind === "book" && pending.kind === "series")) {
        void refreshFollowViews({ kind: active.kind, asin: active.asin });
      } else {
        void refreshFollowViews(null);
      }
      showToast(`Unfollowed ${pending.name}`);
    } catch (error) {
      button.disabled = false;
      button.textContent = `Unfollow ${pending.kind}`;
      message.textContent = error.message;
    }
  }

  async function removeWatch(kind, id) {
    if (kind === "author") {
      const author = (state.watches.authors || []).find((item) => String(item.id) === String(id)) || { id };
      requestAuthorUnfollow(author);
      return;
    }
    const collection = kind === "series" ? state.watches.series : state.watches.books;
    const record = (collection || []).find((item) => String(item.id) === String(id)) || { id };
    await requestItemUnfollow(kind, record);
  }

  return { closeAuthorUnfollowConfirmation, requestAuthorUnfollow, confirmAuthorUnfollow, closeItemUnfollowConfirmation, requestItemUnfollow, confirmItemUnfollow, removeWatch };
}
