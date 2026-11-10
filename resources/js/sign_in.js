if (typeof NL_PORT !== "undefined" && typeof NL_TOKEN !== "undefined") {
    Neutralino.init();
    Neutralino.events.on("windowClose", () => {
        Neutralino.app.exit();
    });
}

const signinForm = document.getElementById("signin-form");
const authToggle = document.getElementById("auth-toggle");
const serverUrlInput = document.getElementById("server-url");
const usernameInput = document.getElementById("username");
const passwordInput = document.getElementById("password");
const formMessage = document.getElementById("form-message");
const submitButton = document.getElementById("submit-button");
const API_URL_KEY = "zircon.apiUrl";
const TOKEN_KEY = "zircon.accessToken";
const USER_KEY = "zircon.user";
let authMode = "login";

serverUrlInput.value = localStorage.getItem(API_URL_KEY) || serverUrlInput.value;

authToggle.addEventListener("click", () => {
    authMode = authMode === "login" ? "register" : "login";
    submitButton.textContent = authMode === "login" ? "Sign in" : "Create account";
    authToggle.textContent = authMode === "login" ?
        "Need an account? Create one" :
        "Already have an account? Sign in";
    passwordInput.autocomplete = authMode === "login" ? "current-password" : "new-password";
    formMessage.textContent = "";
});

signinForm.addEventListener("submit", async(event) => {
    event.preventDefault();
    formMessage.textContent = "";
    submitButton.disabled = true;

    try {
        const serverUrl = serverUrlInput.value.trim().replace(/\/+$/, "");
        const parsedUrl = new URL(serverUrl);
        if (parsedUrl.protocol !== "http:" && parsedUrl.protocol !== "https:") {
            throw new Error("The chat server URL must start with http:// or https://.");
        }

        const response = await fetch(
            `${serverUrl}/api/auth/${authMode === "login" ? "login" : "register"}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    username: usernameInput.value.trim(),
                    password: passwordInput.value
                })
            }
        );
        const data = await response.json();
        if (!response.ok) {
            const detail = typeof data.detail === "string" ?
                data.detail :
                "Please check your details and try again.";
            throw new Error(detail);
        }

        localStorage.setItem(API_URL_KEY, serverUrl);
        localStorage.setItem(TOKEN_KEY, data.access_token);
        localStorage.setItem(USER_KEY, JSON.stringify(data.user));
        window.location.href = "main.html";
    } catch (error) {
        formMessage.textContent = error instanceof TypeError ?
            "Could not reach that chat server. Check the URL and that the server is running." :
            error.message;
    } finally {
        submitButton.disabled = false;
    }
});