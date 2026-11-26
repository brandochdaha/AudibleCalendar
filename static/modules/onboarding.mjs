// The required steps commit credentials and catalogue choices together.
import { $ } from "./format.mjs?v=20260922-2";
import { openModal, closeModal } from "./modal-accessibility.mjs?v=20260922-2";
import { timeZoneSettings } from "./time-zone.mjs?v=20261010-2";
import { bindOnboardingPreferences } from "./onboarding-preferences.mjs?v=20261118-11";

export function createOnboarding({ api, onAccountSaved }) {
  const credentials = $("#setup-credentials");
  const marketplace = $("#setup-marketplace");
  const region = $("#setup-region");
  const zone = $("#setup-time-zone");
  const username = $("#setup-username");
  const password = $("#setup-password");
  const confirmation = $("#setup-password-confirm");
  const message = $("#setup-message");
  const preferences = bindOnboardingPreferences($("#setup-preferences"), {
    api,
    title: $("#setup-title"),
    message,
    finish: () => window.location.reload(),
  });
  let initialSettings;

  function showStep(chooseMarketplace) {
    credentials.hidden = chooseMarketplace;
    marketplace.hidden = !chooseMarketplace;
    $("#setup-progress").textContent = chooseMarketplace ? "Step 2 of 3" : "Step 1 of 3";
    $("#setup-title").textContent = chooseMarketplace ? "Choose your Audible marketplace" : "Create your admin account";
    message.textContent = "";
    (chooseMarketplace ? region : username).focus();
  }

  function reset() {
    credentials.reset();
    preferences.reset();
    initialSettings = null;
    closeModal("#setup-wizard", { restoreFocus: false });
  }

  async function start() {
    initialSettings = await api("/api/auth/setup");
    const { values, timeZoneOptions } = timeZoneSettings(initialSettings);
    username.value = initialSettings.auth.username;
    region.replaceChildren(...initialSettings.region_options.map(({ value, label }) => new Option(label, value)));
    region.value = initialSettings.first_install_setup ? "us" : values.region;
    zone.replaceChildren(...timeZoneOptions.map(({ value, label }) => new Option(label, value)));
    zone.value = values.time_zone;
    showStep(false);
    openModal("#setup-wizard", { initialFocus: "#setup-username" });
  }

  credentials.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!username.value.trim()) {
      message.textContent = "Enter a username.";
      username.focus();
    } else if (Array.from(password.value).length < 3) {
      message.textContent = "Password must be at least 3 characters.";
      password.focus();
    } else if (password.value !== confirmation.value) {
      message.textContent = "The passwords do not match.";
      confirmation.focus();
    } else {
      showStep(true);
    }
  });
  $("#setup-back").addEventListener("click", () => showStep(false));
  marketplace.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = $("#setup-confirm");
    const back = $("#setup-back");
    submit.disabled = back.disabled = true;
    message.textContent = "Saving your setup…";
    try {
      const saved = await api("/api/auth/setup", {
        method: "PUT",
        body: JSON.stringify({
          auth_username: username.value.trim(),
          auth_password: password.value,
          region: region.value,
          time_zone: zone.value,
        }),
      });
      const settings = { ...initialSettings, ...saved };
      credentials.reset();
      initialSettings = null;
      marketplace.hidden = true;
      $("#setup-progress").textContent = "Sync and notifications";
      message.textContent = "";
      onAccountSaved(settings);
      preferences.start(settings);
    } catch (error) {
      message.textContent = error.message;
    } finally {
      submit.disabled = back.disabled = false;
    }
  });
  return { start, reset };
}
