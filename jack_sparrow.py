"""Opt-in torrent discovery, downloads, and Plex import for Ctrl Freak."""

import json
import hashlib
import email.utils
import os
import re
import shutil
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


QUALITY = ("any", "480p", "720p", "1080p", "2160p")
MEDIA = ("movie", "series", "music", "other")
VIDEO = {".mp4", ".mkv", ".m4v", ".avi", ".mov", ".webm"}
AUDIO = {".mp3", ".flac", ".m4a", ".aac", ".ogg"}
MAX_FEEDS = 12
LOCK = threading.RLock()


def clean_title(value):
    return re.sub(r"[^\w .()\[\]-]", "", value, flags=re.UNICODE).strip(" .")[:120] or "Untitled"


def quality_of(title):
    lower = title.lower()
    if re.search(r"\b(2160p|4k|uhd)\b", lower):
        return "2160p"
    for quality in ("1080p", "720p", "480p"):
        if quality in lower:
            return quality
    return "unknown"


def language_of(title, metadata=None):
    label = metadata or ""
    if isinstance(label, list):
        label = " ".join(str(x) for x in label)
    text = (str(label) + " " + title).casefold()
    if re.search(r"\b(english|eng)\b|\[(en)\]", text) or str(label).casefold() in ("en", "en-us", "en-gb"):
        return "english"
    if re.search(r"\b(french|spanish|hindi|german|italian|japanese|korean|russian|arabic|portuguese)\b", text):
        return "other"
    return "unknown"


def category_of(title, metadata=None):
    text = (str(metadata or "") + " " + title).casefold()
    if re.search(r"\bs\d{1,2}e\d{1,2}\b|\bseason\s+\d+\b|\b(tv|television|series)\b", text):
        return "series"
    if re.search(r"\b(music|audio|album)\b", text):
        return "music"
    return "movie"


def _json_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "CtrlFreak/3.4"})
    with urllib.request.urlopen(req, timeout=8) as response:
        if int(response.headers.get("Content-Length", "0")) > 2_000_000:
            raise ValueError("Source response is too large")
        return json.loads(response.read(2_000_001))


def _xml_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "CtrlFreak/3.4"})
    with urllib.request.urlopen(req, timeout=8) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError("Feed is too large")
    return ET.fromstring(data)


class JackSparrow:
    def __init__(self, root, rpc_url="http://127.0.0.1:9091/transmission/rpc",
                 media_root="/media/carlvinjerry/Views"):
        self.root = Path(root)
        self.state_path = self.root / ".jack_sparrow.json"
        self.rpc_url = rpc_url
        self.media_root = Path(media_root)
        self.staging = self.media_root / ".jack-sparrow-downloads"
        self.jobs_lock = threading.Lock()

    def read(self):
        with LOCK:
            if not self.state_path.exists():
                return {"feeds": [], "tracked": [], "jobs": {}, "matches": [], "completed": []}
            data = json.loads(self.state_path.read_text())
            return {"feeds": data.get("feeds", []), "tracked": data.get("tracked", []),
                    "jobs": data.get("jobs", {}), "matches": data.get("matches", []),
                    "completed": data.get("completed", [])}

    def save(self, data):
        with LOCK:
            temp = self.state_path.with_name(self.state_path.name + ".new")
            temp.write_text(json.dumps(data, indent=2))
            os.chmod(temp, 0o600)
            os.replace(temp, self.state_path)

    def rpc(self, method, arguments=None):
        body = json.dumps({"method": method, "arguments": arguments or {}}).encode()
        headers = {"Content-Type": "application/json"}
        for attempt in range(2):
            req = urllib.request.Request(self.rpc_url, data=body, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=8) as response:
                    result = json.load(response)
                if result.get("result") != "success":
                    raise RuntimeError(result.get("result", "Torrent client error"))
                return result.get("arguments", {})
            except urllib.error.HTTPError as exc:
                if exc.code != 409 or attempt:
                    raise
                headers["X-Transmission-Session-Id"] = exc.headers["X-Transmission-Session-Id"]
        raise RuntimeError("Torrent client unavailable")

    def _feed_items(self, url):
        if not url.startswith(("https://", "http://")):
            raise ValueError("Feed must be an HTTP or HTTPS URL")
        root = _xml_url(url)
        items = root.findall(".//item") or root.findall(".//{*}entry")
        results = []
        for item in items[:60]:
            title = item.findtext("title") or item.findtext("{*}title") or "Untitled"
            language = ""
            category = ""
            published = ""
            links = []
            for node in item.iter():
                tag = node.tag.rsplit("}", 1)[-1].lower()
                if tag in ("link", "enclosure"):
                    links.extend([node.get("url", ""), node.get("href", ""), node.text or ""])
                elif tag == "language":
                    language = node.text or ""
                elif tag == "category":
                    category = node.text or node.get("term", "")
                elif tag in ("pubdate", "published", "updated"):
                    published = node.text or ""
            link = next((x.strip() for x in links if x and x.strip().startswith("magnet:?")), None)
            if link is None:
                link = next((x.strip() for x in links if x and x.strip().startswith(("https://", "http://"))
                             and ".torrent" in x.lower().split("?")[0]), None)
            if link:
                seeders = None
                for node in item.iter():
                    if node.tag.rsplit("}", 1)[-1].lower() in ("seeders", "seeds"):
                        try:
                            seeders = max(0, int(node.text or "0"))
                        except ValueError:
                            pass
                results.append({"title": title[:180], "source": "feed", "url": link,
                                "quality": quality_of(title), "seeders": seeders,
                                "language": language_of(title, language),
                                "category": category_of(title, category),
                                "date": self._date(published)})
        return results

    @staticmethod
    def _date(value):
        try:
            return email.utils.parsedate_to_datetime(value).date().isoformat()
        except (TypeError, ValueError, IndexError):
            return value[:10] if value.startswith("20") else ""

    def archive(self, query="", rows=30):
        term = re.sub(r"[^\w .-]", " ", query).strip()[:80]
        archive_query = '(collection:prelinger OR collection:feature_films) AND mediatype:movies'
        if term:
            archive_query += " AND title:(" + term + ")"
        params = urllib.parse.urlencode({"q": archive_query, "fl[]": ["identifier", "title", "publicdate", "language"],
                                         "sort[]": "publicdate desc", "rows": min(rows, 40), "output": "json"},
                                        doseq=True)
        docs = _json_url("https://archive.org/advancedsearch.php?" + params)["response"]["docs"]
        return [{"title": str(doc.get("title", doc["identifier"]))[:180],
                 "source": "archive", "id": doc["identifier"], "quality": "varies",
                 "seeders": None, "date": doc.get("publicdate", "")[:10],
                 "language": language_of(str(doc.get("title", "")), doc.get("language")),
                 "category": "movie"} for doc in docs]

    def discover(self, query="", quality="any", source="all"):
        if quality not in QUALITY or source not in ("all", "archive", "feeds"):
            raise ValueError("Invalid search filter")
        state = self.read()
        results = []
        errors = []
        if source in ("all", "archive"):
            try:
                results.extend(self.archive(query))
            except (OSError, ValueError, KeyError, TimeoutError) as exc:
                errors.append("Archive search unavailable")
        if source in ("all", "feeds"):
            for feed in state["feeds"]:
                try:
                    results.extend(self._feed_items(feed))
                except (OSError, ValueError, ET.ParseError, TimeoutError):
                    errors.append("Could not load a saved feed")
        if query:
            results = [x for x in results if query.casefold() in x["title"].casefold()]
        if quality != "any":
            results = [x for x in results if x["quality"] in (quality, "varies", "unknown")]
        results.sort(key=lambda x: (x.get("language") == "english",
                                    x.get("seeders") is not None, x.get("seeders") or 0), reverse=True)
        return {"results": results[:100], "errors": errors}

    def add_feed(self, url):
        url = url.strip()
        if len(url) > 500 or not url.startswith(("https://", "http://")):
            raise ValueError("Enter an HTTP or HTTPS RSS feed URL")
        self._feed_items(url)  # Validate before saving.
        with LOCK:
            state = self.read()
            if url not in state["feeds"]:
                if len(state["feeds"]) >= MAX_FEEDS:
                    raise ValueError("Too many feeds")
                state["feeds"].append(url)
                self.save(state)
        return self.read()["feeds"]

    def track(self, title, category):
        title = title.strip()
        if not 2 <= len(title) <= 100 or category not in MEDIA:
            raise ValueError("Choose a title and media type")
        with LOCK:
            state = self.read()
            entry = {"title": title, "category": category}
            if entry not in state["tracked"]:
                if len(state["tracked"]) >= 50:
                    raise ValueError("Too many tracked titles")
                state["tracked"].append(entry)
                self.save(state)
        return self.read()["tracked"]

    def remove_feed(self, url):
        with LOCK:
            state = self.read()
            state["feeds"] = [item for item in state["feeds"] if item != url]
            self.save(state)
        return state["feeds"]

    def untrack(self, title, category):
        with LOCK:
            state = self.read()
            state["tracked"] = [item for item in state["tracked"]
                                if item != {"title": title, "category": category}]
            state["matches"] = [item for item in state["matches"]
                                if item.get("tracked_title") != title]
            self.save(state)
        return state["tracked"]

    def refresh_matches(self):
        state = self.read()
        if not state["tracked"]:
            return state["matches"]
        results = self.discover("", "any", "all")["results"]
        with LOCK:
            state = self.read()
            known = {item["key"] for item in state["matches"]}
            for item in results:
                for tracked in state["tracked"]:
                    if tracked["title"].casefold() not in item["title"].casefold():
                        continue
                    identity = item.get("url") or item.get("id") or item["title"]
                    key = hashlib.sha256((tracked["title"] + "|" + identity).encode()).hexdigest()[:24]
                    if key not in known:
                        state["matches"].insert(0, {**item, "key": key,
                                                    "tracked_title": tracked["title"],
                                                    "found_at": int(time.time()), "seen": False})
                        known.add(key)
            state["matches"] = state["matches"][:50]
            self.save(state)
            return state["matches"]

    def mark_match_seen(self, key):
        with LOCK:
            state = self.read()
            match = next((x for x in state["matches"] if x["key"] == key), None)
            if match is None:
                raise ValueError("Unknown tracked match")
            match["seen"] = True
            self.save(state)

    def manage(self, job_id, action):
        if action not in ("pause", "resume", "remove"):
            raise ValueError("Unknown download action")
        with LOCK:
            state = self.read()
            if str(job_id) not in state["jobs"]:
                raise ValueError("Unknown download")
            method = {"pause": "torrent-stop", "resume": "torrent-start", "remove": "torrent-remove"}[action]
            arguments = {"ids": [int(job_id)]}
            if action == "remove":
                arguments["delete-local-data"] = False
            self.rpc(method, arguments)
            if action == "remove":
                del state["jobs"][str(job_id)]
                self.save(state)

    def add(self, source, category, quality, title=None, identifier=None, url=None):
        if category not in MEDIA or quality not in QUALITY:
            raise ValueError("Choose media type and quality")
        wanted_names = []
        if source == "archive":
            if not identifier or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", identifier):
                raise ValueError("Invalid archive item")
            metadata = _json_url("https://archive.org/metadata/" + identifier)
            torrents = [x for x in metadata.get("files", []) if x.get("name", "").endswith(".torrent")]
            if not torrents:
                raise ValueError("No torrent is available for this item")
            if quality != "any":
                heights = {"480p": 480, "720p": 720, "1080p": 1080, "2160p": 2160}
                videos = [x for x in metadata.get("files", []) if Path(x.get("name", "")).suffix.lower() in VIDEO]
                wanted_names = [x["name"] for x in videos if
                                abs(int(x.get("height") or 0) - heights[quality]) <= 80 or
                                quality_of(x.get("name", "")) == quality]
                if not wanted_names:
                    raise ValueError("That quality is not listed for this Archive item")
            filename = urllib.parse.quote(torrents[0]["name"])
            url = "https://archive.org/download/" + identifier + "/" + filename
            title = metadata.get("metadata", {}).get("title", identifier)
        elif source != "feed" and source != "magnet":
            raise ValueError("Unknown source")
        if not url or not (url.startswith("magnet:?xt=urn:btih:") or
                           (url.startswith(("https://", "http://")) and ".torrent" in url.lower().split("?")[0])):
            raise ValueError("Enter a magnet or torrent URL")
        if not title:
            title = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("dn", ["Untitled"])[0]
        known_quality = quality_of(str(title))
        if quality != "any" and source != "archive" and known_quality not in (quality, "unknown"):
            raise ValueError("This result is marked as " + known_quality + ", not " + quality)
        title = clean_title(str(title))
        self.staging.mkdir(parents=True, exist_ok=True)
        result = self.rpc("torrent-add", {"filename": url, "download-dir": str(self.staging), "paused": False})
        if result.get("torrent-duplicate"):
            raise ValueError("This torrent is already in the download queue")
        torrent = result.get("torrent-added")
        if not torrent:
            raise RuntimeError("Torrent was not added")
        job_id = str(torrent["id"])
        with LOCK:
            state = self.read()
            state["jobs"][job_id] = {"title": title, "category": category, "quality": quality,
                                      "source": source, "imported": False,
                                      "wanted_names": wanted_names, "selected": False,
                                      "source_ref": identifier if source == "archive" else url}
            self.save(state)
        return job_id

    def jobs(self):
        with self.jobs_lock:
            return self._jobs()

    def _jobs(self):
        state = self.read()
        if not state["jobs"]:
            return []
        torrents = self.rpc("torrent-get", {"ids": [int(x) for x in state["jobs"]],
                                             "fields": ["id", "name", "percentDone", "status", "rateDownload",
                                                        "peersConnected", "peersSendingToUs", "eta", "errorString",
                                                        "downloadDir", "files"]}).get("torrents", [])
        items = []
        for torrent in torrents:
            job = state["jobs"].get(str(torrent["id"]))
            if not job:
                continue
            if not job.get("selected") and torrent.get("files"):
                eligible = []
                extensions = AUDIO if job["category"] == "music" else VIDEO
                for index, entry in enumerate(torrent["files"]):
                    name = entry["name"]
                    matches = (job["quality"] == "any" or
                               Path(name).name in {Path(x).name for x in job.get("wanted_names", [])} or
                               quality_of(name) == job["quality"])
                    if matches and Path(name).suffix.lower() in extensions:
                        eligible.append(index)
                english = [index for index in eligible if
                           language_of(torrent["files"][index]["name"]) == "english"]
                wanted = english or eligible
                unwanted = [index for index in range(len(torrent["files"])) if index not in wanted]
                if wanted:
                    self.rpc("torrent-set", {"ids": [torrent["id"]], "files-wanted": wanted,
                                             "files-unwanted": unwanted})
                    job["selected"] = True
                elif job["source"] == "archive":
                    self.rpc("torrent-stop", {"ids": [torrent["id"]]})
                    job["error"] = "Requested quality was not found in the torrent"
                else:
                    job["selected"] = True  # A feed title may describe quality without per-file labels.
                self.save(state)
            if torrent.get("percentDone") == 1 and not job["imported"]:
                try:
                    self._import(torrent, job)
                    job["imported"] = True
                    job.pop("error", None)
                    if not any(x.get("source_ref") == job.get("source_ref") and
                               x.get("title") == job["title"] for x in state["completed"]):
                        state["completed"].insert(0, {"title": job["title"],
                                                      "category": job["category"],
                                                      "source_ref": job.get("source_ref"),
                                                      "completed_at": int(time.time())})
                        state["completed"] = state["completed"][:300]
                except (OSError, ValueError, RuntimeError) as exc:
                    job["error"] = str(exc)[:160]
                self.save(state)
            items.append({"id": torrent["id"], **job, "progress": torrent.get("percentDone", 0),
                          "status": torrent.get("status", 0),
                          "rate": torrent.get("rateDownload", 0), "peers": torrent.get("peersConnected", 0),
                          "eta": torrent.get("eta", -1), "error": job.get("error") or torrent.get("errorString", "")})
        return items

    def _import(self, torrent, job):
        folder = {"movie": "Movies", "series": "Series", "music": "Music", "other": "PersonalVids"}[job["category"]]
        target_root = self.media_root / folder
        if not target_root.is_dir():
            raise RuntimeError("Plex destination is unavailable")
        source_root = Path(torrent["downloadDir"]).resolve()
        if source_root != self.staging.resolve():
            raise RuntimeError("Unexpected download directory")
        extensions = AUDIO if job["category"] == "music" else VIDEO
        files = []
        for entry in torrent.get("files", []):
            path = (source_root / entry["name"]).resolve()
            if not path.is_relative_to(source_root) or path.suffix.lower() not in extensions:
                continue
            if job.get("wanted_names") and Path(entry["name"]).name not in {
                    Path(x).name for x in job["wanted_names"]}:
                continue
            if path.is_file() and path.stat().st_size >= 1_000_000:
                files.append(path)
        english_files = [path for path in files if language_of(path.name) == "english"]
        if english_files:
            files = english_files
        if not files:
            raise RuntimeError("No compatible media file found for Plex")
        title = clean_title(job["title"].replace(".", " "))
        episode = re.search(r"(?i)^(.*?)\s*[._ -]*S(\d{1,2})E(\d{1,2})", title)
        if job["category"] == "series" and episode:
            show = clean_title(episode.group(1))
            title_dir = target_root / show / ("Season " + episode.group(2).zfill(2))
        else:
            if job["category"] == "movie":
                match = re.search(r"^(.*?)\s+\(?((?:19|20)\d{2})\)?(?:\s|$)", title)
                if match:
                    title = clean_title(match.group(1)) + " (" + match.group(2) + ")"
            title_dir = target_root / title
        title_dir.mkdir(parents=True, exist_ok=True)
        for path in files:
            destination = title_dir / clean_title(path.name)
            if destination.exists():
                continue
            try:
                os.link(path, destination)
            except OSError:
                shutil.copy2(path, destination)
