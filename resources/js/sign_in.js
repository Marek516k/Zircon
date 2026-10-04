Neutralino.init();
Neutralino.events.on("windowClose", () => {
    Neutralino.app.exit();
});

document.getElementById('signin-form').addEventListener('submit', async(event) => {
    event.preventDefault();

    const form = event.currentTarget;
    const username = form.elements.username.value;
    const password = form.elements.password.value;
    const message = document.getElementById('form-message');
    const submitButton = form.querySelector('button[type="submit"]');

    submitButton.disabled = true;
    message.textContent = '';

    try {
        const response = await fetch('users.json', { cache: 'no-store' });
        if (!response.ok) {
            throw new Error('Unable to load sign-in credentials.');
        }
        // JSON file contains an array of users, it will check if the provided username and password match any user in the array

        const account = await response.json();
        if (username !== account.username || password !== account.password) {
            message.textContent = 'Invalid username or password.';
            return;
        }
        window.location.href = 'main.html';
        // If the credentials are valid it redirects to the main page

    } catch (error) {
        message.textContent = error.message; // simply just displays the error message
    } finally {
        if (document.contains(submitButton)) {
            submitButton.disabled = false;
        }
    }
    // allways re-enables the submit button after the fetch operation is complete, regardless of success or failure
});

// zatím pouze pro lokální testování, v budoucnu se bude ověřovat na serveru a připojí se db