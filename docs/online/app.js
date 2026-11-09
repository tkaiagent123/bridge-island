// Page shell: shows the app in a frame, turns clicks and form submits into requests to the
// Python worker, and keeps browser Back working.
const frame = document.getElementById("frame");
const statusEl = document.getElementById("status");
const veil = document.getElementById("veil");
const worker = new Worker("worker.js");
const pending = new Map();
const pages = new Map();
let nextId = 1;
let current = 0;

function decode(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function send(method, target, body = "") {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, method, target, body });
  });
}

function show(html, pushState) {
  const id = ++current;
  pages.set(id, html);
  if (pushState) history.pushState({ id }, "");
  else history.replaceState({ id }, "");
  frame.srcdoc = html;
}

function download(res) {
  const name = /filename="([^"]+)"/.exec(res.disposition)?.[1] || "download";
  const url = URL.createObjectURL(new Blob([decode(res.data)], { type: res.contentType }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

async function go(method, target, body, pushState = true) {
  veil.hidden = false;
  document.getElementById("veil-text").textContent =
    method === "POST" ? "Running the simulation..." : "Loading...";
  try {
    const res = await send(method, target, body);
    if (res.disposition) download(res);
    else show(new TextDecoder().decode(decode(res.data)), pushState);
  } catch (err) {
    const msg = String(err).replace(/</g, "&lt;");
    show(`<p style="font:16px system-ui;padding:1rem">Something went wrong: ${msg}</p>`, pushState);
  } finally {
    veil.hidden = true;
  }
}

frame.addEventListener("load", () => {
  const doc = frame.contentDocument;
  if (!doc || !doc.body) return;
  if (doc.title) document.title = doc.title;
  doc.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a) return;
    const href = a.getAttribute("href");
    if (!href.startsWith("/")) return; // the '#main' skip link keeps its default behaviour
    e.preventDefault();
    go("GET", href, "");
  });
  doc.addEventListener("submit", (e) => {
    e.preventDefault();
    const form = e.target;
    const body = new URLSearchParams(new FormData(form, e.submitter)).toString();
    go("POST", form.getAttribute("action") || "/", body);
  });
  // #selftest (used by the build check): submit the home form once, so the page title proves a full round trip
  const f = location.hash === "#selftest" && !window.selfTested && doc.querySelector("form.steps");
  if (f) {
    window.selfTested = true;
    f.requestSubmit();
  }
});

window.addEventListener("popstate", (e) => {
  const html = e.state && pages.get(e.state.id);
  if (html) frame.srcdoc = html;
});

worker.onmessage = (event) => {
  const m = event.data;
  if (m.type === "status") {
    statusEl.textContent = m.text;
    document.getElementById("veil-text").textContent = m.text;
  } else if (m.type === "ready") {
    statusEl.textContent = "";
    go("GET", "/", "", false);
  } else if (m.type === "fatal") {
    veil.hidden = false;
    document.getElementById("veil-text").textContent =
      "The app could not start in this browser (" + m.text +
      "). You can still run it on your own computer: see the documentation.";
  } else {
    const p = pending.get(m.id);
    pending.delete(m.id);
    if (!p) return;
    if (m.type === "response") p.resolve(m);
    else p.reject(new Error(m.text));
  }
};
