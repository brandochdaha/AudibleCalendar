// Shared keyboard and focus behavior for every modal in the application.
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

let modalLayers = [];
const returnTargets = new WeakMap();
let closingAllLayers = false;

function elementFor(value) {
  if (!value) return null;
  return typeof value === "string" ? document.querySelector(value) : value;
}

function visibleFocusableElements(container) {
  if (!container) return [];
  return [...container.querySelectorAll(FOCUSABLE_SELECTOR)].filter((element) => (
    !element.hidden
    && element.getAttribute("aria-hidden") !== "true"
    && (element.getClientRects().length > 0 || element === document.activeElement)
  ));
}

function focusFirst(container, preferred = null) {
  const target = elementFor(preferred);
  if (target && container.contains(target) && !target.disabled) {
    target.focus();
    return target;
  }
  const first = visibleFocusableElements(container)[0];
  if (first) {
    first.focus();
    return first;
  }
  const dialog = container.querySelector("[role='dialog']");
  if (dialog) {
    dialog.tabIndex = -1;
    dialog.focus();
  }
  return dialog;
}

function canRestoreFocus(element) {
  if (!element?.isConnected || typeof element.focus !== "function") return false;
  const backdrop = element.closest?.(".modal-backdrop");
  return !backdrop || (!backdrop.hidden && !backdrop.inert);
}

function descriptorForElement(element, container = document) {
  if (!element || !container.contains(element)) return null;
  if (element.id) return { selector: `#${escapeSelectorValue(element.id)}`, index: -1 };
  const dataAttributes = [...element.attributes].filter((attribute) => attribute.name.startsWith("data-"));
  const selector = dataAttributes.length
    ? `${element.tagName.toLowerCase()}${dataAttributes.map((attribute) => `[${attribute.name}="${escapeSelectorValue(attribute.value)}"]`).join("")}`
    : null;
  return {
    selector,
    index: visibleFocusableElements(container).indexOf(element),
  };
}

function resolveReturnTarget(record) {
  if (canRestoreFocus(record?.element)) return record.element;
  if (!record?.descriptor?.selector) return null;
  try {
    const replacement = document.querySelector(record.descriptor.selector);
    return canRestoreFocus(replacement) ? replacement : null;
  } catch (_error) {
    return null;
  }
}

function syncLayerAccessibility() {
  modalLayers = modalLayers.filter((layer) => layer?.isConnected && !layer.hidden);
  const top = modalLayers.at(-1);
  modalLayers.forEach((layer) => {
    const obscured = layer !== top;
    layer.inert = obscured;
    if (obscured) layer.setAttribute("aria-hidden", "true");
    else layer.removeAttribute("aria-hidden");
  });
}

export function openModal(value, { initialFocus = null } = {}) {
  const layer = elementFor(value);
  if (!layer) return null;
  const alreadyOpen = !layer.hidden && modalLayers.includes(layer);
  if (!alreadyOpen) {
    const activeElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    returnTargets.set(layer, {
      element: activeElement,
      descriptor: descriptorForElement(activeElement),
    });
    modalLayers = modalLayers.filter((candidate) => candidate !== layer);
    modalLayers.push(layer);
  }
  layer.hidden = false;
  syncLayerAccessibility();
  return focusFirst(layer, initialFocus);
}

export function closeModal(value, { restoreFocus = true } = {}) {
  const layer = elementFor(value);
  if (!layer) return;
  const returnTarget = returnTargets.get(layer);
  layer.hidden = true;
  layer.inert = false;
  layer.removeAttribute("aria-hidden");
  modalLayers = modalLayers.filter((candidate) => candidate !== layer);
  returnTargets.delete(layer);
  syncLayerAccessibility();
  if (!restoreFocus || closingAllLayers) return;
  const resolvedReturnTarget = resolveReturnTarget(returnTarget);
  if (resolvedReturnTarget) resolvedReturnTarget.focus();
  else if (modalLayers.length) focusFirst(modalLayers.at(-1));
}

export function closeAllModalLayers(closeLayers) {
  const rootReturnTarget = modalLayers.length ? returnTargets.get(modalLayers[0]) : null;
  closingAllLayers = true;
  try {
    closeLayers();
  } finally {
    modalLayers.forEach((layer) => {
      layer.inert = false;
      layer.removeAttribute("aria-hidden");
    });
    modalLayers = [];
    closingAllLayers = false;
  }
  const resolvedReturnTarget = resolveReturnTarget(rootReturnTarget);
  if (resolvedReturnTarget) resolvedReturnTarget.focus();
}

function escapeSelectorValue(value) {
  if (globalThis.CSS?.escape) return CSS.escape(value);
  return String(value).replace(/["\\]/g, "\\$&");
}

export function captureModalFocus(value) {
  const container = elementFor(value);
  const active = document.activeElement;
  if (!container?.contains(active)) return null;
  return descriptorForElement(active, container);
}

export function restoreModalFocus(value, descriptor) {
  const container = elementFor(value);
  if (!container) return;
  let target = null;
  if (descriptor?.selector) {
    try {
      target = container.querySelector(descriptor.selector);
    } catch (_error) {
      target = null;
    }
  }
  if (!target && Number.isInteger(descriptor?.index) && descriptor.index >= 0) {
    target = visibleFocusableElements(container)[descriptor.index];
  }
  if (target) target.focus();
  else focusFirst(container);
}

export function handleTablistKeydown(event, tabSelector) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return false;
  const current = event.target.closest(tabSelector);
  if (!current) return false;
  const tablist = current.closest("[role='tablist']");
  const tabs = [...(tablist?.querySelectorAll(tabSelector) || [])].filter((tab) => !tab.disabled && !tab.hidden);
  if (!tabs.length) return false;
  const currentIndex = Math.max(0, tabs.indexOf(current));
  let nextIndex = currentIndex;
  if (event.key === "Home") nextIndex = 0;
  else if (event.key === "End") nextIndex = tabs.length - 1;
  else if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
  else nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
  event.preventDefault();
  tabs[nextIndex].focus();
  tabs[nextIndex].click();
  return true;
}

if (typeof document !== "undefined") {
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    syncLayerAccessibility();
    const top = modalLayers.at(-1);
    if (!top) return;
    const focusable = visibleFocusableElements(top);
    if (!focusable.length) {
      event.preventDefault();
      focusFirst(top);
      return;
    }
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    } else if (!top.contains(document.activeElement)) {
      event.preventDefault();
      first.focus();
    }
  });
}
