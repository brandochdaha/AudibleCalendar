// The optional setup steps use the same preferences as Settings.
const serviceNames = { email: "Email", ntfy: "ntfy", gotify: "Gotify" };
const weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function options(name, choices) {
  return choices.map(([value, label], index) => `
    <label class="setup-choice"><input type="radio" name="${name}" value="${value}" ${index === 0 ? "checked" : ""}> ${label}</label>
  `).join("");
}

const steps = {
  sync: ["Sync with Audible automatically?", `
    <p>Syncing with Audible updates your calendar with new releases from the authors, series, and books you follow.</p>
    ${options("automatic", [["yes", "Yes, sync automatically"], ["no", "No, I’ll sync manually"]])}
  `],
  cadence: ["How often should AudibleCalendar sync?", `
    <label>Schedule <select name="cadence"><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="interval">Every few hours</option></select></label>
    <label data-cadence="weekly">Day <select name="weekday">${weekdays.map((day, index) => `<option value="${index}">${day}</option>`).join("")}</select></label>
    <label data-cadence="clock">Time in your time zone <input name="syncTime" type="time" value="08:45" required></label>
    <label data-cadence="interval">Hours between syncs <input name="hours" type="number" min="1" max="720" value="24" required></label>
  `],
  notify: ["Do you want release notifications?", `
    <ul class="setup-methods">
      <li><strong>Email</strong> · Supply your own SMTP mail server.</li>
      <li><strong>ntfy</strong> · Use a topic on ntfy.sh or your own ntfy server.</li>
      <li><strong>Gotify</strong> · Connect to a Gotify server with an app token.</li>
    </ul>
    <p>If your time zone differs from your Audible marketplace’s, some release notifications may arrive a day later.</p>
    ${options("notify", [["yes", "Yes, I want notifications"], ["no", "No notifications"]])}
  `],
  schedule: ["When should notifications go out?", `
    ${options("timing", [["linked", "Send digest after scheduled sync"], ["separate", "Use separate schedules"]])}
    <p>Linked digests are sent 15 minutes after each scheduled sync ends. You can change the delay in Settings.</p>
  `],
  format: ["How should we notify you?", `
    ${options("format", [["daily", "Daily digest"], ["weekly", "Weekly digest"], ["per_book", "One notification per book"]])}
    <p data-manual-note>With automatic sync off, new listings appear after you sync manually.</p>
  `],
  timing: ["When should the digest arrive?", `
    <label data-digest-weekday>Day <select name="digestWeekday">${weekdays.map((day, index) => `<option value="${index}">${day}</option>`).join("")}</select></label>
    <label>Send time in your time zone <input name="sendTime" type="time" value="09:00" required></label>
    <p data-timing-note role="status" hidden></p>
  `],
  service: ["Set up a notification service now?", `
    ${options("service", [["later", "Later in Settings"], ...Object.entries(serviceNames).map(([id, label]) => [id, "Set up " + label])])}
    <p>Setting up later saves your choices. Sending starts when you enable a service.</p>
  `],
  details: ["Connect your notification service", `
    <div class="setup-fields setup-provider" data-provider="email">
      <label>SMTP server <input name="host" placeholder="smtp.example.com" required></label>
      <label>Port <input name="port" type="number" min="1" max="65535" value="587" required></label>
      <label>Encryption <select name="encryption"><option value="starttls">STARTTLS</option><option value="tls">TLS</option><option value="none">None</option></select></label>
      <label>Username (if required) <input name="username" autocomplete="off"></label>
      <label>Password (if required) <input name="secret" type="password" autocomplete="new-password"></label>
      <label>Sender address <input name="sender" type="email" required></label>
      <label>Recipient address <input name="recipient" type="email" required></label>
    </div>
    <div class="setup-fields setup-provider" data-provider="ntfy">
      <label>Server URL <input name="url" type="url" value="https://ntfy.sh" required></label>
      <label>Topic <input name="topic" maxlength="64" pattern="[-_A-Za-z0-9]+" required></label>
      <label>Access token (optional) <input name="secret" type="password" autocomplete="new-password"></label>
    </div>
    <div class="setup-fields setup-provider" data-provider="gotify">
      <label>Server URL <input name="url" type="url" required></label>
      <label>Application token <input name="secret" type="password" autocomplete="new-password" required></label>
    </div>
  `],
};

export function bindOnboardingPreferences(form, { api, title, message, finish }) {
  form.innerHTML = Object.entries(steps).map(([name, [, content]]) => `
    <fieldset class="setup-fields" data-step="${name}" hidden disabled>${content}</fieldset>
  `).join("") + `
    <div class="setup-actions">
      <button class="button button-secondary" type="button" data-back>Back</button>
      <button class="button button-secondary" type="button" data-skip>Skip for now</button>
      <button class="button button-primary" type="submit" data-next>Continue</button>
    </div>`;

  const find = (selector) => form.querySelector(selector);
  const read = (name) => form.elements.namedItem(name).value;
  const choice = (name) => find(`[name="${name}"]:checked`).value;
  const automatic = () => choice("automatic") === "yes";
  const linked = () => automatic() && choice("timing") === "linked";
  const destination = () => choice("service");
  const path = [];
  let savedSettings;

  function reveal(element, visible) {
    element.hidden = !visible;
    for (const input of element.querySelectorAll("input, select")) input.disabled = !visible;
  }

  function render({ focus = false } = {}) {
    const current = path.at(-1);
    const cadence = read("cadence");
    for (const panel of form.querySelectorAll("[data-step]")) {
      panel.hidden = panel.disabled = panel.dataset.step !== current;
    }
    reveal(find('[data-cadence="weekly"]'), cadence === "weekly");
    reveal(find('[data-cadence="clock"]'), cadence !== "interval");
    reveal(find('[data-cadence="interval"]'), cadence === "interval");
    find("[data-manual-note]").hidden = automatic();
    reveal(find("[data-digest-weekday]"), choice("format") === "weekly");
    const note = find("[data-timing-note]");
    const beforeSync = automatic() && cadence === "daily" && read("sendTime") <= read("syncTime");
    const infrequent = choice("format") === "daily" && automatic()
      && (cadence === "weekly" || (cadence === "interval" && Number(read("hours")) > 24));
    note.textContent = beforeSync
      ? "This summary may go out before the daily sync finishes. It can miss new listings or include unconfirmed releases."
      : infrequent
        ? "Sync runs less than daily. On days without a sync, the summary may include releases based on scheduled dates that Audible hasn’t confirmed."
        : "";
    note.hidden = !note.textContent;
    for (const panel of form.querySelectorAll("[data-provider]")) reveal(panel, panel.dataset.provider === destination());
    title.textContent = current === "details" ? "Set up " + serviceNames[destination()] : steps[current][0];
    find("[data-back]").hidden = path.length === 1;
    find("[data-skip]").hidden = !["sync", "details"].includes(current);
    find("[data-skip]").textContent = current === "details" ? "Set up later" : "Skip for now";
    find("[data-next]").textContent = current === "details" ||
      (current === "service" && destination() === "later") ||
      (current === "notify" && choice("notify") === "no") ? "Save and open calendar" : "Continue";
    if (focus) find(`[data-step="${current}"] input:not([disabled]), [data-step="${current}"] select:not([disabled])`)?.focus();
  }

  function preferences(service) {
    const notifications = { ...savedSettings.notifications };
    notifications.enabled = choice("notify") === "yes";
    for (const channel of Object.keys(serviceNames)) {
      notifications[channel] = { ...notifications[channel], enabled: channel === service };
    }
    if (choice("notify") === "yes") {
      notifications.mode = linked() || choice("format") !== "per_book" ? "digest" : "per_book";
      notifications.schedule_link = linked() ? "after_sync" : "independent";
      notifications.cadence = choice("format") === "weekly" ? "weekly" : "daily";
      notifications.weekday = Number(read("digestWeekday"));
      if (read("sendTime")) notifications.time = read("sendTime");
      if (linked()) notifications.delay_minutes = 15;
    }
    if (service !== "later") {
      const inputs = find(`[data-provider="${service}"]`).querySelectorAll("input, select");
      for (const { name, value } of inputs) {
        if (name === "secret" && !value) continue;
        notifications[service][name] = name === "port" ? Number(value) : name === "secret" ? value : value.trim();
      }
    }
    return {
      automatic_sync_enabled: automatic(),
      favourites_sync_enabled: false,
      ...(automatic() && {
        sync_cadence: read("cadence"),
        ...(read("cadence") === "interval"
          ? { sync_interval_hours: Number(read("hours")) }
          : { sync_time: read("syncTime"), sync_weekday: Number(read("weekday")) }),
      }),
      notifications,
    };
  }

  async function save(service) {
    const buttons = form.querySelectorAll("button");
    for (const button of buttons) button.disabled = true;
    message.textContent = "Saving your setup…";
    try {
      await api("/api/settings", { method: "PUT", body: JSON.stringify(preferences(service)) });
      finish();
    } catch (error) {
      message.textContent = error.message;
    } finally {
      for (const button of buttons) button.disabled = false;
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const next = {
      sync: automatic() ? "cadence" : "notify",
      cadence: "notify",
      notify: choice("notify") === "yes" ? (automatic() ? "schedule" : "format") : null,
      schedule: linked() ? "service" : "format",
      format: choice("format") === "per_book" ? "service" : "timing",
      timing: "service",
      service: destination() === "later" ? null : "details",
      details: null,
    }[path.at(-1)];
    if (!next) {
      save(path.at(-1) === "details" ? destination() : "later");
      return;
    }
    path.push(next);
    message.textContent = "";
    render({ focus: true });
  });
  find("[data-back]").addEventListener("click", () => {
    path.pop();
    message.textContent = "";
    render({ focus: true });
  });
  find("[data-skip]").addEventListener("click", () => path.at(-1) === "sync" ? finish() : save("later"));
  form.addEventListener("input", () => render());
  return {
    start(settings) {
      savedSettings = settings;
      form.reset();
      path.splice(0, path.length, "sync");
      form.hidden = false;
      render({ focus: true });
    },
    reset() {
      form.reset();
      form.hidden = true;
      path.length = 0;
    },
  };
}
