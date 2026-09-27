// Catalog-wide author merge management.
import { api } from "./api.mjs?v=20260922-2";
import { $, escapeHtml, showToast } from "./format.mjs?v=20260922-2";
import { closeModal, openModal } from "./modal-accessibility.mjs?v=20260922-2";


export function createAuthorManagementController({ invalidateAllDetails, loadWatches, loadCalendar }) {
  const sectionTypes = ["merged", "candidates"];
  const management = {
    merged: { items: null, loaded: false, loading: false, generation: 0 },
    candidates: { items: null, loaded: false, loading: false, generation: 0 },
  };
  let busy = false;
  let pendingSplit = null;
  let rendered = false;

  const content = $("#author-management-content");
  const message = $("#author-management-message");
  const refreshButton = $("#author-management-refresh");
  const splitModal = $("#author-split-modal");

  function countLabel(value, singular, plural = `${singular}s`) {
    const count = Number(value) || 0;
    return `${count} ${count === 1 ? singular : plural}`;
  }

  function memberStats(member) {
    return `${countLabel(member.book_count, "book")} · ${countLabel(member.series_count, "series", "series")}`;
  }

  function renderMergedGroup(group, groupIndex) {
    const canonical = group.canonical || {};
    const members = group.members || [];
    return `
      <article class="author-management-card author-management-merged-card">
        <div class="author-management-card-heading">
          <div>
            <p class="author-management-eyebrow">Merged author</p>
            <h4>${escapeHtml(canonical.name || "Unnamed author")}</h4>
            <p>${countLabel(group.book_count, "book")} · ${countLabel(group.series_count, "series", "series")} across ${members.length} identities</p>
          </div>
          <span class="author-management-badge">${members.length} identities</span>
        </div>
        <p class="author-management-explanation">These source identities currently act as one author throughout the app. Select any non-primary identities to separate them.</p>
        <div class="author-management-members">
          ${members.map((member) => member.is_primary
            ? `<div class="author-management-member is-primary">
                <span class="author-management-primary-label">Primary</span>
                <span class="author-management-member-copy"><strong>${escapeHtml(member.name || "Unnamed identity")}</strong><small>${escapeHtml(member.asin || "")} · ${memberStats(member)}</small></span>
              </div>`
            : `<label class="author-management-member">
                <input type="checkbox" data-author-split-group="${groupIndex}" data-author-split-asin="${escapeHtml(member.asin || "")}" />
                <span class="author-management-member-copy"><strong>${escapeHtml(member.name || "Unnamed identity")}</strong><small>${escapeHtml(member.asin || "")} · ${memberStats(member)}</small></span>
              </label>`).join("")}
        </div>
        <div class="author-management-actions">
          <button class="button button-secondary" type="button" data-author-management-split="${groupIndex}" disabled>Split selected identities</button>
        </div>
      </article>`;
  }

  function renderCandidate(candidate, candidateIndex) {
    const members = candidate.members || [];
    const sharedSeries = candidate.shared_series || [];
    const alreadySeparate = candidate.decision === "separate";
    return `
      <article class="author-management-card author-management-candidate-card">
        <div class="author-management-card-heading">
          <div>
            <p class="author-management-eyebrow">Possible author match</p>
            <h4>${escapeHtml(members.map((member) => member.name).filter(Boolean).join(" / ") || "Unnamed authors")}</h4>
          </div>
          ${alreadySeparate ? '<span class="author-management-badge is-separate">Kept separate</span>' : ""}
        </div>
        <div class="author-management-candidate-members">
          ${members.map((member) => `
            <div class="author-management-candidate-member">
              <strong>${escapeHtml(member.name || "Unnamed identity")}</strong>
              <span>${memberStats(member)}</span>
            </div>`).join("")}
        </div>
        <p class="author-management-shared">${countLabel(candidate.shared_series_count, "shared series", "shared series")}${sharedSeries.length ? `: ${sharedSeries.map((series) => escapeHtml(series.name || "Unnamed series")).join(", ")}` : ""}</p>
        <p class="author-management-explanation">They remain separate until you choose how this possible match should behave throughout the app.</p>
        <div class="author-management-actions">
          <button class="button button-primary" type="button" data-author-management-candidate="${candidateIndex}" data-author-management-decision="merge">Merge authors</button>
          <button class="button button-secondary" type="button" data-author-management-candidate="${candidateIndex}" data-author-management-decision="separate">Keep separate</button>
        </div>
      </article>`;
  }

  function sectionElement(type) {
    return content?.querySelector(`[data-author-management-section="${type}"]`);
  }

  function sectionBody(type) {
    return content?.querySelector(`[data-author-management-body="${type}"]`);
  }

  function sectionMarkup(type, title, description, placeholder) {
    const style = type === "merged" ? "merged" : "candidate";
    return `
      <details class="author-management-section is-${style}" data-author-management-section="${type}">
        <summary class="author-management-section-heading">
          <span class="author-management-section-title">
            <strong>${title}</strong>
            <span>${description}</span>
          </span>
          <span class="author-management-count" data-author-management-count="${type}" hidden></span>
        </summary>
        <div class="author-management-section-body" data-author-management-body="${type}">
          <p class="muted">${placeholder}</p>
        </div>
      </details>`;
  }

  function updateSectionCount(type) {
    const count = content?.querySelector(`[data-author-management-count="${type}"]`);
    if (!count) return;
    const section = management[type];
    count.hidden = !section.loaded;
    count.textContent = section.loaded ? String(section.items.length) : "";
  }

  function updateRefreshButton() {
    if (!refreshButton) return;
    const hasExpandedSection = sectionTypes.some((type) => sectionElement(type)?.open);
    const isLoading = sectionTypes.some((type) => management[type].loading);
    refreshButton.disabled = busy || isLoading || !hasExpandedSection;
  }

  function renderSection(type) {
    const section = management[type];
    const body = sectionBody(type);
    if (!body || !section.loaded) return;
    if (type === "merged") {
      body.innerHTML = section.items.length
        ? `<div class="author-management-list">${section.items.map(renderMergedGroup).join("")}</div>`
        : '<div class="author-management-empty"><p>No merged authors.</p></div>';
    } else {
      body.innerHTML = section.items.length
        ? `<div class="author-management-list">${section.items.map(renderCandidate).join("")}</div>`
        : '<div class="author-management-empty"><p>No separate authors with possible matches.</p></div>';
    }
    updateSplitButtons();
  }

  function renderShell() {
    if (!content || rendered) return;
    content.innerHTML = [
      sectionMarkup(
        "merged",
        "Merged authors",
        "Identities currently combined across follows, books, series and author details.",
        "Expand to load merged authors.",
      ),
      sectionMarkup(
        "candidates",
        "Separate authors with possible matches",
        "Identities that share a series or were previously reviewed. Decisions affect future searches and syncs. This list can be large and may take a while to load.",
        "Expand to load possible matches.",
      ),
    ].join("");
    sectionTypes.forEach((type) => {
      sectionElement(type)?.addEventListener("toggle", () => {
        updateRefreshButton();
        if (sectionElement(type)?.open) loadSection(type, true);
      });
      updateSectionCount(type);
    });
    rendered = true;
    updateRefreshButton();
  }

  function updateSplitButtons() {
    content.querySelectorAll("[data-author-management-split]").forEach((button) => {
      const groupIndex = button.dataset.authorManagementSplit;
      const selected = content.querySelectorAll(`[data-author-split-group="${groupIndex}"]:checked`);
      button.disabled = busy || selected.length === 0;
    });
  }

  function setContentBusy(value) {
    content.querySelectorAll("button, input").forEach((element) => { element.disabled = value; });
    if (!value) updateSplitButtons();
    updateRefreshButton();
  }

  async function loadSection(type, force = false) {
    const section = management[type];
    const body = sectionBody(type);
    if (!section || !body || section.loading || (section.loaded && !force)) return;
    const generation = ++section.generation;
    section.loading = true;
    section.loaded = false;
    body.textContent = type === "merged" ? "Loading merged authors…" : "Loading possible matches…";
    updateSectionCount(type);
    updateRefreshButton();
    try {
      const path = type === "merged" ? "/api/author-management/merged" : "/api/author-management/candidates";
      const result = await api(path);
      if (generation !== section.generation) return;
      section.items = Array.isArray(result.items) ? result.items : [];
      section.loaded = true;
      renderSection(type);
    } catch (error) {
      if (generation === section.generation && error.name !== "AbortError") {
        body.innerHTML = `
          <p class="settings-message">${type === "merged" ? "Merged authors" : "Possible matches"} could not be loaded: ${escapeHtml(error.message)}</p>
          <button class="button button-secondary" type="button" data-author-management-retry="${type}">Retry</button>`;
      }
    } finally {
      if (generation === section.generation) {
        section.loading = false;
        updateSectionCount(type);
        updateRefreshButton();
        setContentBusy(busy);
      }
    }
  }

  function invalidateSection(type) {
    const section = management[type];
    section.generation += 1;
    section.items = null;
    section.loaded = false;
    section.loading = false;
    const body = sectionBody(type);
    if (body) body.textContent = type === "merged" ? "Expand to load merged authors." : "Expand to load possible matches.";
    updateSectionCount(type);
    updateRefreshButton();
  }

  async function refreshExpandedSections() {
    const expanded = sectionTypes.filter((type) => sectionElement(type)?.open);
    await Promise.all(expanded.map((type) => loadSection(type, true)));
  }

  async function refreshAfterChange() {
    const expanded = sectionTypes.filter((type) => sectionElement(type)?.open);
    sectionTypes.forEach(invalidateSection);
    await Promise.all(expanded.map((type) => loadSection(type)));
  }

  async function loadAuthorManagement({ refreshCandidates = false } = {}) {
    renderShell();
    if (!refreshCandidates) {
      await refreshAfterChange();
      return;
    }
    invalidateSection("candidates");
    if (sectionElement("candidates")?.open) await loadSection("candidates");
  }

  function openSplitConfirmation(groupIndex) {
    const group = management.merged.items?.[groupIndex];
    if (!group || busy) return;
    const selectedAsins = [...content.querySelectorAll(`[data-author-split-group="${groupIndex}"]:checked`)]
      .map((input) => input.dataset.authorSplitAsin)
      .filter(Boolean);
    if (!selectedAsins.length) {
      message.textContent = "Select at least one merged identity to split.";
      return;
    }
    const selected = (group.members || []).filter((member) => selectedAsins.includes(member.asin));
    pendingSplit = {
      primary_asin: group.primary_asin,
      split_asins: selectedAsins,
    };
    $("#author-split-content").innerHTML = `
      <p class="eyebrow">AUTHOR MERGE MANAGEMENT</p>
      <h2 id="author-split-title">Split these identities?</h2>
      <p class="muted">The selected identities will become a separate author everywhere. Their book credits and follow settings will be retained.</p>
      <ul class="author-split-list">
        ${selected.map((member) => `<li><strong>${escapeHtml(member.name || "Unnamed identity")}</strong><small>${escapeHtml(member.asin || "")}</small></li>`).join("")}
      </ul>`;
    $("#author-split-message").textContent = "";
    openModal("#author-split-modal", { initialFocus: "#author-split-cancel" });
  }

  function closeAuthorSplitConfirmation() {
    pendingSplit = null;
    closeModal("#author-split-modal");
  }

  function setSplitModalBusy(value) {
    $("#author-split-cancel").disabled = value;
    $("#author-split-confirm").disabled = value;
    $("#author-split-confirm").textContent = value ? "Splitting…" : "Split selected identities";
  }

  async function confirmSplit() {
    if (!pendingSplit || busy) return;
    busy = true;
    setContentBusy(true);
    setSplitModalBusy(true);
    $("#author-split-message").textContent = "Saving split…";
    let saved = false;
    try {
      await api("/api/author-management/split", {
        method: "POST",
        body: JSON.stringify(pendingSplit),
      });
      saved = true;
      closeAuthorSplitConfirmation();
      invalidateAllDetails();
      message.textContent = "";
      await Promise.all([loadWatches(), loadCalendar(), refreshAfterChange()]);
      showToast("Authors split");
    } catch (error) {
      if (error.name !== "AbortError") {
        const target = saved || !pendingSplit ? message : $("#author-split-message");
        target.textContent = saved ? `Split saved, but views could not be refreshed: ${error.message}` : error.message;
      }
    } finally {
      busy = false;
      setSplitModalBusy(false);
      setContentBusy(false);
    }
  }

  async function decideCandidate(candidateIndex, decision) {
    const candidate = management.candidates.items?.[candidateIndex];
    if (!candidate || busy) return;
    busy = true;
    setContentBusy(true);
    message.textContent = "Saving author decision…";
    try {
      await api("/api/author-management-decision", {
        method: "POST",
        body: JSON.stringify({
          decision,
          primary_asin: candidate.primary_asin,
          members: (candidate.members || []).map((member) => ({ asin: member.asin, name: member.name, source_asins: member.source_asins || [member.asin] })),
        }),
      });
      invalidateAllDetails();
      message.textContent = "";
      await Promise.all([loadWatches(), loadCalendar(), refreshAfterChange()]);
      showToast(decision === "merge" ? "Authors merged" : "Authors kept separate");
    } catch (error) {
      if (error.name !== "AbortError") message.textContent = error.message;
    } finally {
      busy = false;
      setContentBusy(false);
    }
  }

  refreshButton?.addEventListener("click", refreshExpandedSections);
  content?.addEventListener("change", updateSplitButtons);
  content?.addEventListener("click", (event) => {
    const retry = event.target.closest("[data-author-management-retry]");
    if (retry) {
      loadSection(retry.dataset.authorManagementRetry, true);
      return;
    }
    const split = event.target.closest("[data-author-management-split]");
    if (split) {
      openSplitConfirmation(Number(split.dataset.authorManagementSplit));
      return;
    }
    const candidate = event.target.closest("[data-author-management-candidate]");
    if (candidate) {
      decideCandidate(
        Number(candidate.dataset.authorManagementCandidate),
        candidate.dataset.authorManagementDecision,
      );
    }
  });
  $("#author-split-close")?.addEventListener("click", closeAuthorSplitConfirmation);
  $("#author-split-cancel")?.addEventListener("click", closeAuthorSplitConfirmation);
  $("#author-split-confirm")?.addEventListener("click", confirmSplit);
  splitModal?.addEventListener("click", (event) => {
    if (event.target.id === "author-split-modal") closeAuthorSplitConfirmation();
  });

  return {
    closeAuthorSplitConfirmation,
    loadAuthorManagement,
  };
}
