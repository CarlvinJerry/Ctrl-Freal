
"use strict";

let csrf = "";
let tv = "tcl";
let apps = {};
let commandBusy = false;
let toastTimer;
let statusTimer;
let lastDirection = 0;
let tvTypes = {};
let pairToken = null;
let pairPoll = null;
let tvNames = {};
let prefDraft = null;
let prefWriteQueue = Promise.resolve();
let jackCandidate = null;
let jackTracked = [];
let jackJobs = [];
let jackCompletedHistory = [];
let jackResultsRaw = [];
let jackSearchSerial = 0;
const specialApps = {magicmirror:{name:"MagicMirror", package:"magicmirror"}};

const $ = id => document.getElementById(id);

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.style.display = "block";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.style.display = "none", 2500);
}

function localPreferences() {
  const favorites = {};
  for (const id of Object.keys(tvNames)) {
    try {
      const saved = JSON.parse(localStorage.getItem(`favorites:${id}`) || "[]");
      favorites[id] = Array.isArray(saved) ? saved : [];
    } catch { favorites[id] = []; }
  }
  return {theme:localStorage.getItem("theme") || "dark-glass",
          accent:localStorage.getItem("accentColor") || "blue",
          haptics:localStorage.getItem("haptics") !== "false",
          favorites, scenes:[]};
}

function applySharedPreferences(prefs) {
  prefDraft = prefs;
  localStorage.setItem("theme", prefs.theme);
  localStorage.setItem("accentColor", prefs.accent);
  localStorage.setItem("haptics", String(prefs.haptics));
  for (const id of Object.keys(tvNames)) {
    localStorage.setItem(`favorites:${id}`, JSON.stringify(prefs.favorites?.[id] || []));
  }
}

async function preferencesRequest(preferences, onlyIfMissing = false) {
  const response = await fetch("/api/preferences", {
    method:"PUT", headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},
    body:JSON.stringify({preferences, only_if_missing:onlyIfMissing})
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Could not save preferences");
  return data.preferences;
}

function queuePreferencesSave() {
  const snapshot = JSON.parse(JSON.stringify(prefDraft));
  prefWriteQueue = prefWriteQueue.catch(() => {}).then(() => preferencesRequest(snapshot));
  prefWriteQueue.catch(() => toast("Preferences could not sync to the Pi"));
  return prefWriteQueue;
}

function haptic() {
  if ($("haptics").checked && navigator.vibrate) {
    navigator.vibrate(12);
  }
}

async function command(type, value) {
  // Serialize commands to preserve ordering.
  while (commandBusy) {
    await new Promise(resolve => setTimeout(resolve, 15));
  }

  commandBusy = true;
  haptic();

  try {
    const response = await fetch("/api/command", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf
      },
      body: JSON.stringify({tv, type, value})
    });

    const result = await response.json();

    if (response.status === 401) {
      location.reload();
      return;
    }

    if (!response.ok) {
  if (response.status === 501) {
    toast("This feature is not supported by the TV connection");
  } else if (response.status === 503) {
    toast("TV unavailable. Check power or Wi-Fi.");
  } else {
    toast(result.error || "Command failed");
  }
}
  } catch (error) {
    toast("Cannot reach Raspberry Pi");
  } finally {
    commandBusy = false;
  }
}

function key(name) {
  return command("key", name);
}

function launchApp(id) {
  return id === "magicmirror" ? command("magicmirror", null) : command("app", id);
}

async function pairingPost(path, data) {
  const response = await fetch(path, {method:"POST", headers:{"Content-Type":"application/json","X-CSRF-Token":csrf}, body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Pairing failed");
  return result;
}

function pairInstructions() {
  const lg = $("pairType").value === "webos";
  $("pairInstructions").textContent = lg
    ? "LG LJ570U / webOS 3.5: turn on the TV and enable Settings → All Settings → Network → LG Connect Apps. Other LG models may call this Mobile TV access. Start pairing, then accept the TV's request with its physical remote. No code is needed."
    : "Android TV / Google TV: after Start pairing, read the code shown on the TV screen and enter it here. The TV must have Android TV Remote Service enabled.";
}

function installPairing() {
  const dialog = $("pairDialog");
  $("addTv").onclick = () => { $("pairSetup").hidden=false; $("pairProgress").hidden=true; $("pairMessage").textContent=""; dialog.showModal(); };
  $("closePair").onclick = () => dialog.close();
  dialog.addEventListener("close", () => { clearInterval(pairPoll); pairPoll=null; });
  $("pairType").onchange = pairInstructions;
  pairInstructions();
  $("scanTvs").onclick = async () => {
    const found = $("foundTvs");
    found.textContent = "Scanning for Android and LG TVs from the Raspberry Pi…";
    try {
      const response = await fetch("/api/tvs/discover", {cache:"no-store"});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Scan failed");
      found.replaceChildren();
      if (!data.devices.length) { found.textContent = "No TVs found. Choose a TV type and enter its IP address below."; return; }
      for (const device of data.devices) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = `${device.name} · ${device.host}`;
        button.onclick = () => { $("pairType").value=device.type; $("pairHost").value=device.host; $("pairName").value=device.name; pairInstructions(); };
        found.appendChild(button);
      }
    } catch (error) { found.textContent = error.message; }
  };
  $("beginPair").onclick = async () => {
    const name = $("pairName").value.trim();
    const id = name.toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"").slice(0,30);
    $("pairMessage").textContent = "Contacting TV…";
    try {
      const result = await pairingPost("/api/tvs/pair/start", {type:$("pairType").value, host:$("pairHost").value.trim(), name, id});
      pairToken = result.token;
      $("pairSetup").hidden = true;
      $("pairProgress").hidden = false;
      $("pairCodeLabel").hidden = result.step !== "code";
      $("pairStepText").textContent = result.step === "code"
        ? "A pairing code should now be visible on the Android TV. Enter it below, then tap Finish pairing."
        : "A request should now appear on the LG TV. Accept it using the TV remote, then tap Finish pairing.";
      $("pairMessage").textContent = "";
      if (result.step === "approve") {
        pairPoll = setInterval(async () => {
          try {
            const response = await fetch(`/api/tvs/pair/${encodeURIComponent(pairToken)}`, {cache:"no-store"});
            const state = await response.json();
            if (state.state === "paired") { clearInterval(pairPoll); pairPoll=null; $("pairMessage").textContent="LG approved. Tap Finish pairing."; }
            else if (state.state === "error") { clearInterval(pairPoll); pairPoll=null; $("pairMessage").textContent="LG pairing failed or timed out. Close this window and try again."; }
          } catch { /* The Finish button reports a concrete error. */ }
        }, 2000);
      }
    } catch (error) { $("pairMessage").textContent = error.message; }
  };
  $("finishPair").onclick = async () => {
    $("pairMessage").textContent = "Saving TV…";
    try {
      const result = await pairingPost("/api/tvs/pair/finish", {token:pairToken, code:$("pairCode").value.trim()});
      const option = document.createElement("option");
      option.value = result.tv_id;
      option.textContent = result.name;
      $("tvSelect").appendChild(option);
      $("tvSelect").value = result.tv_id;
      tvTypes[result.tv_id] = $("pairType").value;
      $("tvSelect").onchange();
      dialog.close();
      toast("TV paired and selected");
    } catch (error) { $("pairMessage").textContent = error.message; }
  };
}

function friendlyAppName(packageName) {
  if (!packageName) return "—";

  const match = Object.values(apps).find(
    app => app.package === packageName
  );

  return match ? match.name : packageName;
}

async function updateStatus() {
  const selectedTV = tv;
  try {
    const response = await fetch(
      `/api/status/${encodeURIComponent(tv)}`,
      {cache: "no-store"}
    );

    if (!response.ok) throw new Error("Status unavailable");
    const data = await response.json();
    if (selectedTV !== tv) return;

    const dot = $("dot");
    dot.className = "dot " + (
      !data.connected ? "offline" :
      data.power === false ? "standby" :
      "online"
     );

        let connectionLabel;

    if (data.connected) {
      if (data.power === false) {
        connectionLabel = "Standby · TV screen off";
      } else if (data.current_app) {
        connectionLabel = "Connected · " + friendlyAppName(data.current_app);
      } else {
        connectionLabel = "Connected · Ready";
      }
    } else {
      connectionLabel = "Reconnecting · Waiting for TV";
    }

    $("connectionText").textContent = connectionLabel;

    $("latency").textContent = data.latency_ms == null
      ? ""
      : `${data.latency_ms} ms`;
        // Live TV information
    $("tvPower").textContent = !data.connected
      ? "Unavailable"
      : data.power === true
        ? "On"
        : data.power === false
          ? "Standby"
          : "Unknown";

    const volume = data.volume;

    if (!data.connected) {
      $("tvVolume").textContent = "Unavailable";
    } else if (volume && volume.muted === true) {
      $("tvVolume").textContent = "Muted";
    } else if (volume && volume.level != null) {
      $("tvVolume").textContent = volume.maximum != null
        ? `${volume.level} / ${volume.maximum}`
        : String(volume.level);
    } else {
      $("tvVolume").textContent = "Unknown";
    }

    $("tvCurrentApp").textContent =
      !data.connected ? "Unavailable" :
      data.power === false ? "Standby" :
      data.current_app ? friendlyAppName(data.current_app) : "Unknown";

    $("diagnostics").textContent = JSON.stringify(data, null, 2);
    } catch {
    $("dot").className = "dot offline";
    $("connectionText").textContent = "Pi unavailable";
    $("latency").textContent = "";

    $("tvPower").textContent = "Unavailable";
    $("tvVolume").textContent = "Unavailable";
    $("tvCurrentApp").textContent = "Unavailable";
  }
}

function selectTab(name) {
  document.querySelectorAll("[data-tab]").forEach(button => {
    button.classList.toggle(
      "selected", button.dataset.tab === name
    );
  });

  document.querySelectorAll(".panel").forEach(panel => {
    panel.classList.toggle("active", panel.id === name);
  });
}

async function jackPost(path, payload) {
  const response = await fetch(path, {method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},body:JSON.stringify(payload)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Jack Sparrow request failed");
  return data;
}

function jackButton(label, onclick) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.onclick = onclick;
  return button;
}

function jackCard(title, detail) {
  const card = document.createElement("div");
  card.className = "jackCard";
  const heading = document.createElement("strong");
  heading.textContent = title;
  const small = document.createElement("small");
  small.textContent = detail;
  card.append(heading, small);
  return card;
}

function jackReview(candidate) {
  jackCandidate = candidate;
  $("jackDialogTitle").textContent = candidate.title;
  $("jackDialogSource").textContent = candidate.source === "archive" ? "Internet Archive catalog" : candidate.source === "feed" ? "Saved torrent feed" : candidate.source === "indexer" ? candidate.indexer_name : "Your magnet link";
  $("jackDownloadQuality").value = ["480p","720p","1080p","2160p"].includes(candidate.quality) ? candidate.quality : "any";
  $("jackDownloadType").value = candidate.category || (/S\d{1,2}E\d{1,2}/i.test(candidate.title) ? "series" : "movie");
  $("jackDialog").showModal();
}

function jackCompleted(item) {
  const ref = item.id || item.url;
  return [...jackJobs.filter(job => job.imported && !job.deleted), ...jackCompletedHistory].find(job =>
    (job.source_ref && ref ? job.source_ref === ref : job.title.toLowerCase() === item.title.toLowerCase()));
}

function jackRenderResults() {
  const type = $("jackType").value;
  const order = $("jackOrder").value;
  const results = jackResultsRaw.filter(item => type === "all" || item.category === type);
  results.sort((a, b) => {
    const completedA = Boolean(jackCompleted(a));
    const completedB = Boolean(jackCompleted(b));
    if (order === "completed" && completedA !== completedB) return completedA ? -1 : 1;
    const englishA = a.language === "english";
    const englishB = b.language === "english";
    if (englishA !== englishB) return englishA ? -1 : 1;
    if (order === "title") return a.title.localeCompare(b.title);
    if (order === "seeders" || order === "english") return (b.seeders || 0) - (a.seeders || 0);
    return (b.date || "").localeCompare(a.date || "");
  });
  $("jackResults").replaceChildren();
  for (const item of results) {
    const tracked = jackTracked.some(t => item.title.toLowerCase().includes(t.title.toLowerCase()));
    const completed = jackCompleted(item);
    const detail = `${completed ? "Downloaded · " : ""}${tracked ? "★ Tracked · " : ""}${item.indexer_name || (item.source === "archive" ? "Archive" : "Feed")} · ${item.category || "other"} · ${item.quality} · ${item.language === "english" ? "English" : item.language === "other" ? "Other language" : "Language unlisted"}${item.seeders !== null ? " · " + item.seeders + " seeders" : ""}${item.date ? " · " + item.date : ""}`;
    const card = jackCard(item.title, detail);
    if (completed) {
      card.classList.add("downloaded");
      if (jackJobs.some(job => job.imported && (job.source_ref || job.title) === (item.id || item.url || item.title))) {
        card.append(jackButton("View download", () => $("jackJobs").scrollIntoView({behavior:"smooth",block:"start"})));
      }
    } else {
      card.append(jackButton("Review", () => jackReview(item)));
    }
    $("jackResults").append(card);
  }
  $("jackMessage").textContent = `${results.length} results shown`;
}

async function jackSearch() {
  const serial = ++jackSearchSerial;
  const query = $("jackQuery").value.trim();
  const params = new URLSearchParams({q:query,quality:$("jackQuality").value,source:$("jackSource").value});
  $("jackMessage").textContent = "Searching sources…";
  $("jackResults").replaceChildren();
  try {
    const response = await fetch(`/api/jack/search?${params}`, {cache:"no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Search unavailable");
    if (serial !== jackSearchSerial) return;
    jackResultsRaw = data.results;
    jackRenderResults();
    if (data.errors.length) $("jackMessage").textContent += " · " + data.errors.join("; ");
  } catch (error) { $("jackMessage").textContent = error.message; }
}

async function jackRefresh() {
  try {
    const response = await fetch("/api/jack", {cache:"no-store"});
    if (!response.ok) return;
    const data = await response.json();
    jackTracked = data.tracked;
    jackJobs = data.jobs;
    jackCompletedHistory = data.completed || [];
    const source = $("jackSource");
    if (!source.dataset.indexersLoaded) {
      Object.entries(data.indexers || {}).forEach(([id, name]) => source.add(new Option(name, `indexer:${id}`)));
      source.dataset.indexersLoaded = "1";
    }
    if (jackResultsRaw.length) jackRenderResults();
    $("jackClientStatus").textContent = data.client_available ? "Torrent client ready" : "Torrent client unavailable on the Pi";
    $("jackTracked").replaceChildren();
    data.tracked.forEach(item => {
      const card = jackCard(item.title, item.category);
      card.append(jackButton("Stop tracking", async () => {
        try { await jackPost("/api/jack/untrack", item); await jackRefresh(); }
        catch (error) { toast(error.message); }
      }));
      $("jackTracked").append(card);
    });
    $("jackMatches").replaceChildren();
    $("jackNotices").replaceChildren();
    (data.notices || []).forEach(item => {
      const card = jackCard(item.title, item.detail);
      const actions = document.createElement("div");
      actions.className = "jackActions";
      actions.append(jackButton("Search", () => { $("jackQuery").value = item.query; jackSearch(); $("jackResults").scrollIntoView({behavior:"smooth"}); }),
        jackButton("Dismiss", async () => { try { await jackPost("/api/jack/notice/dismiss", {key:item.key}); await jackRefresh(); } catch (error) { toast(error.message); } }));
      card.append(actions);
      $("jackNotices").append(card);
    });
    data.matches.filter(item => !item.seen).forEach(item => {
      const card = jackCard(item.title, `New match for ${item.tracked_title} · ${item.quality}`);
      const actions = document.createElement("div");
      actions.className = "jackActions";
      async function seen() {
        try { await jackPost("/api/jack/match-seen", {key:item.key}); await jackRefresh(); }
        catch (error) { toast(error.message); }
      }
      actions.append(jackButton("Review", () => { jackReview(item); seen(); }), jackButton("Dismiss", seen));
      card.append(actions);
      $("jackMatches").append(card);
      const noticeKey = `jackMatch:${item.key}`;
      if (!localStorage.getItem(noticeKey)) {
        localStorage.setItem(noticeKey, "1");
        toast(`New match for ${item.tracked_title}`);
      }
    });
    $("jackFeeds").replaceChildren();
    data.feeds.forEach(url => {
      const card = jackCard(url, "Saved feed");
      card.append(jackButton("Remove feed", async () => {
        try { await jackPost("/api/jack/remove-feed", {url}); await jackRefresh(); }
        catch (error) { toast(error.message); }
      }));
      $("jackFeeds").append(card);
    });
    $("jackJobs").replaceChildren();
    data.jobs.forEach(job => {
      const done = job.imported;
      const inPlex = ["movie", "series"].includes(job.category);
      const detail = done ? `${inPlex ? "Saved for Plex scan" : "Saved to media folder; add a Plex library"} · ${job.category}` : `${Math.round(job.progress * 100)}% · ${job.peers} peers · ${Math.round(job.rate / 1024)} KB/s${job.error ? " · " + job.error : ""}`;
      const card = jackCard(job.title, detail);
      const actions = document.createElement("div");
      actions.className = "jackActions";
      async function manage(action) {
        try { await jackPost("/api/jack/manage", {id:job.id, action}); await jackRefresh(); }
        catch (error) { toast(error.message); }
      }
      if (!done) actions.append(jackButton(job.status === 0 ? "Resume" : "Pause", () => manage(job.status === 0 ? "resume" : "pause")));
      actions.append(jackButton("Remove", () => manage("remove")));
      if (done && job.category === "movie" && !job.deleted && job.imported_paths?.length) {
        actions.append(jackButton("Delete movie", async () => {
          if (!confirm(`Delete ${job.title} from the Plex movie library? This removes ${job.imported_paths.length} file(s).`)) return;
          try { await jackPost("/api/jack/movie/delete", {source_ref:job.source_ref}); await jackRefresh(); toast("Movie removed from Plex library"); }
          catch (error) { toast(error.message); }
        }));
      }
      card.append(actions);
      $("jackJobs").append(card);
      const noticeKey = `jackCompleted:${job.id}`;
      if (done && !localStorage.getItem(noticeKey)) {
        localStorage.setItem(noticeKey, "1");
        toast(inPlex ? `${job.title} finished; Plex will scan it` : `${job.title} is saved; add a Plex library`);
      }
    });
    jackCompletedHistory.filter(item => item.category === "movie" && item.imported_paths?.length &&
      !data.jobs.some(job => job.source_ref === item.source_ref)).forEach(item => {
      const card = jackCard(item.title, "Saved for Plex scan · movie");
      card.append(jackButton("Delete movie", async () => {
        if (!confirm(`Delete ${item.title} from the Plex movie library? This removes ${item.imported_paths.length} file(s).`)) return;
        try { await jackPost("/api/jack/movie/delete", {source_ref:item.source_ref}); await jackRefresh(); toast("Movie removed from Plex library"); }
        catch (error) { toast(error.message); }
      }));
      $("jackJobs").append(card);
    });
  } catch (error) { console.debug("Jack Sparrow status unavailable", error); }
}

function installJack() {
  $("jackSearchButton").onclick = jackSearch;
  $("jackQuery").onkeydown = event => { if (event.key === "Enter") jackSearch(); };
  $("jackSource").onchange = jackSearch;
  $("jackQuality").onchange = jackSearch;
  $("jackType").onchange = jackRenderResults;
  $("jackOrder").onchange = jackRenderResults;
  $("jackFeedButton").onclick = async () => {
    try { await jackPost("/api/jack/feed", {url:$("jackFeedUrl").value.trim()}); $("jackFeedUrl").value = ""; await jackRefresh(); toast("Feed added"); }
    catch (error) { toast(error.message); }
  };
  $("jackTrackButton").onclick = async () => {
    try { await jackPost("/api/jack/track", {title:$("jackTrackTitle").value.trim(),category:$("jackTrackType").value}); $("jackTrackTitle").value = ""; await jackRefresh(); toast("Title tracked"); }
    catch (error) { toast(error.message); }
  };
  $("jackMagnetButton").onclick = () => {
    const url = $("jackMagnet").value.trim();
    if (!url.startsWith("magnet:?xt=urn:btih:")) return toast("Enter a magnet link");
    const name = new URLSearchParams(url.slice(8)).get("dn") || "Magnet download";
    jackReview({title:name,source:"magnet",url,quality:"unknown"});
  };
  $("jackCancelDownload").onclick = () => $("jackDialog").close();
  $("jackConfirmDownload").onclick = async () => {
    const button = $("jackConfirmDownload");
    button.disabled = true;
    try {
      await jackPost("/api/jack/download", {...jackCandidate,category:$("jackDownloadType").value,quality:$("jackDownloadQuality").value});
      $("jackDialog").close();
      $("jackMagnet").value = "";
      await jackRefresh();
      toast("Download started");
    } catch (error) { toast(error.message); }
    finally { button.disabled = false; }
  };
  jackRefresh();
  jackSearch();
  setInterval(jackRefresh, 30000);
  setInterval(() => { if (!document.hidden) jackSearch(); }, 600000);
}

function setPadMode(mode) {
  $("dpad").classList.toggle("hidden", mode !== "dpad");
  $("touchpad").classList.toggle("hidden", mode !== "touch");
  $("padMode").classList.toggle("selected", mode === "dpad");
  $("touchMode").classList.toggle("selected", mode === "touch");
  localStorage.setItem("padMode", mode);
}

function installTouchpad() {
  const pad = $("touchpad");
  let startX = 0, startY = 0, startTime = 0;
  let lastX = 0, lastY = 0;

  pad.addEventListener("pointerdown", e => {
    startX = lastX = e.clientX;
    startY = lastY = e.clientY;
    startTime = Date.now();
    pad.setPointerCapture(e.pointerId);
  });

  pad.addEventListener("pointermove", e => {
    if (!pad.hasPointerCapture(e.pointerId)) return;

    const dx = e.clientX - lastX;
    const dy = e.clientY - lastY;

    if (Math.max(Math.abs(dx), Math.abs(dy)) < 45) return;
    if (Date.now() - lastDirection < 100) return;

    const direction = Math.abs(dx) > Math.abs(dy)
      ? (dx > 0 ? "right" : "left")
      : (dy > 0 ? "down" : "up");

    key(direction);
    lastDirection = Date.now();
    lastX = e.clientX;
    lastY = e.clientY;
  });

  pad.addEventListener("pointerup", e => {
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;

    if (Math.hypot(dx, dy) < 20 &&
        Date.now() - startTime < 450) {
      key("ok");
    }
  });
}

function installLongPress() {
  const repeatable = new Set([
    "up", "down", "left", "right", "volup", "voldown"
  ]);

  document.querySelectorAll("[data-key]").forEach(button => {
    const name = button.dataset.key;
        if (name === "power") {
      let timer = null;
      let holding = false;
      let pressed = false;
      let suppressClick = false;

      button.addEventListener("pointerdown", e => {
        if (e.button !== 0) return;

        pressed = true;
        holding = false;
        suppressClick = false;

        clearTimeout(timer);
        timer = setTimeout(() => {
          if (!pressed) return;
          holding = true;
          command("power_start", "power");
        }, 700);
      });

      const release = () => {
        if (!pressed) return;

        pressed = false;
        clearTimeout(timer);

        if (holding) {
          command("power_end", "power");
          suppressClick = true;
        } else {
          key("power");
          suppressClick = true;
        }

        holding = false;
      };

      button.addEventListener("pointerup", release);
      button.addEventListener("pointercancel", release);
      button.addEventListener("pointerleave", release);

      button.addEventListener("click", e => {
        if (suppressClick) {
          e.preventDefault();
          suppressClick = false;
          return;
        }
        key("power");
      });

      return;
    }
    let delayTimer = null;
    let repeatTimer = null;
    let started = false;

    button.addEventListener("pointerdown", e => {
      if (!repeatable.has(name)) return;
      if (e.button !== 0) return;

      started = true;
      key(name);

      delayTimer = setTimeout(() => {
        repeatTimer = setInterval(() => key(name), 170);
      }, 450);
    });

    const stop = () => {
      clearTimeout(delayTimer);
      clearInterval(repeatTimer);
      delayTimer = repeatTimer = null;
    };

    button.addEventListener("pointerup", stop);
    button.addEventListener("pointercancel", stop);
    button.addEventListener("pointerleave", stop);

    button.addEventListener("click", e => {
      if (started) {
        started = false;
        return;
      }
      key(name);
    });
  });
}

function renderApps() {
  const favoritesKey = `favorites:${tv}`;
  const grid = $("appGrid");
  let favorites;
  try {
    favorites = JSON.parse(localStorage.getItem(favoritesKey) || "[]");
    if (!Array.isArray(favorites)) favorites = [];
  } catch { favorites = []; }

    grid.replaceChildren();
  $("favoriteList").replaceChildren();

  const remoteFavorites = $("remoteFavorites");
  remoteFavorites.replaceChildren();

  const selectedFavorites = favorites.filter(id => apps[id]);
  $("appsMessage").textContent = Object.keys(apps).length > 1
    ? "TV apps and Pi shortcuts"
    : "No installed apps found. Check TV diagnostics and refresh.";

  $("noFavorites").hidden = selectedFavorites.length > 0;

  for (const id of selectedFavorites) {
    const button = document.createElement("button");
    button.textContent = "★ " + apps[id].name;
    button.onclick = () => launchApp(id);
    remoteFavorites.appendChild(button);
  }

    const ordered = [
    ...favorites.filter(id => apps[id]),
    ...Object.keys(apps).filter(id => !favorites.includes(id))
  ];

  for (const id of ordered) {
    const button = document.createElement("button");
    button.textContent =
      (favorites.includes(id) ? "★ " : "") + apps[id].name;

    button.onclick = () => launchApp(id);
    grid.appendChild(button);

    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = favorites.includes(id);

    checkbox.onchange = () => {
      const current = new Set(JSON.parse(
        localStorage.getItem(favoritesKey) || "[]"
      ));
      if (checkbox.checked) current.add(id);
      else current.delete(id);
      localStorage.setItem(
        favoritesKey, JSON.stringify([...current])
      );
      prefDraft.favorites[tv] = [...current];
      queuePreferencesSave();
      renderApps();
    };

        label.append(checkbox, document.createTextNode(apps[id].name));

    if (favorites.includes(id)) {
      const position = favorites.indexOf(id);

      for (const [symbol, offset] of [["▲", -1], ["▼", 1]]) {
        const moveButton = document.createElement("button");
        moveButton.type = "button";
        moveButton.textContent = symbol;
        moveButton.disabled =
          position + offset < 0 ||
          position + offset >= favorites.length;

        moveButton.onclick = () => {
          const reordered = [...favorites];
          const target = position + offset;

          [reordered[position], reordered[target]] =
            [reordered[target], reordered[position]];

          localStorage.setItem(
            favoritesKey, JSON.stringify(reordered)
          );
          prefDraft.favorites[tv] = reordered;
          queuePreferencesSave();

          renderApps();
        };

        label.appendChild(moveButton);
      }
    }

    $("favoriteList").appendChild(label);
  }
}

function renderScenes() {
  if (!prefDraft) return;
  const scenes = prefDraft.scenes.filter(scene => scene.tv === tv);
  const buttons = $("sceneButtons");
  const list = $("sceneList");
  buttons.replaceChildren();
  list.replaceChildren();
  $("noScenes").hidden = scenes.length > 0;
  for (const scene of scenes) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = scene.name;
    button.onclick = async () => {
      button.disabled = true;
      try {
        const result = await pairingPost("/api/scenes/run", {id:scene.id});
        toast(`${result.name} is ready`);
        updateStatus();
      } catch (error) { toast(error.message); }
      finally { button.disabled = false; }
    };
    buttons.appendChild(button);
    const row = document.createElement("div");
    row.className = "sceneItem";
    const label = document.createElement("span");
    label.textContent = `${scene.name} · ${scene.app ? (apps[scene.app]?.name || scene.app) : "No app"}${scene.volume == null ? "" : ` · ${scene.volume}%`}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove ${scene.name} scene`);
    remove.onclick = () => {
      prefDraft.scenes = prefDraft.scenes.filter(item => item.id !== scene.id);
      renderScenes();
      queuePreferencesSave();
    };
    row.append(label, remove);
    list.appendChild(row);
  }
  const appSelect = $("sceneApp");
  const prior = appSelect.value;
  appSelect.replaceChildren(new Option("No app", ""));
  for (const [id, app] of Object.entries(apps)) {
    appSelect.add(new Option(app.name, id));
  }
  if ([...appSelect.options].some(option => option.value === prior)) appSelect.value = prior;
}

function saveScene() {
  const name = $("sceneName").value.trim();
  const appId = $("sceneApp").value || null;
  const volumeText = $("sceneVolume").value.trim();
  const volume = volumeText === "" ? null : Number(volumeText);
  if (!name || name.length > 40 || (appId === null && volume === null) ||
      (volume !== null && (!Number.isInteger(volume) || volume < 0 || volume > 100))) {
    toast("Enter a name, an app or volume, and a volume from 0 to 100");
    return;
  }
  if (prefDraft.scenes.length >= 12) { toast("Maximum of 12 scenes reached"); return; }
  const id = `scene-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,6)}`;
  prefDraft.scenes.push({id, name, tv, app:appId, volume});
  $("sceneName").value = "";
  $("sceneVolume").value = "";
  renderScenes();
  queuePreferencesSave();
  toast("Scene saved");
}

async function loadGuestStatus() {
  try {
    const response = await fetch("/api/guest", {cache:"no-store"});
    if (!response.ok) throw new Error("Guest status unavailable");
    const data = await response.json();
    $("guestStatus").textContent = data.active
      ? `Guest access expires ${new Date(data.expires * 1000).toLocaleString()}.`
      : "Guest access is off.";
    $("revokeGuest").hidden = !data.active;
  } catch { $("guestStatus").textContent = "Could not check guest access."; }
}

async function createGuestPin() {
  const button = $("createGuest");
  button.disabled = true;
  try {
    const data = await pairingPost("/api/guest", {hours:Number($("guestHours").value)});
    $("guestPin").textContent = `Guest PIN: ${data.pin}`;
    await loadGuestStatus();
    toast("Share the PIN privately; it appears only now");
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
}

async function revokeGuestAccess() {
  try {
    const response = await fetch("/api/guest", {method:"DELETE",headers:{"X-CSRF-Token":csrf}});
    if (!response.ok) throw new Error("Could not revoke guest access");
    $("guestPin").textContent = "";
    await loadGuestStatus();
    toast("Guest access revoked");
  } catch (error) { toast(error.message); }
}

async function updateScreensaverStatus() {
  const selectedTV = tv;
  const button = $("toggleScreensaver");
  if (tvTypes[tv] !== "android") {
    button.disabled = true;
    button.textContent = "Android TV only";
    $("screensaverStatus").textContent = "LG webOS uses its own screen saver settings.";
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch(`/api/screensaver/${encodeURIComponent(tv)}`, {cache:"no-store"});
    const data = await response.json();
    if (selectedTV !== tv) return;
    if (!response.ok) throw new Error(data.error || "Screensaver unavailable");
    button.dataset.enabled = String(data.enabled);
    button.textContent = data.enabled ? "Restore previous screensaver" : "Use MagicMirror as screensaver";
    $("screensaverStatus").textContent = data.enabled
      ? "MagicMirror is selected for idle time."
      : "Your TV currently uses another screensaver.";
  } catch (error) {
    if (selectedTV !== tv) return;
    button.textContent = "Retry screensaver check";
    button.dataset.enabled = "";
    $("screensaverStatus").textContent = error.message;
  } finally {
    if (selectedTV === tv) button.disabled = false;
  }
}

async function toggleScreensaver() {
  const button = $("toggleScreensaver");
  if (!button.dataset.enabled) { await updateScreensaverStatus(); return; }
  button.disabled = true;
  try {
    const enabled = button.dataset.enabled !== "true";
    const data = await pairingPost(`/api/screensaver/${encodeURIComponent(tv)}`, {enabled});
    toast(data.enabled ? "MagicMirror screensaver enabled" : "Previous screensaver restored");
    await updateScreensaverStatus();
  } catch (error) {
    toast(error.message);
    await updateScreensaverStatus();
  }
}

async function loadApps(refresh = false) {
  const selectedTV = tv;
  try {
    const response = await fetch(`/api/apps/${encodeURIComponent(tv)}${refresh ? "?refresh=1" : ""}`, {cache: "no-store"});
    if (!response.ok) throw new Error("Discovery unavailable");
    const data = await response.json();
    if (selectedTV !== tv) return;
    apps = {...(data.apps || {}), ...specialApps};
    renderApps();
    renderScenes();
    updateStatus();
  } catch {
    if (selectedTV !== tv) return;
    apps = {...specialApps};
    renderApps();
    renderScenes();
    $("appsMessage").textContent = "App discovery unavailable. Check the TV's ADB connection, then refresh.";
  }
}

function installVoice() {
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;
  const button = $("voiceButton");
  const info = $("voiceInfo");
  if (!SpeechRecognition || !window.isSecureContext) {
    button.disabled = true;
    info.textContent = !window.isSecureContext
      ? "Voice control needs HTTPS or localhost. The Pi's HTTP address cannot use the microphone."
      : "Voice recognition is unavailable in this browser.";
    return;
  }
  let recognition;
  $("voiceButton").onclick = () => {
    if (recognition) {
      recognition.stop();
      return;
    }
    recognition = new SpeechRecognition();
    recognition.lang = "en-KE";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    button.textContent = "Stop listening";
    button.classList.add("listening");
    info.textContent = "Listening… Your words will appear in the text box.";
    recognition.onresult = event => {
      const transcript = event.results?.[0]?.[0]?.transcript?.trim();
      if (!transcript) return;
      $("tvText").value = transcript;
      info.textContent = "Text captured. Review it, then tap Send Text.";
    };
    recognition.onerror = event => {
      info.textContent = event.error === "not-allowed"
        ? "Microphone access is blocked. Allow it in your browser to use voice control."
        : `Voice recognition: ${event.error}`;
    };
    recognition.onend = () => {
      recognition = null;
      button.textContent = "Speak";
      button.classList.remove("listening");
    };
    try { recognition.start(); }
    catch {
      recognition = null;
      button.textContent = "Speak";
      button.classList.remove("listening");
      info.textContent = "Could not start voice recognition. Try again.";
    }
  };
}

function formatGiB(bytes) {
  return `${(bytes / (1024 ** 3)).toFixed(2)} GiB`;
}

async function updateDeviceInfo() {
  const selectedTV = tv;
  const model = $("deviceModel");
  const ramBar = $("ramBar");
  const storageBar = $("storageBar");

  try {
    const response = await fetch(
      `/api/device-info/${encodeURIComponent(tv)}`,
      { cache: "no-store" }
    );

    if (!response.ok) throw new Error("Diagnostics unavailable");

    const data = await response.json();
    if (selectedTV !== tv) return;

    model.textContent = `${data.model} · Android ${data.android}`;

    const ramTotal = data.ram?.total;
    const ramAvailable = data.ram?.available;

    if (ramTotal > 0 && ramAvailable != null) {
      const ramUsed = Math.max(0, ramTotal - ramAvailable);
      const ramPercent = Math.min(100, Math.round(ramUsed / ramTotal * 100));

      $("ramPercent").textContent = `${ramPercent}%`;
      ramBar.style.width = `${ramPercent}%`;
      $("ramDetails").textContent =
        `${formatGiB(ramUsed)} used · ${formatGiB(ramAvailable)} available · ${formatGiB(ramTotal)} total`;
    }

    const storageTotal = data.storage?.total;
    const storageUsed = data.storage?.used;
    const storageAvailable = data.storage?.available;

    if (storageTotal > 0 && storageUsed != null) {
      const storagePercent = Math.min(
        100,
        Math.round(storageUsed / storageTotal * 100)
      );

      $("storagePercent").textContent = `${storagePercent}%`;
      storageBar.style.width = `${storagePercent}%`;
      $("storageDetails").textContent =
        `${formatGiB(storageUsed)} used · ${formatGiB(storageAvailable)} available · ${formatGiB(storageTotal)} total`;
    }
    $("deviceInfoUpdated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    if (selectedTV !== tv) return;
    model.textContent = "TV diagnostics unavailable";
    $("deviceInfoUpdated").textContent = "Could not refresh device information.";
    $("ramPercent").textContent = "—";
    $("storagePercent").textContent = "—";
    $("ramDetails").textContent = "Unable to read TV memory";
    $("storageDetails").textContent = "Unable to read TV storage";
    ramBar.style.width = "0%";
    storageBar.style.width = "0%";
  }
}

async function init() {
  const response = await fetch("/api/bootstrap");
  if (!response.ok) {
    toast("Unable to initialize remote. Refresh or log in again.");
    return;
  }

  const data = await response.json();
  csrf = data.csrf;
  installJack();
  tvNames = Object.fromEntries(Object.entries(data.tvs).map(([id, config]) => [id, config.name]));
  try {
    const prefs = data.preferences || await preferencesRequest(localPreferences(), true);
    applySharedPreferences(prefs);
  } catch (error) {
    prefDraft = localPreferences();
    toast("Using this phone's preferences until Pi sync is available");
  }
  apps = {...(data.apps || {}), ...specialApps};
  tvTypes = Object.fromEntries(Object.entries(data.tvs).map(([id, config]) => [id, config.type || "android"]));

  const select = $("tvSelect");
  select.replaceChildren();

  for (const [id, config] of Object.entries(data.tvs)) {
    const option = document.createElement("option");
    option.value = id;
    option.textContent = config.name;
    select.appendChild(option);
  }

  const saved = localStorage.getItem("selectedTV");
  tv = saved && data.tvs[saved]
    ? saved
    : Object.keys(data.tvs)[0];

  select.value = tv;
  select.onchange = () => {
    tv = select.value;
    localStorage.setItem("selectedTV", tv);
    apps = {};
    renderApps();
    renderScenes();
    loadApps();
    updateStatus();
    updateDeviceInfo();
    updateScreensaverStatus();
  };

  // On a new phone, prefer a reachable TV over an old saved IP.
  if (!saved && Object.keys(data.tvs).length > 1) {
    setTimeout(async () => {
      if (localStorage.getItem("selectedTV")) return;
      const ids = Object.keys(data.tvs);
      const states = await Promise.all(ids.map(async id => {
        try {
          const response = await fetch(`/api/status/${encodeURIComponent(id)}`, {cache:"no-store"});
          return response.ok && (await response.json()).connected;
        } catch { return false; }
      }));
      const active = ids[states.findIndex(Boolean)];
      if (active && active !== tv && !localStorage.getItem("selectedTV")) {
        select.value = active;
        select.onchange();
      }
    }, 3000);
  }

  document.querySelectorAll("[data-tab]").forEach(button => {
    button.onclick = () => selectTab(button.dataset.tab);
  });

  $("padMode").onclick = () => setPadMode("dpad");
  $("touchMode").onclick = () => setPadMode("touch");
  setPadMode(localStorage.getItem("padMode") || "dpad");

  installTouchpad();
  installLongPress();
  installVoice();
  installPairing();
  $("magicMirrorShortcut").onclick = () => launchApp("magicmirror");
  renderApps();
  renderScenes();
  loadApps();
  $("saveScene").onclick = saveScene;
  $("toggleScreensaver").onclick = toggleScreensaver;
  updateScreensaverStatus();
  $("createGuest").onclick = createGuestPin;
  $("revokeGuest").onclick = revokeGuestAccess;
  loadGuestStatus();
  $("refreshApps").onclick = () => loadApps(true);

  $("sendText").onclick = () => {
    const value = $("tvText").value;
    if (value.trim()) command("text", value);
  };

  $("clearText").onclick = () => $("tvText").value = "";
  $("openTvSettings").onclick = () => key("settings");
  $("refreshStatus").onclick = updateStatus;
  $("refreshDeviceInfo").onclick = async () => {
    const button = $("refreshDeviceInfo");
    button.disabled = true;
    $("deviceInfoUpdated").textContent = "Refreshing…";
    try { await updateDeviceInfo(); } finally { button.disabled = false; }
  };
  $("reconnectSaved").onclick = async () => {
    const button = $("reconnectSaved");
    button.disabled = true;
    $("reconnectSavedMessage").textContent = "Searching for saved TVs…";
    try {
      const response = await fetch("/api/tvs/reconnect", {
        method:"POST", headers:{"X-CSRF-Token":csrf}, credentials:"same-origin"
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "TV search failed");
      $("reconnectSavedMessage").textContent = data.message;
      await updateStatus();
      await updateDeviceInfo();
    } catch (error) {
      $("reconnectSavedMessage").textContent = error.message;
    } finally { button.disabled = false; }
  };

  const availableThemes = [
  "dark-glass",
  "light-glass",
  "midnight",
  "dark",
  "light"
];

function applyTheme(theme) {
  const selected = availableThemes.includes(theme)
    ? theme
    : "dark-glass";

  document.body.classList.remove(
    "light",
    "dark-glass",
    "light-glass",
    "midnight"
  );

  if (selected === "light") {
    document.body.classList.add("light");
  } else if (selected !== "dark") {
    document.body.classList.add(selected);
  }

  $("themeSelect").value = selected;
  localStorage.setItem("theme", selected);
}

applyTheme(localStorage.getItem("theme") || "dark-glass");

$("themeSelect").onchange = () => {
  applyTheme($("themeSelect").value);
  prefDraft.theme = $("themeSelect").value;
  queuePreferencesSave();
};

  // Persistent accent choice; only CSS variables change, never TV commands.
  const accentColors = {
    blue: "#779bff", violet: "#ad8bff", mint: "#4bd5b1",
    coral: "#ff8b8e", amber: "#f4b95f", pink: "#ed8ac6"
  };
  const accentOptions = $("accentOptions");
  function setAccent(name) {
    const selected = Object.hasOwn(accentColors, name) ? name : "blue";
    document.documentElement.style.setProperty("--user-accent", accentColors[selected]);
    localStorage.setItem("accentColor", selected);
    accentOptions.querySelectorAll("button").forEach(button => {
      const active = button.dataset.accent === selected;
      button.classList.toggle("selected", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }
  for (const [name, color] of Object.entries(accentColors)) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.accent = name;
    button.className = "accentSwatch";
    button.style.backgroundColor = color;
    button.setAttribute("aria-label", name + " accent");
    button.title = name;
    button.onclick = () => {
      setAccent(name);
      prefDraft.accent = name;
      queuePreferencesSave();
    };
    accentOptions.appendChild(button);
  }
  setAccent(localStorage.getItem("accentColor") || "blue");

  $("haptics").checked =
    localStorage.getItem("haptics") !== "false";

  $("haptics").onchange = () => {
    localStorage.setItem("haptics", $("haptics").checked);
    prefDraft.haptics = $("haptics").checked;
    queuePreferencesSave();
  };

  setInterval(async () => {
    try {
      await prefWriteQueue.catch(() => {});
      const response = await fetch("/api/preferences", {cache:"no-store"});
      if (!response.ok) return;
      const {preferences} = await response.json();
      if (!preferences || JSON.stringify(preferences) === JSON.stringify(prefDraft)) return;
      applySharedPreferences(preferences);
      applyTheme(preferences.theme);
      setAccent(preferences.accent);
      $("haptics").checked = preferences.haptics;
      renderApps();
      renderScenes();
    } catch (error) { console.debug("Preference sync paused", error); }
  }, 30000);

  await updateStatus();
statusTimer = setInterval(updateStatus, 3000);

updateDeviceInfo();
setInterval(updateDeviceInfo, 30000);
}

init().catch(error => {
  console.error(error);
  toast("Unable to initialize remote");
});
