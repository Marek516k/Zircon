if (typeof NL_PORT !== "undefined" && typeof NL_TOKEN !== "undefined") {
    Neutralino.init();
    Neutralino.events.on("windowClose", () => {
        Neutralino.app.exit();
    });
}

const API_URL_KEY = "zircon.apiUrl";
const TOKEN_KEY = "zircon.accessToken";
const USER_KEY = "zircon.user";
const apiUrl = (localStorage.getItem(API_URL_KEY) || "").replace(/\/+$/, "");
const accessToken = localStorage.getItem(TOKEN_KEY);
const serverList = document.getElementById("server-list");
const channelList = document.getElementById("channel-list");
const serverName = document.getElementById("server-name");
const channelHeading = document.getElementById("channel-heading");
const connectionStatus = document.getElementById("connection-status");
const messageList = document.getElementById("message-list");
const messageForm = document.getElementById("message-form");
const messageInput = document.getElementById("message-input");
const sendButton = document.getElementById("send-button");
const chatNotice = document.getElementById("chat-notice");
const noticeTimer = { id: null };
const state = {
    user: null,
    servers: [],
    channels: [],
    selectedServer: null,
    selectedChannel: null,
    websocket: null,
    reconnectTimer: null,
    selectionVersion: 0,
    messages: new Map()
};

if (!apiUrl || !accessToken) {
    window.location.replace("index.html");
} else {
    try {
        state.user = JSON.parse(localStorage.getItem(USER_KEY) || "null");
    } catch (error) {
        console.error("Could not read the saved Zircon account.", error);
    }

    document.getElementById("account-name").textContent =
        state.user ? state.user.username || "Signed in" : "Signed in";
    document.getElementById("account-avatar").textContent =
        (state.user ? state.user.username || "?" : "?").slice(0, 1).toUpperCase();
    void startApp();
}

function showNotice(message) {
    chatNotice.textContent = message;
    clearTimeout(noticeTimer.id);
    if (message) {
        noticeTimer.id = setTimeout(() => {
            chatNotice.textContent = "";
        }, 6000);
    }
}

async function apiRequest(path, options = {}) {
    let response;
    try {
        response = await fetch(`${apiUrl}/api${path}`, {
            ...options,
            headers: {
                Authorization: `Bearer ${accessToken}`,
                ...(options.body ? { "Content-Type": "application/json" } : {}),
                ...options.headers
            }
        });
    } catch (error) {
        throw new Error("Could not reach the chat server. Check your connection.");
    }

    let data;
    try {
        data = await response.json();
    } catch (error) {
        data = null;
    }
    if (response.status === 401) {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
        window.location.replace("index.html");
        throw new Error("Your session expired. Please sign in again.");
    }
    if (!response.ok) {
        throw new Error(
            typeof data === "object" && data !== null && typeof data.detail === "string" ?
            data.detail :
            "The request failed."
        );
    }
    return data;
}

async function startApp() {
    try {
        state.servers = await apiRequest("/servers");
        renderServers();
        if (state.servers.length) {
            await selectServer(state.servers[0]);
        } else {
            serverName.textContent = "Your servers";
            setEmptyState("Add a server or join one using an invite code.");
            connectionStatus.textContent = "Create or join a chat server to get started.";
        }
    } catch (error) {
        connectionStatus.textContent = "Could not connect.";
        showNotice(error.message);
    }
}

function renderServers() {
    serverList.replaceChildren();
    for (const server of state.servers) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "server-button";
        button.textContent = server.name.slice(0, 2).toUpperCase();
        button.title = server.name;
        button.setAttribute("aria-label", server.name);
        button.setAttribute(
            "aria-pressed",
            String(state.selectedServer && state.selectedServer.id === server.id)
        );
        button.addEventListener("click", () => void selectServer(server));
        serverList.append(button);
    }
}

async function selectServer(server) {
    state.selectedServer = server;
    state.selectedChannel = null;
    state.selectionVersion++;
    disconnectWebSocket();
    state.messages.clear();
    serverName.textContent = server.name;
    channelHeading.textContent = "Choose a channel";
    connectionStatus.textContent = "Loading channels...";
    messageInput.disabled = true;
    sendButton.disabled = true;
    document.getElementById("open-channel-dialog").disabled = false;
    renderServers();
    channelList.replaceChildren();
    setEmptyState("Choose a channel to see its messages.");

    try {
        const channels = await apiRequest(`/servers/${server.id}/channels`);
        if (state.selectedServer && state.selectedServer.id !== server.id) {
            return;
        }
        state.channels = channels;
        renderChannels(channels);
        if (channels.length) {
            await selectChannel(channels[0]);
        } else {
            connectionStatus.textContent = "Create a channel to start chatting.";
        }
    } catch (error) {
        connectionStatus.textContent = "Could not load channels.";
        showNotice(error.message);
    }
}

function renderChannels(channels) {
    channelList.replaceChildren();
    for (const channel of channels) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "channel-button";
        button.textContent = `#  ${channel.name}`;
        button.dataset.channelId = String(channel.id);
        button.setAttribute(
            "aria-pressed",
            String(state.selectedChannel && state.selectedChannel.id === channel.id)
        );
        button.addEventListener("click", () => void selectChannel(channel));
        channelList.append(button);
    }
}

async function selectChannel(channel) {
    const version = ++state.selectionVersion;
    state.selectedChannel = channel;
    disconnectWebSocket();
    state.messages.clear();
    renderChannels(state.channels);
    channelHeading.textContent = `# ${channel.name}`;
    connectionStatus.textContent = "Loading messages...";
    messageInput.disabled = false;
    sendButton.disabled = false;
    messageInput.placeholder = `Message #${channel.name}`;
    setEmptyState("No messages yet. Start the conversation.");

    try {
        const messages = await apiRequest(
            `/servers/${state.selectedServer.id}/channels/${channel.id}/messages`
        );
        if (version !== state.selectionVersion) {
            return;
        }
        for (const message of messages) {
            state.messages.set(message.id, message);
        }
        renderMessages();
        connectionStatus.textContent = "Connecting to chat...";
        connectWebSocket(state.selectedServer.id, channel.id);
    } catch (error) {
        if (version === state.selectionVersion) {
            connectionStatus.textContent = "Could not load messages.";
            showNotice(error.message);
        }
    }
}

function setEmptyState(message) {
    messageList.replaceChildren();
    const emptyState = document.createElement("p");
    emptyState.className = "empty-state";
    emptyState.textContent = message;
    messageList.append(emptyState);
}

function renderMessages() {
    const shouldScroll = messageList.scrollHeight - messageList.scrollTop -
        messageList.clientHeight < 80;
    const messages = Array.from(state.messages.values())
        .sort((left, right) => left.id - right.id);
    messageList.replaceChildren();

    if (!messages.length) {
        setEmptyState("No messages yet. Start the conversation.");
        return;
    }
    for (const message of messages) {
        const article = document.createElement("article");
        article.className = "message";
        const header = document.createElement("div");
        header.className = "message-meta";
        const author = document.createElement("strong");
        author.textContent = message.author_username;
        const time = document.createElement("time");
        time.dateTime = message.created_at;
        const date = new Date(message.created_at);
        time.textContent = Number.isNaN(date.getTime()) ?
            "" :
            date.toLocaleString([], { dateStyle: "short", timeStyle: "short" });
        const content = document.createElement("p");
        content.textContent = message.content;
        header.append(author, time);
        article.append(header, content);
        messageList.append(article);
    }
    if (shouldScroll) {
        messageList.scrollTop = messageList.scrollHeight;
    }
}

function connectWebSocket(serverId, channelId) {
    if (state.reconnectTimer !== null) {
        clearTimeout(state.reconnectTimer);
        state.reconnectTimer = null;
    }
    const websocketUrl = new URL(apiUrl);
    websocketUrl.protocol = websocketUrl.protocol === "https:" ? "wss:" : "ws:";
    websocketUrl.pathname =
        `${websocketUrl.pathname.replace(/\/+$/, "")}/api/servers/${serverId}/channels/${channelId}/ws`;
    websocketUrl.search = "";
    websocketUrl.hash = "";

    let websocket;
    try {
        websocket = new WebSocket(websocketUrl);
    } catch (error) {
        connectionStatus.textContent = "Could not connect to chat.";
        showNotice("Check that the chat server supports WebSocket connections.");
        return;
    }
    state.websocket = websocket;

    websocket.addEventListener("open", () => {
        if (state.websocket !== websocket) {
            return;
        }
        connectionStatus.textContent = "Authenticating...";
        websocket.send(JSON.stringify({ type: "authenticate", token: accessToken }));
    });

    websocket.addEventListener("message", (event) => {
        if (state.websocket !== websocket) {
            return;
        }
        let payload;
        try {
            payload = JSON.parse(event.data);
        } catch (error) {
            showNotice("The chat server sent an unreadable response.");
            return;
        }
        if (payload.type === "ready") {
            connectionStatus.textContent = "Connected";
            void refreshMessageHistory(serverId, channelId);
        } else if (payload.type === "message" && payload.message) {
            state.messages.set(payload.message.id, payload.message);
            renderMessages();
        } else if (payload.type === "error") {
            showNotice(payload.detail || "The chat server rejected that message.");
        }
    });

    websocket.addEventListener("close", (event) => {
        if (state.websocket !== websocket) {
            return;
        }
        state.websocket = null;
        if (event.code === 4401 || event.code === 4403 || event.code === 4404) {
            connectionStatus.textContent = "Could not join this chat.";
            showNotice(event.reason || "The chat server rejected the connection.");
            return;
        }
        connectionStatus.textContent = "Disconnected. Reconnecting...";
        state.reconnectTimer = setTimeout(() => {
            if (state.selectedServer && state.selectedServer.id === serverId &&
                state.selectedChannel && state.selectedChannel.id === channelId) {
                connectWebSocket(serverId, channelId);
            }
        }, 2000);
    });
}

async function refreshMessageHistory(serverId, channelId) {
    try {
        const messages = await apiRequest(
            `/servers/${serverId}/channels/${channelId}/messages`
        );
        if (state.selectedServer && state.selectedServer.id !== serverId ||
            state.selectedChannel && state.selectedChannel.id !== channelId) {
            return;
        }
        for (const message of messages) {
            state.messages.set(message.id, message);
        }
        renderMessages();
    } catch (error) {
        showNotice(error.message);
    }
}

function disconnectWebSocket() {
    if (state.reconnectTimer !== null) {
        clearTimeout(state.reconnectTimer);
        state.reconnectTimer = null;
    }
    if (state.websocket) {
        const websocket = state.websocket;
        state.websocket = null;
        websocket.close();
    }
}

messageForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const content = messageInput.value.trim();
    if (!content || state.websocket && state.websocket.readyState !== WebSocket.OPEN) {
        if (content) {
            showNotice("Wait for the chat connection before sending a message.");
        }
        return;
    }
    state.websocket.send(JSON.stringify({ content }));
    messageInput.value = "";
});

document.getElementById("open-server-dialog").addEventListener("click", () => {
    document.getElementById("server-dialog").showModal();
});

document.getElementById("open-channel-dialog").addEventListener("click", () => {
    document.getElementById("channel-dialog").showModal();
});

for (const closeButton of document.querySelectorAll("[data-close-dialog]")) {
    closeButton.addEventListener("click", () => closeButton.closest("dialog").close());
}

document.getElementById("create-server-form").addEventListener("submit", async(event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    const message = document.getElementById("create-server-message");
    button.disabled = true;
    message.textContent = "";
    try {
        const server = await apiRequest("/servers", {
            method: "POST",
            body: JSON.stringify({ name: form.elements.name.value.trim() })
        });
        state.servers.push(server);
        renderServers();
        await selectServer(server);
        form.reset();
        document.getElementById("server-dialog").close();
        showNotice(`Server created. Share this invite code: ${server.invite_code}`);
    } catch (error) {
        message.textContent = error.message;
    } finally {
        button.disabled = false;
    }
});

document.getElementById("join-server-form").addEventListener("submit", async(event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    const message = document.getElementById("join-server-message");
    button.disabled = true;
    message.textContent = "";
    try {
        const server = await apiRequest("/servers/join", {
            method: "POST",
            body: JSON.stringify({ invite_code: form.elements.invite_code.value.trim() })
        });
        state.servers = await apiRequest("/servers");
        renderServers();
        await selectServer(server);
        form.reset();
        document.getElementById("server-dialog").close();
    } catch (error) {
        message.textContent = error.message;
    } finally {
        button.disabled = false;
    }
});

document.getElementById("create-channel-form").addEventListener("submit", async(event) => {
    event.preventDefault();
    if (!state.selectedServer) {
        return;
    }
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    const message = document.getElementById("create-channel-message");
    button.disabled = true;
    message.textContent = "";
    try {
        const channel = await apiRequest(
            `/servers/${state.selectedServer.id}/channels`, {
                method: "POST",
                body: JSON.stringify({ name: form.elements.name.value.trim() })
            }
        );
        const channels = await apiRequest(
            `/servers/${state.selectedServer.id}/channels`
        );
        state.channels = channels;
        renderChannels(channels);
        form.reset();
        document.getElementById("channel-dialog").close();
        await selectChannel(channel);
    } catch (error) {
        message.textContent = error.message;
    } finally {
        button.disabled = false;
    }
});

document.getElementById("logout-button").addEventListener("click", () => {
    disconnectWebSocket();
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    window.location.replace("index.html");
});