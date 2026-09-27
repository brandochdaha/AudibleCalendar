import { state } from "./state.mjs?v=20260922-2";
import { $, escapeHtml, showToast } from "./format.mjs?v=20260922-2";
import { compactAuthorDisplay, compactInitials, displayableAuthors } from "./identity-display.mjs?v=20260922-2";
import { closeModal, openModal } from "./modal-accessibility.mjs?v=20260922-2";
import { followedAuthor } from "./watch-display.mjs?v=20260922-2";

export function createBookAuthorPrompt() {
  let pendingBookAuthorPrompt = null;
  function bookAuthorCandidates(item) {
    const scoped = new Set((item.author_scope_asins || []).map((asin) => String(asin)));
    const seen = new Set();
    return displayableAuthors(item.authors || []).filter((author) => {
      const asin = String(author?.asin || "");
      if (!asin || scoped.has(asin) || seen.has(asin) || followedAuthor(author)) return false;
      seen.add(asin);
      return true;
    });
  }

  function closeBookAuthorPrompt(selected = null) {
    const pending = pendingBookAuthorPrompt;
    if (!pending) return;
    pendingBookAuthorPrompt = null;
    closeModal("#follow-authors-modal");
    pending.resolve(selected);
  }

  function requestBookAuthorPrompt(item) {
    const candidates = bookAuthorCandidates(item);
    const modal = $("#follow-authors-modal");
    if (!modal || !candidates.length) return Promise.resolve([]);
    const content = $("#follow-authors-content");
    const options = candidates.map((author) => {
      const label = compactAuthorDisplay(author.display_name || author.name || "Unknown author");
      return `<label class="follow-author-option"><input type="checkbox" value="${escapeHtml(author.asin)}" data-follow-author-choice /> <span>${escapeHtml(label)}</span></label>`;
    }).join("");
    content.innerHTML = `
      <p class="eyebrow">FOLLOW BOOK</p>
      <h2 id="follow-authors-modal-title">Follow other credited authors?</h2>
      <p class="details-blurb">The book itself will be followed. Select any authors whose future releases you also want to follow.</p>
      <div class="follow-author-options">${options}</div>`;
    return new Promise((resolve) => {
      pendingBookAuthorPrompt = { resolve };
      openModal(modal, { initialFocus: "#follow-authors-cancel" });
    });
  }

  function confirmBookAuthorPrompt() {
    if (!pendingBookAuthorPrompt) return;
    const selected = [...document.querySelectorAll("#follow-authors-modal [data-follow-author-choice]:checked")]
      .map((input) => input.value);
    closeBookAuthorPrompt(selected);
  }

  return { closeBookAuthorPrompt, requestBookAuthorPrompt, confirmBookAuthorPrompt };
}
