"use strict";
const $ = id => document.getElementById(id);
let csrf = "";
let tv = "";

async function updateStatus() {
  if (!tv) return;
  try {
    const response = await fetch(`/api/status/${encodeURIComponent(tv)}`, {cache:"no-store"});
    if (response.status === 401) { location.reload(); return; }
    if (!response.ok) throw new Error("Unavailable");
    const state = await response.json();
    $("guestDot").classList.toggle("offline", !state.connected);
    $("guestStatus").textContent = state.connected
      ? `Connected${state.volume?.level == null ? "" : ` · Volume ${state.volume.level}`}`
      : "TV is unavailable";
  } catch {
    $("guestDot").classList.add("offline");
    $("guestStatus").textContent = "Remote unavailable";
  }
}

async function sendKey(key) {
  if (!tv) return;
  try {
    const response = await fetch("/api/command", {
      method:"POST", headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},
      body:JSON.stringify({tv,type:"key",value:key})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Command failed");
    $("guestMessage").textContent = "";
    if (navigator.vibrate) navigator.vibrate(10);
    updateStatus();
  } catch (error) { $("guestMessage").textContent = error.message; }
}

async function init() {
  const response = await fetch("/api/bootstrap", {cache:"no-store"});
  if (!response.ok) { location.reload(); return; }
  const data = await response.json();
  if (!data.guest) { location.reload(); return; }
  csrf = data.csrf;
  for (const [id, config] of Object.entries(data.tvs)) {
    $("guestTv").add(new Option(config.name, id));
  }
  const saved = localStorage.getItem("guestSelectedTV");
  tv = saved && data.tvs[saved] ? saved : Object.keys(data.tvs)[0];
  $("guestTv").value = tv;
  $("guestTv").onchange = () => {
    tv = $("guestTv").value;
    localStorage.setItem("guestSelectedTV", tv);
    updateStatus();
  };
  document.querySelectorAll("[data-key]").forEach(button => {
    button.onclick = () => sendKey(button.dataset.key);
  });
  updateStatus();
  setInterval(updateStatus, 4000);
}
init();
