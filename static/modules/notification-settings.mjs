// Notification settings share the main settings form and save action.
export function bindNotificationSettings(api, onChange) {
  const panel = document.querySelector("#settings-panel-notifications");
  if (!panel) return null;
  const inputs = [...panel.querySelectorAll("[data-notification]")];
  const calendarLinks = [...panel.querySelectorAll("[data-calendar-link]")];
  const configureButtons = [...panel.querySelectorAll("[data-notification-configure]")];
  const testButtons = [...panel.querySelectorAll("[data-notification-test]")];
  const format = panel.querySelector("[data-notification-format]");
  const field = (name) => panel.querySelector(`[data-notification="${name}"]`);
  const schedule = [...panel.querySelectorAll('[name="notification-schedule-link"]')];
  const scheduleLink = () => schedule.find((input) => input.checked).value;
  let changed = false;

  function setVisible(selector, visible) {
    const label = panel.querySelector(selector);
    label.hidden = !visible;
    label.querySelector("input, select").disabled = !visible;
  }

  function updateDeliveryFields() {
    const linked = scheduleLink() === "after_sync";
    const separateDigest = !linked && format.value !== "per_book";
    const notificationsOn = field("enabled").checked;
    panel.querySelector("#notification-options").disabled = !notificationsOn;
    setVisible("[data-notification-format-field]", !linked);
    setVisible("[data-digest-time]", separateDigest);
    setVisible("[data-digest-weekday]", separateDigest && format.value === "weekly");
    setVisible("[data-sync-delay]", linked);
    panel.querySelector("#settings-notification-zone").hidden = !separateDigest;
    panel.querySelector("#notification-sync-cadence").hidden = !linked;
    panel.querySelector("#settings-digest-status").hidden = !notificationsOn || !(linked || separateDigest);
    let hasService = false;
    for (const status of panel.querySelectorAll("[data-service-state]")) {
      const serviceOn = field(`${status.dataset.serviceState}.enabled`).checked;
      hasService ||= serviceOn;
      status.textContent = serviceOn ? "Enabled" : "Off";
      status.classList.toggle("is-enabled", serviceOn);
    }
    panel.querySelector("#notification-service-needed").hidden = !notificationsOn || hasService;
    for (const reminder of panel.querySelectorAll("[data-notification-save-reminder]")) reminder.hidden = !changed;
    for (const button of testButtons) {
      button.disabled = changed || button.hasAttribute("aria-busy") || !field(`${button.dataset.notificationTest}.enabled`).checked;
    }
  }
  panel.addEventListener("input", (event) => {
    if (calendarLinks.includes(event.target)) {
      for (const link of calendarLinks) {
        if (link !== event.target) link.value = event.target.value;
      }
    }
    changed = true;
    updateDeliveryFields();
    onChange();
  });

  for (const button of configureButtons) {
    button.addEventListener("click", () => {
      const opening = button.getAttribute("aria-expanded") === "false";
      for (const control of configureButtons) {
        const open = opening && control === button;
        panel.querySelector(`#${control.getAttribute("aria-controls")}`).hidden = !open;
        control.setAttribute("aria-expanded", String(open));
        control.querySelector("span").textContent = open ? "Close" : "Configure";
      }
    });
  }

  function needsAutomaticSync() {
    return field("enabled").checked && scheduleLink() === "after_sync";
  }

  function populate(settings) {
    if (!settings) return;
    changed = false;
    for (const link of calendarLinks) link.value = settings.calendar_url || "";
    for (const input of schedule) input.checked = input.value === settings.schedule_link;
    format.value = settings.mode === "per_book" ? "per_book" : settings.cadence;
    for (const input of inputs) {
      const [section, key] = input.dataset.notification.split(".");
      if (key === "secret") {
        input.value = "";
        input.placeholder = settings[section].has_secret ? "Stored" : "";
      } else {
        const value = key ? settings[section][key] : settings[section];
        if (input.type === "checkbox") input.checked = value;
        else input.value = value;
      }
    }
    for (const input of panel.querySelectorAll("[data-notification-clear]")) {
      input.checked = false;
      input.disabled = !settings[input.dataset.notificationClear].has_secret;
      input.closest("label").hidden = input.disabled;
    }
    for (const status of panel.querySelectorAll("[data-notification-status]")) {
      const error = settings[status.dataset.notificationStatus].error;
      status.textContent = error ? `${error} Delivery will be retried.` : "";
    }
    updateDeliveryFields();
  }

  function read() {
    if (!changed) return undefined;
    const link = scheduleLink();
    const linked = link === "after_sync";
    const settings = {
      schedule_link: link,
      calendar_url: calendarLinks[0].value.trim(),
      mode: !linked && format.value === "per_book" ? "per_book" : "digest",
      cadence: format.value === "weekly" ? "weekly" : "daily",
    };
    for (const input of inputs) {
      const [section, key] = input.dataset.notification.split(".");
      const value = input.type === "checkbox" ? input.checked : input.value.trim();
      if (!key) {
        if (section === "time" && !value && input.matches(":disabled")) continue;
        settings[section] = ["weekday", "delay_minutes"].includes(section) ? Number(value) : value;
        continue;
      }
      const destination = settings[section] ??= {};
      if (key === "secret") {
        const clear = panel.querySelector(`[data-notification-clear="${section}"]`).checked;
        if (input.value) destination.secret = input.value;
        else if (clear) destination.secret = null;
      } else {
        destination[key] = key === "port" ? Number(value || 587) : value;
      }
    }
    return settings;
  }

  for (const button of testButtons) {
    button.addEventListener("click", async () => {
      const channel = button.dataset.notificationTest;
      const status = panel.querySelector(`[data-notification-status="${channel}"]`);
      button.setAttribute("aria-busy", "true");
      updateDeliveryFields();
      status.textContent = "Sending test…";
      try {
        await api(`/api/settings/notifications/${channel}/test`, { method: "POST" });
        status.textContent = "The server accepted the test notification.";
      } catch (error) {
        status.textContent = error.message;
      } finally {
        button.removeAttribute("aria-busy");
        updateDeliveryFields();
      }
    });
  }
  return { populate, read, needsAutomaticSync };
}
