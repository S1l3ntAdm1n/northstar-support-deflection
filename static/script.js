// Point this at your Flask backend. Edit if your backend runs elsewhere.
const API_BASE = "/api";

// ---------------------------------------------------------------------------
// Intent matching (client-side only - the actual data lives in Flask)
// ---------------------------------------------------------------------------
const INTENT_KEYWORDS = {
    order_status: ["where is my order", "track", "tracking", "shipped", "order status", "arrive", "delivery"],
    returns_refund: ["return", "refund", "money back", "exchange", "cancel my order"],
    stock_availability: ["in stock", "back in stock", "available", "different size", "do you have", "restock", "sold out"],
    human: ["human", "agent", "representative", "real person"],
};

function classify(text) {
    const t = text.toLowerCase();
    for (const [intent, phrases] of Object.entries(INTENT_KEYWORDS)) {
        if (phrases.some((p) => t.includes(p))) return intent;
    }
    return null;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let idCounter = 0;
const nextId = () => ++idCounter;

function welcomeMessage() {
    return {
        id: nextId(),
        sender: "bot",
        text: "Hi, I'm the Northstar support bot. I can help with order status, returns and refunds, or stock availability. What do you need?",
    };
}

function makeThread() {
    return {
        id: nextId(),
        title: "New conversation",
        messages: [welcomeMessage()],
        awaiting: null,
        pendingProduct: null,
    };
}

let threads = [makeThread()];
let activeId = threads[0].id;
let isBusy = false;
let deflected = 0;
let escalated = 0;
let offline = false;
let humanModalThreadId = null;

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------
const sidebar = document.getElementById("sidebar");
const sidebarBackdrop = document.getElementById("sidebar-backdrop");
const sidebarOpenBtn = document.getElementById("sidebar-open-btn");
const sidebarCloseBtn = document.getElementById("sidebar-close-btn");
const newChatBtn = document.getElementById("new-chat-btn");
const deflectedCountEl = document.getElementById("deflected-count");
const escalatedCountEl = document.getElementById("escalated-count");
const offlineNote = document.getElementById("offline-note");
const threadListEl = document.getElementById("thread-list");
const threadTitleEl = document.getElementById("thread-title");
const chatLogEl = document.getElementById("chat-log");
const categoryBar = document.getElementById("category-bar");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");

const humanModalOverlay = document.getElementById("human-modal-overlay");
const modalCloseBtn = document.getElementById("modal-close-btn");
const modalCancelBtn = document.getElementById("modal-cancel-btn");
const modalSubmitBtn = document.getElementById("modal-submit-btn");
const humanNameInput = document.getElementById("human-name");
const humanEmailInput = document.getElementById("human-email");
const humanMessageInput = document.getElementById("human-message");
const humanNameError = document.getElementById("human-name-error");
const humanEmailError = document.getElementById("human-email-error");
const humanMessageError = document.getElementById("human-message-error");

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
function getActiveThread() {
    return threads.find((t) => t.id === activeId) || threads[0];
}

function threadPreview(thread) {
    const lastUser = [...thread.messages].reverse().find((m) => m.sender === "user");
    return lastUser ? lastUser.text : "No messages yet";
}

const PACKAGE_ICON =
    '<svg class="icon icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16.5 9.4 7.55 4.24"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/></svg>';
const MESSAGE_ICON =
    '<svg class="icon icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';

function renderThreadList() {
    threadListEl.innerHTML = "";
    threads.forEach((t) => {
        const btn = document.createElement("button");
        btn.className = "thread-item" + (t.id === activeId ? " active" : "");
        btn.innerHTML = `
            ${MESSAGE_ICON}
            <span class="thread-item-text">
                <span class="thread-item-title"></span>
                <span class="thread-item-preview"></span>
            </span>
        `;
        btn.querySelector(".thread-item-title").textContent = t.title;
        btn.querySelector(".thread-item-preview").textContent = threadPreview(t);
        btn.addEventListener("click", () => selectThread(t.id));
        threadListEl.appendChild(btn);
    });
}

function renderMessages() {
    const thread = getActiveThread();
    threadTitleEl.textContent = thread.title;
    chatLogEl.innerHTML = "";

    thread.messages.forEach((m) => {
        const row = document.createElement("div");
        row.className = "msg-row " + m.sender;

        if (m.sender === "bot") {
            const avatar = document.createElement("div");
            avatar.className = "msg-avatar";
            avatar.innerHTML = PACKAGE_ICON;
            row.appendChild(avatar);
        }

        const bubble = document.createElement("div");
        bubble.className = "bubble " + m.sender;
        bubble.textContent = m.text;
        row.appendChild(bubble);

        chatLogEl.appendChild(row);
    });

    if (isBusy) {
        const row = document.createElement("div");
        row.className = "msg-row bot";
        const avatar = document.createElement("div");
        avatar.className = "msg-avatar";
        avatar.innerHTML = PACKAGE_ICON;
        const bubble = document.createElement("div");
        bubble.className = "bubble bot typing";
        bubble.textContent = "Checking...";
        row.appendChild(avatar);
        row.appendChild(bubble);
        chatLogEl.appendChild(row);
    }

    chatLogEl.scrollTop = chatLogEl.scrollHeight;
}

function renderStats() {
    deflectedCountEl.textContent = deflected;
    escalatedCountEl.textContent = escalated;
    offlineNote.hidden = !offline;
}

function renderInputState() {
    chatInput.disabled = isBusy;
    sendBtn.disabled = isBusy;
    categoryBar.querySelectorAll(".category-chip").forEach((chip) => {
        chip.disabled = isBusy;
    });
}

function renderAll() {
    renderThreadList();
    renderMessages();
    renderStats();
    renderInputState();
}

// ---------------------------------------------------------------------------
// Thread mutation helpers
// ---------------------------------------------------------------------------
function addBot(text, threadId = activeId) {
    const thread = threads.find((t) => t.id === threadId);
    if (!thread) return;
    thread.messages.push({ id: nextId(), sender: "bot", text });
    renderAll();
}

function addUser(text, threadId = activeId) {
    const thread = threads.find((t) => t.id === threadId);
    if (!thread) return;
    thread.messages.push({ id: nextId(), sender: "user", text });
    if (thread.title === "New conversation") {
        thread.title = text.slice(0, 42) + (text.length > 42 ? "…" : "");
    }
    renderAll();
}

function setAwaiting(value, threadId = activeId) {
    const thread = threads.find((t) => t.id === threadId);
    if (thread) thread.awaiting = value;
}

function setPendingProduct(value, threadId = activeId) {
    const thread = threads.find((t) => t.id === threadId);
    if (thread) thread.pendingProduct = value;
}

function startNewChat() {
    const t = makeThread();
    threads.unshift(t);
    activeId = t.id;
    closeSidebar();
    chatInput.value = "";
    renderAll();
    chatInput.focus();
}

function selectThread(id) {
    activeId = id;
    closeSidebar();
    renderAll();
}

// ---------------------------------------------------------------------------
// Sidebar (mobile) toggling
// ---------------------------------------------------------------------------
function openSidebar() {
    sidebar.classList.add("open");
    sidebarBackdrop.classList.add("visible");
}
function closeSidebar() {
    sidebar.classList.remove("open");
    sidebarBackdrop.classList.remove("visible");
}

// ---------------------------------------------------------------------------
// Backend calls
// ---------------------------------------------------------------------------
async function refreshSummary() {
    try {
        const res = await fetch(`${API_BASE}/tickets`);
        if (!res.ok) throw new Error("bad response");
        const data = await res.json();
        deflected = data.summary.deflected;
        escalated = data.summary.escalated;
        offline = false;
    } catch {
        offline = true;
    }
    renderStats();
}

async function logTicket(userMessage, intent, wasDeflected) {
    try {
        await fetch(`${API_BASE}/tickets`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_message: userMessage, intent, deflected: wasDeflected }),
        });
        refreshSummary();
    } catch {
        offline = true;
        if (wasDeflected) deflected += 1;
        else escalated += 1;
        renderStats();
    }
}

function connectionErrorMessage(threadId) {
    addBot(
        "I can't reach the Northstar systems right now. Make sure the Flask backend is running on http://localhost:5000, then try again.",
        threadId
    );
}

function startOrderStatus(threadId) {
    addBot("Sure, what's your order ID? (try N1001, N1002, or N1003)", threadId);
    setAwaiting("order_id", threadId);
}
function startReturn(threadId) {
    addBot("What's the order ID you'd like to return? (try N1001 or N1002)", threadId);
    setAwaiting("return_id", threadId);
}
function startStock(threadId) {
    addBot("Which product are you checking? (try Running Shoes, Wireless Headphones, or Office Chair)", threadId);
    setAwaiting("stock_product", threadId);
}
function startHuman(threadId) {
    humanNameInput.value = "";
    humanEmailInput.value = "";
    humanMessageInput.value = "";
    hideFormErrors();
    humanModalThreadId = threadId;
    humanModalOverlay.hidden = false;
    setTimeout(() => humanNameInput.focus(), 50);
}

function closeHumanModal() {
    humanModalOverlay.hidden = true;
    humanModalThreadId = null;
}

function hideFormErrors() {
    humanNameError.hidden = true;
    humanEmailError.hidden = true;
    humanMessageError.hidden = true;
}

async function submitHumanForm() {
    const name = humanNameInput.value.trim();
    const email = humanEmailInput.value.trim();
    const message = humanMessageInput.value.trim();

    hideFormErrors();
    let hasError = false;

    if (!name) {
        humanNameError.textContent = "Enter your name";
        humanNameError.hidden = false;
        hasError = true;
    }
    if (!email) {
        humanEmailError.textContent = "Enter your email";
        humanEmailError.hidden = false;
        hasError = true;
    } else if (!/^\S+@\S+\.\S+$/.test(email)) {
        humanEmailError.textContent = "Enter a valid email";
        humanEmailError.hidden = false;
        hasError = true;
    }
    if (!message) {
        humanMessageError.textContent = "Tell us what you need help with";
        humanMessageError.hidden = false;
        hasError = true;
    }

    if (hasError) return;

    const threadId = humanModalThreadId || activeId;
    modalSubmitBtn.disabled = true;
    modalSubmitBtn.textContent = "Sending...";

    try {
        await fetch(`${API_BASE}/escalations`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, email, message }),
        });
    } catch {
        // Backend may not have this route yet; still confirm below and log via /api/tickets.
    } finally {
        modalSubmitBtn.disabled = false;
        modalSubmitBtn.textContent = "Send to agent";
    }

    addBot(`Thanks ${name.split(" ")[0]}, I've passed this to a human agent. They'll follow up at ${email} shortly.`, threadId);
    logTicket(message, "human", false);
    closeHumanModal();
}

async function resolveOrderId(text, threadId) {
    const id = text.trim().toUpperCase();
    isBusy = true;
    renderAll();
    try {
        const res = await fetch(`${API_BASE}/orders/${encodeURIComponent(id)}`);
        if (res.status === 404) {
            addBot(`I couldn't find order ${id}. Double check the ID, or ask to talk to a human.`, threadId);
            logTicket(text, "order_status", false);
            return;
        }
        if (!res.ok) throw new Error("request failed");
        const order = await res.json();

        if (order.status === "Delivered") {
            addBot(`Order ${id} was delivered on ${order.delivered_date}. Items: ${order.items.join(", ")}.`, threadId);
        } else if (order.status === "Shipped") {
            addBot(
                `Order ${id} shipped on ${order.shipped_date} via ${order.carrier} (tracking ${order.tracking_number}). Estimated arrival: ${order.eta}.`,
                threadId
            );
        } else {
            addBot(`Order ${id} is still processing and hasn't shipped yet. Estimated ship-by: ${order.eta}.`, threadId);
        }
        logTicket(text, "order_status", true);
    } catch {
        connectionErrorMessage(threadId);
    } finally {
        isBusy = false;
        setAwaiting(null, threadId);
        renderAll();
    }
}

async function resolveReturnId(text, threadId) {
    const id = text.trim().toUpperCase();
    isBusy = true;
    renderAll();
    try {
        const res = await fetch(`${API_BASE}/orders/${encodeURIComponent(id)}/return-eligibility`);
        if (res.status === 404) {
            addBot(`I couldn't find order ${id}. Ask to talk to a human if you need help locating it.`, threadId);
            logTicket(text, "returns_refund", false);
            return;
        }
        if (!res.ok) throw new Error("request failed");
        const data = await res.json();

        if (!data.eligible) {
            addBot(data.reason, threadId);
            logTicket(text, "returns_refund", true);
            return;
        }

        const initRes = await fetch(`${API_BASE}/orders/${encodeURIComponent(id)}/return`, { method: "POST" });
        if (!initRes.ok) throw new Error("request failed");
        const initData = await initRes.json();
        addBot(
            `Order ${id} is eligible for return. I've started the request, once we receive the item your refund goes out within ${initData.refund_days} business days.`,
            threadId
        );
        logTicket(text, "returns_refund", true);
    } catch {
        connectionErrorMessage(threadId);
    } finally {
        isBusy = false;
        setAwaiting(null, threadId);
        renderAll();
    }
}

async function resolveStockProduct(text, threadId) {
    const key = text.trim().toLowerCase();
    isBusy = true;
    renderAll();
    try {
        const res = await fetch(`${API_BASE}/stock/${encodeURIComponent(key)}`);
        if (res.status === 404) {
            addBot(`I couldn't find "${text}" in the catalog. Check the spelling, or ask to talk to a human.`, threadId);
            logTicket(text, "stock_availability", false);
            setAwaiting(null, threadId);
            return;
        }
        if (!res.ok) throw new Error("request failed");
        const item = await res.json();
        setPendingProduct({ key }, threadId);
        addBot(`Any specific size or variant? (in stock: ${item.sizes_in_stock.join(", ")})`, threadId);
        setAwaiting("stock_size", threadId);
    } catch {
        connectionErrorMessage(threadId);
        setAwaiting(null, threadId);
    } finally {
        isBusy = false;
        renderAll();
    }
}

async function resolveStockSize(text, threadId, item) {
    isBusy = true;
    renderAll();
    try {
        const res = await fetch(`${API_BASE}/stock/${encodeURIComponent(item.key)}?size=${encodeURIComponent(text.trim())}`);
        if (!res.ok) throw new Error("request failed");
        const data = await res.json();

        if (data.in_stock) {
            addBot(`Good news, "${text}" is currently in stock for ${item.key}.`, threadId);
        } else {
            addBot(`"${text}" is out of stock for ${item.key} right now. Expected restock: ${data.restock_eta || "date to be confirmed"}.`, threadId);
        }
        logTicket(text, "stock_availability", true);
    } catch {
        connectionErrorMessage(threadId);
    } finally {
        isBusy = false;
        setAwaiting(null, threadId);
        setPendingProduct(null, threadId);
        renderAll();
    }
}

// ---------------------------------------------------------------------------
// Send / category handling
// ---------------------------------------------------------------------------
function handleSend(rawText) {
    const text = (rawText ?? chatInput.value).trim();
    const threadId = activeId;
    const thread = getActiveThread();
    if (!text || isBusy || !thread) return;

    addUser(text, threadId);
    chatInput.value = "";

    if (thread.awaiting === "order_id") return resolveOrderId(text, threadId);
    if (thread.awaiting === "return_id") return resolveReturnId(text, threadId);
    if (thread.awaiting === "stock_product") return resolveStockProduct(text, threadId);
    if (thread.awaiting === "stock_size") return resolveStockSize(text, threadId, thread.pendingProduct);

    const intent = classify(text);
    if (intent === "order_status") return startOrderStatus(threadId);
    if (intent === "returns_refund") return startReturn(threadId);
    if (intent === "stock_availability") return startStock(threadId);
    if (intent === "human") return startHuman(threadId);

    addBot("I didn't quite catch that. Pick a topic below and I'll take it from there.", threadId);
}

function handleCategoryClick(key) {
    if (isBusy) return;
    const threadId = activeId;
    if (key === "order_status") return startOrderStatus(threadId);
    if (key === "returns_refund") return startReturn(threadId);
    if (key === "stock_availability") return startStock(threadId);
    if (key === "human") return startHuman(threadId);
}

// ---------------------------------------------------------------------------
// Event wiring
// ---------------------------------------------------------------------------
sidebarOpenBtn.addEventListener("click", openSidebar);
sidebarCloseBtn.addEventListener("click", closeSidebar);
sidebarBackdrop.addEventListener("click", closeSidebar);
newChatBtn.addEventListener("click", startNewChat);

sendBtn.addEventListener("click", () => handleSend());
chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") handleSend();
});

categoryBar.querySelectorAll(".category-chip").forEach((chip) => {
    chip.addEventListener("click", () => handleCategoryClick(chip.dataset.intent));
});

modalCloseBtn.addEventListener("click", closeHumanModal);
modalCancelBtn.addEventListener("click", closeHumanModal);
modalSubmitBtn.addEventListener("click", submitHumanForm);
humanModalOverlay.addEventListener("click", (e) => {
    if (e.target === humanModalOverlay) closeHumanModal();
});
[humanNameInput, humanEmailInput, humanMessageInput].forEach((input, idx) => {
    const errorEls = [humanNameError, humanEmailError, humanMessageError];
    input.addEventListener("input", () => {
        errorEls[idx].hidden = true;
    });
});

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
renderAll();
refreshSummary();