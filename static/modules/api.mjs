// Cancel obsolete account requests before they can update application state.
import { state } from "./state.mjs?v=20260922-2";
let generation = 0;
const pending = new Set();

export function invalidateRequests() {
  generation += 1;
  for (const controller of pending) controller.abort();
  pending.clear();
}

export async function api(path, options = {}) {
  const epoch = generation;
  const controller = new AbortController();
  pending.add(controller);
  try {
    const response = await fetch(path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(state.authenticated && path !== "/api/config" && !path.startsWith("/api/auth/")
          ? { "X-Audible-Region": state.region } : {}),
        ...(options.headers || {}),
      },
      signal: controller.signal,
    });
    const body = await response.json().catch(() => ({}));
    if (epoch !== generation) throw new DOMException("Session changed", "AbortError");
    if (!response.ok) {
      if (body.code === "region_changed" && typeof window !== "undefined") {
        invalidateRequests();
        window.location.reload();
      }
      if (response.status === 401 && typeof window !== "undefined") {
        invalidateRequests();
        window.dispatchEvent(new CustomEvent("audiblecalendar-auth-required"));
      }
      throw new Error(body.detail || "Request failed");
    }
    return body;
  } finally {
    pending.delete(controller);
  }
}
