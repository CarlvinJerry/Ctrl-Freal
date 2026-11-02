
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
let ambientArtwork = "";
let mediaFreeBytes = null;
const specialApps = {magicmirror:{name:"MagicMirror", package:"magicmirror"}};

const $ = id => document.getElementById(id);

function toast(message, kind = "") {
  const el = $("toast");
  el.textContent = message;
  el.classList.toggle("completionToast", kind === "complete");
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
          favorites, scenes:[], profiles:JSON.parse(localStorage.getItem("roomProfiles") || "{}")} ;
}

function applySharedPreferences(prefs) {
  prefDraft = prefs;
  localStorage.setItem("theme", prefs.theme);
  localStorage.setItem("accentColor", prefs.accent);
  localStorage.setItem("haptics", String(prefs.haptics));
  localStorage.setItem("roomProfiles", JSON.stringify(prefs.profiles || {}));
  for (const id of Object.keys(tvNames)) {
    localStorage.setItem(`favorites:${id}`, JSON.stringify(prefs.favorites?.[id] || []));
  }
}

function roomProfile() {
  return prefDraft?.profiles?.[tv] || {theme:prefDraft?.theme || "dark-glass",
    accent:prefDraft?.accent || "blue", effects:"subtle", glass:"balanced",
    background:"none", dim:45, blur:8};
}

function applyRoomBackground() {
  const profile = roomProfile();
  const image = profile.background === "custom" ? `/api/background/${encodeURIComponent(tv)}?v=${localStorage.getItem(`backgroundVersion:${tv}`) || "0"}` :
    profile.background === "artwork" ? ambientArtwork : "";
  document.body.style.setProperty("--room-image", image ? `url("${image}")` : "none");
  document.body.style.setProperty("--room-dim", String(image ? (profile.dim ?? 45) / 100 : 0));
  document.body.style.setProperty("--room-blur", `${profile.blur ?? 8}px`);
  document.body.dataset.glass = profile.glass || "balanced";
  document.body.dataset.effects = profile.effects || "subtle";
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

    if (response.ok && result.action === "power_wake") toast("LG wake signal sent. The TV may take a few seconds to connect.");
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

async function loadSavedTvs() {
  const list = $("savedTvList");
  const message = $("savedTvMessage");
  try {
    const response = await fetch("/api/tvs/saved", {cache:"no-store"});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load saved TVs");
    list.replaceChildren();
    for (const item of result.tvs) {
      const row = document.createElement("div");
      row.className = "savedTvRow";
      const details = document.createElement("div");
      details.className = "savedTvDetails";
      const name = document.createElement("strong");
      name.textContent = item.name;
      const meta = document.createElement("span");
      meta.textContent = `${item.type === "webos" ? "LG webOS" : "Android TV"} · ${item.host} · ${item.active ? "Enabled" : "Disabled"}`;
      details.append(name, meta);
      const actions = document.createElement("div");
      actions.className = "savedTvActions";
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.textContent = item.active ? "Disable" : "Enable";
      toggle.onclick = async () => {
        toggle.disabled = true;
        try {
          await pairingPost(`/api/tvs/${encodeURIComponent(item.id)}/active`, {active:!item.active});
          location.reload();
        } catch (error) { message.textContent = error.message; toggle.disabled = false; }
      };
      actions.append(toggle);
      if (!item.active) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "Remove";
        remove.onclick = async () => {
          if (!confirm(`Remove ${item.name} from paired TVs? You would need to pair it again to use it.`)) return;
          remove.disabled = true;
          try {
            const response = await fetch(`/api/tvs/${encodeURIComponent(item.id)}`, {
              method:"DELETE", headers:{"X-CSRF-Token":csrf}
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || "Could not remove TV");
            location.reload();
          } catch (error) { message.textContent = error.message; remove.disabled = false; }
        };
        actions.append(remove);
      }
      row.append(details, actions);
      list.append(row);
    }
    message.textContent = "";
  } catch (error) { message.textContent = error.message; }
}

function pairInstructions() {
  const lg = $("pairType").value === "webos";
  $("pairInstructions").textContent = lg
    ? "LG LJ570U / webOS 3.5: turn on the TV and enable Settings → All Settings → Network → LG Connect Apps. For power-on, also enable General → Mobile TV On → Turn On via Wi-Fi, if available. Start pairing, then accept the TV's request with its physical remote. No code is needed."
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
    $("lgWakeHint").hidden = tvTypes[tv] !== "webos";
    if (tvTypes[tv] === "webos") $("lgWakeHint").textContent = data.wake_ready
      ? "LG wake is ready. Enable General → Mobile TV On → Turn On via Wi-Fi on the TV for power-on."
      : "To enable LG wake, turn the TV on once with its physical remote while the Pi is online. Then enable General → Mobile TV On → Turn On via Wi-Fi on the TV.";

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

function jackCard(title, detail, artwork) {
  const card = document.createElement("div");
  card.className = "jackCard";
  if (artwork) {
    const image = document.createElement("img");
    image.className = "jackArtwork";
    image.src = artwork;
    image.alt = "";
    image.loading = "lazy";
    image.onerror = () => { image.remove(); card.classList.remove("hasArtwork"); };
    card.append(image);
    card.classList.add("hasArtwork");
  }
  const heading = document.createElement("strong");
  heading.textContent = title;
  const small = document.createElement("small");
  small.textContent = detail;
  card.append(heading, small);
  return card;
}

function jackBytes(value) {
  if (!value || value < 0) return "0 B";
  if (value < 1048576) return `${Math.max(1, Math.round(value / 1024))} KB`;
  return value >= 1073741824 ? `${(value / 1073741824).toFixed(1)} GB` : `${(value / 1048576).toFixed(1)} MB`;
}

function jackPlexLabel(item) {
  return item.plex_ready === true ? "Ready in Plex" : item.plex_ready === false ? "Waiting for Plex scan" : "Saved to media folder";
}

function jackReview(candidate) {
  jackCandidate = candidate;
  $("jackDialogTitle").textContent = candidate.title;
  $("jackDialogSource").textContent = candidate.source === "archive" ? "Internet Archive catalog" : candidate.source === "feed" ? "Saved torrent feed" : candidate.source === "indexer" ? candidate.indexer_name : "Your magnet link";
  $("jackDownloadQuality").value = ["480p","720p","1080p","2160p"].includes(candidate.quality) ? candidate.quality : "any";
  $("jackDownloadType").value = candidate.category || (/S\d{1,2}E\d{1,2}/i.test(candidate.title) ? "series" : "movie");
  const estimate = Number(candidate.size || candidate.size_bytes || 0);
  $("jackStorageForecast").textContent = mediaFreeBytes === null ? "Storage information is loading." :
    estimate > 0 ? `${jackBytes(estimate)} needed · ${jackBytes(mediaFreeBytes)} free${estimate > mediaFreeBytes ? " · Not enough space" : mediaFreeBytes - estimate < 10 * 1024 ** 3 ? " · Storage will be low" : ""}` :
    `${jackBytes(mediaFreeBytes)} free · Size unavailable from source`;
  $("jackConfirmDownload").disabled = estimate > 0 && mediaFreeBytes !== null && estimate > mediaFreeBytes;
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
    const card = jackCard(item.title, detail, item.artwork);
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

async function jackSearch(allowEmpty = false) {
  const serial = ++jackSearchSerial;
  const query = $("jackQuery").value.trim();
  if (!query && allowEmpty !== true) {
    jackResultsRaw = [];
    $("jackResults").replaceChildren();
    $("jackMessage").textContent = "Search a title or explore latest releases.";
    return;
  }
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
    $("jackClientStatus").textContent = data.jobs_refreshing ?
      "Download status delayed; last known progress is shown" :
      data.client_available ? "Torrent client ready" : "Torrent service unavailable on the Pi";
    $("jackTracked").replaceChildren();
    data.tracked.filter(item => !(item.category === "movie" && jackCompletedHistory.some(done => {
      const name = done.title.toLowerCase().replace(/^[\s._-]+/, "");
      const tracked = item.title.toLowerCase();
      return done.category === "movie" && name.startsWith(tracked) &&
        (name.length === tracked.length || /[\s._(\[-]/.test(name[tracked.length]));
    }))).forEach(item => {
      const card = jackCard(item.title, item.category);
      card.append(jackButton("Stop tracking", async () => {
        try { await jackPost("/api/jack/untrack", item); await jackRefresh(); }
        catch (error) { toast(error.message); }
      }));
      $("jackTracked").append(card);
    });
    $("jackMatches").replaceChildren();
    $("jackNotices").replaceChildren();
    const noticeGroups = new Map();
    (data.notices || []).forEach(item => {
      const group = item.kind === "episode" ? item.title : (item.query || item.title);
      if (!noticeGroups.has(group)) noticeGroups.set(group, []);
      noticeGroups.get(group).push(item);
    });
    for (const [name, notices] of noticeGroups) {
      const group = document.createElement("details");
      group.className = "jackGroup";
      const summary = document.createElement("summary");
      summary.textContent = `${name} · ${notices.length} update${notices.length === 1 ? "" : "s"}`;
      group.append(summary);
      const list = document.createElement("div");
      list.className = "jackGroupItems";
      notices.forEach(item => {
        const card = jackCard(item.title, item.detail, item.artwork);
        const actions = document.createElement("div");
        actions.className = "jackActions";
        actions.append(jackButton("Search", () => { $("jackQuery").value = item.query; jackSearch(); $("jackResults").scrollIntoView({behavior:"smooth"}); }),
          jackButton("Dismiss", async () => { try { await jackPost("/api/jack/notice/dismiss", {key:item.key}); await jackRefresh(); } catch (error) { toast(error.message); } }));
        card.append(actions);
        list.append(card);
      });
      group.append(list);
      $("jackNotices").append(group);
    }
    $("jackMatchCount").textContent = `(${data.matches.length})`;
    data.matches.filter(item => !item.seen).forEach(item => {
      const choices = [item, ...(item.alternatives || [])];
      const group = document.createElement("details");
      group.className = "jackGroup";
      const summary = document.createElement("summary");
      const episode = item.title.match(/S\d{1,2}[ ._-]*E\d{1,2}/i)?.[0] || "New release";
      summary.textContent = `${item.tracked_title} · ${episode} · ${choices.length} option${choices.length === 1 ? "" : "s"}`;
      group.append(summary);
      const list = document.createElement("div");
      list.className = "jackGroupItems";
      choices.forEach(choice => {
        const card = jackCard(choice.title, `${choice.indexer_name || choice.source || "Source"} · ${choice.quality} · ${choice.seeders || 0} seeders`, item.artwork);
        card.classList.add("jackMatchCard");
        card.append(jackButton("Review", () => jackReview(choice)));
        list.append(card);
      });
      group.append(list);
      group.append(jackButton("Dismiss episode", async () => {
        try { for (const choice of choices) await jackPost("/api/jack/match-seen", {key:choice.key}); await jackRefresh(); }
        catch (error) { toast(error.message); }
      }));
      $("jackMatches").append(group);
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
    const openJobDetails = new Set([...$("jackJobs").querySelectorAll("details[open]")].map(node => node.dataset.job));
    $("jackJobs").replaceChildren();
    $("jackArchivedJobs").replaceChildren();
    let archiveCount = 0;
    data.jobs.forEach(job => {
      const done = job.imported;
      const inPlex = ["movie", "series"].includes(job.category);
      const labels = {error:"Needs attention", paused:"Paused", stalled:"Stalled", waiting:"Waiting for peers", downloading:"Downloading", queued:"Queued"};
      const eta = job.eta > 0 && job.eta < 172800 ? ` · about ${job.eta < 3600 ? Math.ceil(job.eta / 60) + " min" : Math.ceil(job.eta / 3600) + " hr"} left` : "";
      const detail = done ? `${jackPlexLabel(job)} · ${job.category}` :
        `${labels[job.health] || "Downloading"} · ${Math.round(job.progress * 100)}% of ${jackBytes(job.size)} · ${job.peers} peers · ${jackBytes(job.rate)}/s${eta}${job.error ? " · " + job.error : ""}`;
      const card = jackCard(job.title, detail);
      if (!done) {
        const progress = document.createElement("progress");
        progress.max = 100;
        progress.value = Math.round(job.progress * 100);
        progress.setAttribute("aria-label", `Download ${Math.round(job.progress * 100)} percent complete`);
        card.append(progress);
      }
      const actions = document.createElement("div");
      actions.className = "jackActions";
      async function manage(action) {
        try { await jackPost("/api/jack/manage", {id:job.id, action}); await jackRefresh(); }
        catch (error) { toast(error.message); }
      }
      if (!done) actions.append(jackButton(job.status === 0 ? "Resume" : "Pause", () => manage(job.status === 0 ? "resume" : "pause")));
      if (job.health === "error") actions.append(jackButton("Verify files", () => manage("verify")));
      if (job.health === "stalled" || job.health === "error") actions.append(jackButton("Find another", () => {
        $("jackQuery").value = job.title;
        $("jackSource").value = "indexers";
        jackSearch();
        $("jackResults").scrollIntoView({behavior:"smooth"});
      }));
      if (done && job.plex_ready === false) actions.append(jackButton("Check Plex", jackRefresh));
      actions.append(jackButton(done ? "Remove from queue" : "Remove", () => manage("remove")));
      if (done && job.category === "movie" && !job.deleted && job.imported_paths?.length) {
        actions.append(jackButton("Delete movie", async () => {
          if (!confirm(`Delete ${job.title} from the Plex movie library? This removes ${job.imported_paths.length} file(s).`)) return;
          try { await jackPost("/api/jack/movie/delete", {source_ref:job.source_ref}); await jackRefresh(); toast("Movie removed from Plex library"); }
          catch (error) { toast(error.message); }
        }));
      }
      const detailSheet = document.createElement("details");
      detailSheet.className = "downloadDetails";
      detailSheet.dataset.job = String(job.id);
      detailSheet.open = openJobDetails.has(String(job.id));
      const summary = document.createElement("summary"); summary.textContent = "Details & actions";
      const metrics = document.createElement("p"); metrics.className = "hint";
      metrics.textContent = `${jackBytes(job.left || 0)} remaining · ${jackBytes(job.rate || 0)}/s · ${job.peers || 0} peers${job.error ? " · " + job.error : ""}`;
      detailSheet.append(summary, metrics, actions);
      card.append(detailSheet);
      (done ? $("jackArchivedJobs") : $("jackJobs")).append(card);
      if (done) archiveCount++;
      const noticeKey = `jackCompleted:${job.id}`;
      if (done && !localStorage.getItem(noticeKey)) {
        localStorage.setItem(noticeKey, "1");
        toast(inPlex ? `${job.title} finished; Plex will scan it` : `${job.title} is saved; add a Plex library`, "complete");
      }
    });
    jackCompletedHistory.filter(item => !data.jobs.some(job => job.source_ref === item.source_ref)).forEach(item => {
      const card = jackCard(item.title, `${jackPlexLabel(item)} · ${item.category}`);
      if (item.plex_ready === false) card.append(jackButton("Check Plex", jackRefresh));
      if (item.category === "movie" && item.imported_paths?.length) card.append(jackButton("Delete movie", async () => {
        if (!confirm(`Delete ${item.title} from the Plex movie library? This removes ${item.imported_paths.length} file(s).`)) return;
        try { await jackPost("/api/jack/movie/delete", {source_ref:item.source_ref}); await jackRefresh(); toast("Movie removed from Plex library"); }
        catch (error) { toast(error.message); }
      }));
      $("jackArchivedJobs").append(card);
      archiveCount++;
    });
    $("jackArchiveCount").textContent = `(${archiveCount})`;
    if (!data.jobs.some(job => !job.imported)) $("jackJobs").append(jackCard("Queue is clear", "Completed downloads are in the archive below."));
  } catch (error) { console.debug("Jack Sparrow status unavailable", error); }
}

function installJack() {
  $("jackSearchButton").onclick = () => jackSearch();
  $("jackExploreLatest").onclick = () => { $("jackQuery").value = ""; jackSearch(true); };
  $("jackQuery").onkeydown = event => { if (event.key === "Enter") jackSearch(); };
  $("jackSource").onchange = () => jackSearch();
  $("jackQuality").onchange = () => jackSearch();
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
      if (jackCandidate.key) {
        try { await jackPost("/api/jack/match-seen", {key:jackCandidate.key}); }
        catch (error) { console.debug("Match already cleared", error); }
      }
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
  setInterval(() => { if (!document.hidden && $("jackQuery").value.trim()) jackSearch(); }, 600000);
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
      let tapTimer = null;
      let taps = 0;
      let tapTV = null;
      let holding = false;
      let pressed = false;
      let suppressClick = false;

      const finishTaps = () => {
        clearTimeout(tapTimer);
        const count = taps;
        const selectedTV = tapTV;
        taps = 0;
        delete button.dataset.taps;
        if (selectedTV !== tv) return;
        if (count === 1) key("power");
        else if (count === 3) {
          if (tvTypes[tv] !== "android") return toast("Full shutdown needs Android TV debugging");
          toast("Shutting down TV…");
          command("power_shutdown", "power");
        } else if (count === 5) {
          if (tvTypes[tv] !== "android") return toast("Restart needs Android TV debugging");
          toast("Restarting TV…");
          command("power_reboot", "power");
        } else if (count) toast("Power: tap once for standby, 3 times to shut down, or 5 times to restart");
      };
      const registerTap = () => {
        if (tapTV !== tv) taps = 0;
        tapTV = tv;
        taps++;
        button.dataset.taps = String(taps);
        clearTimeout(tapTimer);
        if (taps === 5) finishTaps();
        else tapTimer = setTimeout(finishTaps, 500);
      };

      button.addEventListener("pointerdown", e => {
        if (e.button !== 0) return;

        pressed = true;
        holding = false;
        suppressClick = false;
        clearTimeout(tapTimer);

        clearTimeout(timer);
        timer = setTimeout(() => {
          if (!pressed) return;
          holding = true;
          taps = 0;
          delete button.dataset.taps;
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
          registerTap();
          suppressClick = true;
        }

        holding = false;
      };

      button.addEventListener("pointerup", release);
      const cancel = () => {
        if (!pressed) return;
        if (pressed && holding) { command("power_end", "power"); suppressClick = true; }
        pressed = false; holding = false; suppressClick = true; taps = 0;
        delete button.dataset.taps;
        clearTimeout(timer); clearTimeout(tapTimer);
      };
      button.addEventListener("pointercancel", cancel);
      button.addEventListener("pointerleave", e => { if (e.pointerType === "mouse") cancel(); });

      button.addEventListener("click", e => {
        if (suppressClick) {
          e.preventDefault();
          suppressClick = false;
          return;
        }
        registerTap();
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
  const gridScroll = grid.scrollTop;
  const favoriteScroll = $("favoriteList").scrollTop;
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
  $("appCount").textContent = `(${Math.max(0, Object.keys(apps).length - 1)})`;
  $("appsMessage").textContent = Object.keys(apps).length > 1
    ? "TV apps and Pi shortcuts"
    : "No installed apps found. Check TV diagnostics and refresh.";

  $("noFavorites").hidden = selectedFavorites.length > 0;

  for (const id of selectedFavorites) {
    const button = document.createElement("button");
    appButtonContent(button, id, true);
    button.onclick = () => launchApp(id);
    remoteFavorites.appendChild(button);
  }

    const ordered = [
    ...favorites.filter(id => apps[id]),
    ...Object.keys(apps).filter(id => !favorites.includes(id))
  ];

  for (const id of ordered) {
    const button = document.createElement("button");
    appButtonContent(button, id, favorites.includes(id));

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

    label.append(checkbox);
    if (apps[id].icon) {
      const icon = document.createElement("img");
      icon.src = apps[id].icon;
      icon.alt = "";
      icon.loading = "lazy";
      icon.onerror = () => icon.remove();
      label.append(icon);
    }
    label.append(document.createTextNode(apps[id].name));

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
  grid.scrollTop = gridScroll;
  $("favoriteList").scrollTop = favoriteScroll;
}

function appButtonContent(button, id, favorite) {
  button.classList.add("tvAppButton");
  button.setAttribute("aria-label", apps[id].name);
  if (apps[id].banner) {
    button.classList.add("hasBanner");
    const banner = document.createElement("img");
    banner.className = "tvAppBanner";
    banner.src = apps[id].banner;
    banner.alt = "";
    banner.loading = "lazy";
    banner.onerror = () => { banner.remove(); button.classList.remove("hasBanner"); button.querySelector(".tvAppName")?.classList.remove("srOnly"); };
    button.append(banner);
  } else if (apps[id].icon) {
    const icon = document.createElement("img");
    icon.className = "tvAppIcon";
    icon.src = apps[id].icon;
    icon.alt = "";
    icon.loading = "lazy";
    icon.onerror = () => icon.remove();
    button.append(icon);
  }
  const label = document.createElement("span");
  label.className = "tvAppName" + (apps[id].banner ? " srOnly" : "");
  label.textContent = apps[id].name;
  button.append(label);
  if (favorite) {
    const star = document.createElement("span");
    star.className = "tvAppStar";
    star.textContent = "★";
    star.setAttribute("aria-hidden", "true");
    button.append(star);
  }
}

async function pollAppIcons() {
  if (document.hidden || !tv || !Object.keys(apps).length) return;
  const selectedTV = tv;
  try {
    const response = await fetch(`/api/apps/${encodeURIComponent(tv)}/icons`, {cache:"no-store"});
    if (!response.ok) return;
    const data = await response.json();
    if (selectedTV !== tv) return;
    let changed = false;
    Object.entries(data.icons || {}).forEach(([id, url]) => {
      if (apps[id] && apps[id].icon !== url) { apps[id].icon = url; changed = true; }
    });
    Object.entries(data.banners || {}).forEach(([id, url]) => {
      if (apps[id] && apps[id].banner !== url) { apps[id].banner = url; changed = true; }
    });
    if (changed) renderApps();
  } catch (error) { console.debug("App icons unavailable", error); }
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
    button.dataset.repair = String(data.enabled && data.auto_start === false);
    button.textContent = data.enabled && data.auto_start === false ? "Repair MagicMirror screensaver" :
      data.enabled ? "Restore previous screensaver" : "Use MagicMirror as screensaver";
    $("screensaverStatus").textContent = data.enabled
      ? data.auto_start === false ? "MagicMirror is selected, but this TV blocks it from starting. Tap Repair to allow it." : "MagicMirror is selected for idle time. This TV's display timeout is set in Android TV settings."
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
    const enabled = button.dataset.repair === "true" || button.dataset.enabled !== "true";
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

function mediaButton(title, artwork, detail, onclick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "mediaButton";
  if (artwork) {
    const image = document.createElement("img");
    image.src = artwork;
    image.alt = "";
    image.loading = "lazy";
    image.onerror = () => image.remove();
    button.append(image);
  }
  const words = document.createElement("span");
  const strong = document.createElement("strong");
  strong.textContent = title;
  const small = document.createElement("small");
  small.textContent = detail;
  words.append(strong, small);
  button.append(words);
  button.onclick = onclick;
  return button;
}

async function loadContinueWatching() {
  try {
    const response = await fetch("/api/plex/continue", {cache:"no-store"});
    if (!response.ok) return;
    const data = await response.json();
    ambientArtwork = (data.items || []).find(item => item.artwork)?.artwork || "";
    if (prefDraft?.profiles?.[tv]?.background === "artwork") applyRoomBackground();
    const row = $("continueRow");
    row.replaceChildren();
    for (const item of data.items || []) {
      row.append(mediaButton(item.title, item.artwork, `${item.progress}% watched · Open Plex`, () => {
        if (apps.plex) launchApp("plex");
        else toast("Install or select Plex on this TV to continue watching");
      }));
    }
    $("continueSection").hidden = !row.childElementCount;
  } catch (error) { console.debug("Continue Watching unavailable", error); }
}

async function updateEcosystem() {
  try {
    const response = await fetch("/api/ecosystem", {cache:"no-store"});
    if (!response.ok) return;
    const data = await response.json();
    const strip = $("ecosystemStrip");
    strip.replaceChildren();
    const items = [["Pi", true], ["Plex", data.services?.plex],
      ["Downloads", data.services?.downloads], ["Search", data.services?.indexers]];
    for (const [label, ready] of items) {
      const chip = document.createElement("span");
      chip.className = ready === true ? "healthGood" : ready === false ? "healthBad" : "";
      chip.textContent = `${ready === true ? "●" : ready === false ? "○" : "·"} ${label}`;
      strip.append(chip);
    }
    if (data.storage) {
      mediaFreeBytes = data.storage.free;
      const chip = document.createElement("span");
      chip.textContent = `${jackBytes(data.storage.free)} free`;
      if (data.storage.free < 10 * 1024 ** 3) chip.className = "healthBad";
      strip.append(chip);
    }
  } catch (error) { console.debug("Pi health unavailable", error); }
}

async function globalSearch() {
  const query = $("globalQuery").value.trim();
  if (query.length < 2) return toast("Enter at least two characters");
  const dialog = $("globalSearchDialog");
  if (!dialog.open) dialog.showModal();
  $("globalSearchStatus").textContent = "Searching apps, Plex and Jack…";
  for (const id of ["globalApps", "globalPlex", "globalJack"]) $(id).replaceChildren();
  const matchingApps = Object.entries(apps).filter(([, app]) => app.name.toLowerCase().includes(query.toLowerCase())).slice(0, 12);
  for (const [id, app] of matchingApps) $("globalApps").append(mediaButton(app.name, app.icon, "Open on TV", () => { dialog.close(); launchApp(id); }));
  if (!matchingApps.length) $("globalApps").textContent = "No matching TV apps";
  const params = new URLSearchParams({q:query});
  const plexTask = fetch(`/api/plex/search?${params}`, {cache:"no-store"}).then(r => r.json()).then(data => {
    for (const item of data.items || []) $("globalPlex").append(mediaButton(item.title, item.artwork, "Open Plex on TV", () => {
      dialog.close(); if (apps.plex) launchApp("plex"); else toast("Plex is not available on this TV");
    }));
    if (!$("globalPlex").childElementCount) $("globalPlex").textContent = "No matching Plex titles";
  }).catch(() => { $("globalPlex").textContent = "Plex search unavailable"; });
  const jackTask = fetch(`/api/jack/search?${new URLSearchParams({q:query,quality:"any",source:"all"})}`, {cache:"no-store"})
    .then(r => r.json()).then(data => {
      for (const item of (data.results || []).slice(0, 12)) $("globalJack").append(mediaButton(item.title, item.artwork,
        `${item.quality} · ${item.indexer_name || item.source}`, () => { dialog.close(); jackReview(item); }));
      if (!$("globalJack").childElementCount) $("globalJack").textContent = "No matching releases";
    }).catch(() => { $("globalJack").textContent = "Jack search unavailable"; });
  await Promise.all([plexTask, jackTask]);
  $("globalSearchStatus").textContent = "Search complete";
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
  loadSavedTvs();
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
    applyRoomProfile();
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
  $("quickPlex").onclick = () => launchApp("plex");
  $("quickMirror").onclick = () => launchApp("magicmirror");
  $("quickDownloads").onclick = () => { selectTab("jack"); $("jackJobs").scrollIntoView({behavior:"smooth",block:"start"}); };
  $("quickSwitch").onclick = () => { $("tvSelect").focus(); try { $("tvSelect").showPicker?.(); } catch {} };
  renderApps();
  renderScenes();
  loadApps();
  loadContinueWatching();
  updateEcosystem();
  $("globalSearchButton").onclick = globalSearch;
  $("globalQuery").onkeydown = event => { if (event.key === "Enter") globalSearch(); };
  $("closeGlobalSearch").onclick = () => $("globalSearchDialog").close();
  setInterval(() => { if (!document.hidden) { loadContinueWatching(); updateEcosystem(); } }, 60000);
  setInterval(pollAppIcons, 12000);
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
  "frost",
  "warm-cinema",
  "high-contrast",
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
    "midnight", "frost", "warm-cinema", "high-contrast"
  );

  if (selected === "light") {
    document.body.classList.add("light");
  } else if (selected !== "dark") {
    document.body.classList.add(selected);
  }

  $("themeSelect").value = selected;
  localStorage.setItem("theme", selected);
}

function applyRoomProfile() {
  const profile = roomProfile();
  applyTheme(profile.theme);
  setAccent(profile.accent);
  $("backgroundMode").value = profile.background;
  $("backgroundDim").value = profile.dim;
  $("backgroundBlur").value = profile.blur;
  $("dimValue").value = `${profile.dim}%`;
  $("blurValue").value = `${profile.blur}px`;
  $("glassStrength").value = profile.glass;
  $("effectsLevel").value = profile.effects;
  applyRoomBackground();
}

function saveRoomProfile(changes) {
  prefDraft.profiles ||= {};
  prefDraft.profiles[tv] = {...roomProfile(), ...changes};
  applyRoomProfile();
  queuePreferencesSave();
}

applyTheme(localStorage.getItem("theme") || "dark-glass");

$("themeSelect").onchange = () => {
  prefDraft.theme = $("themeSelect").value;
  saveRoomProfile({theme:$("themeSelect").value});
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
      prefDraft.accent = name;
      saveRoomProfile({accent:name});
    };
    accentOptions.appendChild(button);
  }
  setAccent(localStorage.getItem("accentColor") || "blue");
  applyRoomProfile();
  $("backgroundMode").onchange = () => saveRoomProfile({background:$("backgroundMode").value});
  $("glassStrength").onchange = () => saveRoomProfile({glass:$("glassStrength").value});
  $("effectsLevel").onchange = () => saveRoomProfile({effects:$("effectsLevel").value});
  for (const [id, field] of [["backgroundDim", "dim"], ["backgroundBlur", "blur"]]) {
    $(id).oninput = () => {
      const profile = roomProfile();
      prefDraft.profiles ||= {};
      prefDraft.profiles[tv] = {...profile, [field]:Number($(id).value)};
      $(field === "dim" ? "dimValue" : "blurValue").value = $(id).value + (field === "dim" ? "%" : "px");
      applyRoomBackground();
    };
    $(id).onchange = queuePreferencesSave;
  }
  $("backgroundUpload").onchange = async () => {
    const file = $("backgroundUpload").files[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) return toast("Choose an image under 5 MB");
    const form = new FormData(); form.append("image", file);
    try {
      const response = await fetch(`/api/background/${encodeURIComponent(tv)}`, {method:"POST",headers:{"X-CSRF-Token":csrf},body:form});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Image upload failed");
      localStorage.setItem(`backgroundVersion:${tv}`, String(Date.now()));
      saveRoomProfile({background:"custom"});
      toast("Room background saved");
    } catch (error) { toast(error.message); }
    $("backgroundUpload").value = "";
  };
  $("exportBackup").onclick = async () => {
    const password = $("backupPassword").value;
    if (password.length < 12) return toast("Use at least 12 characters for the backup password");
    $("backupMessage").textContent = "Preparing encrypted backup…";
    try {
      const response = await fetch("/api/backup/export", {method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},body:JSON.stringify({password})});
      if (!response.ok) throw new Error((await response.json()).error || "Backup failed");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = "ctrl-freak.cfbackup"; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      $("backupMessage").textContent = "Backup downloaded. Keep its password safe.";
    } catch (error) { $("backupMessage").textContent = error.message; }
  };
  $("restoreBackup").onclick = async () => {
    const file = $("restoreFile").files[0];
    const password = $("backupPassword").value;
    if (!file || !password) return toast("Choose a backup and enter its password");
    if (!confirm("Restore paired TVs and settings from this backup? Existing downloads will stay in place.")) return;
    const form = new FormData(); form.append("backup", file); form.append("password", password);
    $("backupMessage").textContent = "Restoring backup…";
    try {
      const response = await fetch("/api/backup/restore", {method:"POST",headers:{"X-CSRF-Token":csrf},body:form});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Restore failed");
      $("backupMessage").textContent = `${result.tvs} TVs restored. Reloading…`;
      setTimeout(() => location.reload(), 900);
    } catch (error) { $("backupMessage").textContent = error.message; }
  };

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
      applyRoomProfile();
      $("haptics").checked = preferences.haptics;
      renderApps();
      renderScenes();
    } catch (error) { console.debug("Preference sync paused", error); }
  }, 30000);

  window.ctrlFreakReady = true;
  await updateStatus();
statusTimer = setInterval(updateStatus, 3000);

updateDeviceInfo();
setInterval(updateDeviceInfo, 30000);
}

init().catch(error => {
  console.error(error);
  toast("Unable to initialize remote");
});
