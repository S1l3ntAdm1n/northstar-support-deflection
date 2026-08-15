// Point this at your Flask backend. Change if your server runs elsewhere.
const CHAT_URL = "/chat";
const API_BASE  = "/api";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let idCounter = 0;
const nextId = () => ++idCounter;

function welcomeMessage() {
    return {
        id: nextId(),
        sender: "bot",
        text: "Hi! I'm the Northstar support assistant. I can track orders, check stock availability, or connect you with a human agent. What do you need?",
    };
}

function makeThread() {
    return {
        id:         nextId(),
        title:      "New conversation",
        messages:   [welcomeMessage()],
        escalated:  false,
    };
}

let threads        = [makeThread()];
let activeId       = threads[0].id;
let isBusy         = false;
let deflectedCount = 0;
let escalatedCount = 0;
let offline        = false;

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------
const sidebar          = document.getElementById("sidebar");
const sidebarBackdrop  = document.getElementById("sidebar-backdrop");
const sidebarOpenBtn   = document.getElementById("sidebar-open-btn");
const sidebarCloseBtn  = document.getElementById("sidebar-close-btn");
const newChatBtn       = document.getElementById("new-chat-btn");
const deflectedCountEl = document.getElementById("deflected-count");
const escalatedCountEl = document.getElementById("escalated-count");
const offlineNote      = document.getElementById("offline-note");
const threadListEl     = document.getElementById("thread-list");
const threadTitleEl    = document.getElementById("thread-title");
const chatLogEl        = document.getElementById("chat-log");
const categoryBar      = document.getElementById("category-bar");
const chatInput        = document.getElementById("chat-input");
const sendBtn          = document.getElementById("send-btn");

const humanModalOverlay  = document.getElementById("human-modal-overlay");
const modalCloseBtn      = document.getElementById("modal-close-btn");
const modalCancelBtn     = document.getElementById("modal-cancel-btn");
const modalSubmitBtn     = document.getElementById("modal-submit-btn");
const humanNameInput     = document.getElementById("human-name");
const humanEmailInput    = document.getElementById("human-email");
const humanMessageInput  = document.getElementById("human-message");
const humanNameError     = document.getElementById("human-name-error");
const humanEmailError    = document.getElementById("human-email-error");
const humanMessageError  = document.getElementById("human-message-error");

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------
const PACKAGE_ICON =
    '<svg class="icon icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16.5 9.4 7.55 4.24"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/></svg>';
const MESSAGE_ICON =
    '<svg class="icon icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';

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
        btn.querySelector(".thread-item-title").textContent   = t.title;
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
        // --- Bot / user message ---
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

        // --- Suggest-ticket button after bot message ---
        if (m.sender === "bot" && m.suggestTicket) {
            const suggestRow = document.createElement("div");
            suggestRow.className = "msg-row bot";
            // Spacer so button aligns with bubble (not avatar)
            const spacer = document.createElement("div");
            spacer.style.width = "40px";
            spacer.style.flexShrink = "0";
            suggestRow.appendChild(spacer);
            const btn = document.createElement("button");
            btn.className = "suggest-btn";
            btn.innerHTML = `
                <svg viewBox="0 0 24 24" style="width:13px;height:13px;flex-shrink:0" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14"/><path d="M12 5v14"/></svg>
                Open support ticket
            `;
            btn.addEventListener("click", () => openHumanModal(m.prefillOrderId || ""));
            suggestRow.appendChild(btn);
            chatLogEl.appendChild(suggestRow);
        }
    });

    // --- Typing indicator ---
    if (isBusy) {
        const row = document.createElement("div");
        row.className = "msg-row bot";
        const avatar = document.createElement("div");
        avatar.className = "msg-avatar";
        avatar.innerHTML = PACKAGE_ICON;
        const bubble = document.createElement("div");
        bubble.className = "bubble bot typing";
        bubble.textContent = "Checking…";
        row.appendChild(avatar);
        row.appendChild(bubble);
        chatLogEl.appendChild(row);
    }

    chatLogEl.scrollTop = chatLogEl.scrollHeight;
}

function renderStats() {
    deflectedCountEl.textContent = deflectedCount;
    escalatedCountEl.textContent = escalatedCount;
    offlineNote.hidden = !offline;
}

function renderInputState() {
    chatInput.disabled = isBusy;
    sendBtn.disabled   = isBusy;
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
// Thread helpers
// ---------------------------------------------------------------------------
function addBot(text, opts = {}, threadId = activeId) {
    const thread = threads.find((t) => t.id === threadId);
    if (!thread) return;
    thread.messages.push({
        id:            nextId(),
        sender:        "bot",
        text,
        suggestTicket: opts.suggestTicket  || false,
        prefillOrderId: opts.prefillOrderId || "",
    });
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

function selectThread(id) {
    activeId = id;
    closeSidebar();
    renderAll();
}

// ---------------------------------------------------------------------------
// Sidebar (mobile) toggling
// ---------------------------------------------------------------------------
function openSidebar()  { sidebar.classList.add("open");    sidebarBackdrop.classList.add("visible"); }
function closeSidebar() { sidebar.classList.remove("open"); sidebarBackdrop.classList.remove("visible"); }

// ---------------------------------------------------------------------------
// Human-agent modal
// ---------------------------------------------------------------------------
function hideFormErrors() {
    humanNameError.hidden    = true;
    humanEmailError.hidden   = true;
    humanMessageError.hidden = true;
}

function openHumanModal(prefillOrderId = "") {
    humanNameInput.value    = "";
    humanEmailInput.value   = "";
    humanMessageInput.value = prefillOrderId ? `Order #${prefillOrderId}: ` : "";
    hideFormErrors();
    humanModalOverlay.hidden = false;
    setTimeout(() => humanNameInput.focus(), 50);
}

function closeHumanModal() {
    humanModalOverlay.hidden = true;
}

// If the modal is already open, pulse it rather than re-opening.
function triggerModal(prefillOrderId = "") {
    if (!humanModalOverlay.hidden) {
        const card = humanModalOverlay.querySelector(".modal-card");
        card.style.outline = "2px solid #f59e0b";
        setTimeout(() => { card.style.outline = ""; }, 1200);
        return;
    }
    openHumanModal(prefillOrderId);
}

async function submitHumanForm() {
    const name    = humanNameInput.value.trim();
    const email   = humanEmailInput.value.trim();
    const message = humanMessageInput.value.trim();

    hideFormErrors();
    let hasError = false;
    if (!name)  { humanNameError.textContent    = "Enter your name";           humanNameError.hidden    = false; hasError = true; }
    if (!email) { humanEmailError.textContent   = "Enter your email";          humanEmailError.hidden   = false; hasError = true; }
    else if (!/^\S+@\S+\.\S+$/.test(email)) {
                  humanEmailError.textContent   = "Enter a valid email";       humanEmailError.hidden   = false; hasError = true; }
    if (!message){ humanMessageError.textContent = "Tell us what you need help with"; humanMessageError.hidden = false; hasError = true; }
    if (hasError) return;

    modalSubmitBtn.disabled    = true;
    modalSubmitBtn.textContent = "Sending…";

    try {
        await fetch(`${API_BASE}/ticket`, {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ customer_name: name, customer_email: email, issue_description: message }),
        });
    } catch { /* best effort */ }

    modalSubmitBtn.disabled    = false;
    modalSubmitBtn.textContent = "Send to agent";

    addBot(`Got it, ${name.split(" ")[0]} — I've passed your details to the team. They'll follow up at ${email} shortly.`);
    closeHumanModal();
    escalatedCount++;
    renderStats();
}

// ---------------------------------------------------------------------------
// Stats helpers
// ---------------------------------------------------------------------------
async function refreshSummary() {
    try {
        const res = await fetch(`${API_BASE}/tickets`);
        if (!res.ok) throw new Error("bad response");
        const data = await res.json();
        deflectedCount = data.summary.deflected;
        escalatedCount = data.summary.escalated;
        offline = false;
    } catch {
        offline = true;
    }
    renderStats();
}

// ---------------------------------------------------------------------------
// Main chat call — all messages route through here
// ---------------------------------------------------------------------------
async function sendChat(text, threadId) {
    isBusy = true;
    renderAll();

    try {
        const res = await fetch(CHAT_URL, {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ message: text }),
        });
        if (!res.ok) throw new Error("request failed");
        const data = await res.json();

        // Add bot reply
        addBot(
            data.response,
            { suggestTicket: data.suggest_ticket, prefillOrderId: data.prefilled_order_id },
            threadId,
        );

        // Escalation — open (or pulse) the modal
        if (data.show_ticket_form) {
            const thread = threads.find((t) => t.id === threadId);
            if (thread && !thread.escalated) {
                thread.escalated = true;
                escalatedCount++;
                renderStats();
            }
            triggerModal(data.prefilled_order_id || "");
        }

        offline = false;
    } catch {
        addBot(
            "I can't reach the Northstar systems right now. Make sure the server is running and try again.",
            {},
            threadId,
        );
        offline = true;
        renderStats();
    } finally {
        isBusy = false;
        renderAll();
    }
}

// ---------------------------------------------------------------------------
// Send / category chip handling
// ---------------------------------------------------------------------------
function handleSend(rawText) {
    const text     = (rawText ?? chatInput.value).trim();
    const threadId = activeId;
    const thread   = getActiveThread();
    if (!text || isBusy || !thread) return;

    addUser(text, threadId);
    chatInput.value = "";
    sendChat(text, threadId);
}

const CHIP_TEXT = {
    order_status:       "Where is my order?",
    returns_refund:     "I need a refund",
    stock_availability: "What do you have in stock?",
    human:              "I need to talk to a real person",
};

function handleCategoryClick(key) {
    if (isBusy) return;
    const text = CHIP_TEXT[key];
    if (text) handleSend(text);
}

// ---------------------------------------------------------------------------
// New chat — reset Flask session so chatbot starts fresh
// ---------------------------------------------------------------------------
async function startNewChat() {
    try { await fetch(`${API_BASE}/session/reset`, { method: "POST" }); } catch {}
    const t = makeThread();
    threads.unshift(t);
    activeId = t.id;
    closeSidebar();
    chatInput.value = "";
    renderAll();
    chatInput.focus();
}

// ---------------------------------------------------------------------------
// Event wiring
// ---------------------------------------------------------------------------
sidebarOpenBtn.addEventListener("click",  openSidebar);
sidebarCloseBtn.addEventListener("click", closeSidebar);
sidebarBackdrop.addEventListener("click", closeSidebar);
newChatBtn.addEventListener("click",      startNewChat);

sendBtn.addEventListener("click", () => handleSend());
chatInput.addEventListener("keydown", (e) => { if (e.key === "Enter") handleSend(); });

categoryBar.querySelectorAll(".category-chip").forEach((chip) => {
    chip.addEventListener("click", () => handleCategoryClick(chip.dataset.intent));
});

modalCloseBtn.addEventListener("click",  closeHumanModal);
modalCancelBtn.addEventListener("click", closeHumanModal);
modalSubmitBtn.addEventListener("click", submitHumanForm);
humanModalOverlay.addEventListener("click", (e) => { if (e.target === humanModalOverlay) closeHumanModal(); });

[humanNameInput, humanEmailInput, humanMessageInput].forEach((input, idx) => {
    const errors = [humanNameError, humanEmailError, humanMessageError];
    input.addEventListener("input", () => { errors[idx].hidden = true; });
});

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
renderAll();
refreshSummary();