// Login and the persistent application session gate.
import { api } from "./api.mjs?v=20260922-2";
import { $ } from "./format.mjs?v=20260922-2";


export function createAuthController({ onAuthenticated, onSignedOut }) {
  const gate = $("#auth-gate");
  const form = $("#login-form");
  const usernameInput = $("#login-username");
  const passwordInput = $("#login-password");
  const message = $("#login-message");
  const submitButton = $("#login-submit");

  function showLogin(messageText = "") {
    onSignedOut();
    gate.hidden = false;
    message.textContent = messageText;
    passwordInput.value = "";
    window.setTimeout(() => usernameInput.focus(), 0);
  }

  function hideLogin() {
    gate.hidden = true;
    message.textContent = "";
  }

  async function startAuthenticatedApp(session) {
    hideLogin();
    try {
      await onAuthenticated(session);
    } catch (error) {
      showLogin(error.message);
    }
  }

  async function checkSession() {
    try {
      const session = await api("/api/auth/session");
      if (session.authenticated) await startAuthenticatedApp(session);
      else showLogin();
    } catch (error) {
      showLogin(error.message);
    }
  }

  async function login(event) {
    event.preventDefault();
    message.textContent = "Signing in…";
    submitButton.disabled = true;
    try {
      await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({
          username: usernameInput.value,
          password: passwordInput.value,
        }),
      });
      window.location.reload();
    } catch (error) {
      message.textContent = error.message;
      passwordInput.select();
    } finally {
      submitButton.disabled = false;
    }
  }

  form.addEventListener("submit", login);
  window.addEventListener("audiblecalendar-auth-required", () => showLogin("Your session expired. Sign in again."));

  return { checkSession, showLogin };
}
