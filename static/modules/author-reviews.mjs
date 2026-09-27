import { api } from "./api.mjs?v=20260922-2";
import { $, escapeHtml, showToast } from "./format.mjs?v=20260922-2";
import { closeModal, openModal } from "./modal-accessibility.mjs?v=20260922-2";

export function renderReviewBooks(books, label) {
  if (!books.length) return `<p class="muted">${escapeHtml(label)}</p>`;
  return `<details class="author-review-evidence"><summary>${escapeHtml(label)}</summary>
    <ul>${books.map(book => `<li>${escapeHtml(book.title)}
      <small class="muted">${book.series.length
        ? book.series.map(series => escapeHtml(series.name)).join(" · ")
        : "No series recorded"}</small></li>`).join("")}</ul></details>`;
}

export function createAuthorReviews({ invalidateAllDetails, loadWatches, loadCalendar }) {
  let reviews = [];
  let busy = false;
  const close = () => closeModal("#author-reviews-modal");
  const sourcesHtml = (sources) => sources.map((source) => (
    `<li>${escapeHtml(source.name)} <small class="muted">${escapeHtml(source.asin)}</small></li>`
  )).join("");

  function render() {
    $("#author-reviews-content").innerHTML = reviews.length ? reviews.map((review) => `
      <article class="author-review-card">
        <h3>${review.kind === "split" ? "Should these authors stay merged?" : "Are these the same author?"}</h3>
        <div class="author-review-sources">
          <div><ul>${sourcesHtml(review.left)}</ul>${renderReviewBooks(review.left_only_book_details, `${review.left_only_books} ${review.left_only_books === 1 ? "book" : "books"} only in this group`)}</div>
          <div><ul>${sourcesHtml(review.right)}</ul>${renderReviewBooks(review.right_only_book_details, `${review.right_only_books} ${review.right_only_books === 1 ? "book" : "books"} only in this group`)}</div>
        </div>
        ${renderReviewBooks(review.shared_book_details, `${review.shared_books} shared ${review.shared_books === 1 ? "book" : "books"}`)}
        <p class="muted">Based on books currently recorded for these author listings.</p>
        <p class="muted">${review.kind === "split"
          ? "Splitting retains each source’s book credits and current follow and favorite settings."
          : "Merging combines follows and favorites while retaining both source identities."}</p>
        <div class="modal-actions">
          <button class="button button-secondary" type="button" data-author-review="${review.id}" data-decision="separate">${review.kind === "split" ? "Split authors" : "Keep separate"}</button>
          <button class="button button-secondary" type="button" data-author-review="${review.id}" data-decision="merge">${review.kind === "split" ? "Keep merged" : "Merge authors"}</button>
        </div>
      </article>`).join("") : '<p>No author matches need review.</p>';
  }

  async function reload() {
    const result = await api("/api/author-reviews");
    reviews = result.items;
    render();
  }

  $("#author-reviews-button").addEventListener("click", async () => {
    $("#author-reviews-message").textContent = "";
    $("#author-reviews-content").textContent = "Loading author matches…";
    openModal("#author-reviews-modal");
    try { await reload(); }
    catch (error) { if (error.name !== "AbortError") $("#author-reviews-message").textContent = error.message; }
  });
  $("#author-reviews-close").addEventListener("click", close);
  $("#author-reviews-modal").addEventListener("click", (event) => {
    if (event.target.id === "author-reviews-modal") close();
  });
  $("#author-reviews-content").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-author-review]");
    if (!button || busy) return;
    busy = true;
    $("#author-reviews-message").textContent = "Saving decision…";
    $("#author-reviews-content").querySelectorAll("button").forEach((item) => { item.disabled = true; });
    try {
      await api("/api/author-reviews", {
        method: "POST",
        body: JSON.stringify({ review_id: button.dataset.authorReview, decision: button.dataset.decision }),
      });
      invalidateAllDetails();
      await Promise.all([loadWatches(), loadCalendar(), reload()]);
      $("#author-reviews-close").focus();
      $("#author-reviews-message").textContent = "Decision saved. Future syncs will respect it.";
      showToast("Author decision saved");
    } catch (error) {
      if (error.name !== "AbortError") $("#author-reviews-message").textContent = error.message;
    } finally {
      busy = false;
      $("#author-reviews-content").querySelectorAll("button").forEach((item) => { item.disabled = false; });
    }
  });
  return { closeAuthorReviews: close };
}
