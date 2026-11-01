"""Opt-in torrent discovery, downloads, and Plex import for Ctrl Freak."""

import json
import hashlib
import email.utils
import os
import re
import shutil
import sqlite3
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


QUALITY = ("any", "480p", "720p", "1080p", "2160p")
MEDIA = ("movie", "series", "music", "other")
VIDEO = {".mp4", ".mkv", ".m4v", ".avi", ".mov", ".webm"}
AUDIO = {".mp3", ".flac", ".m4a", ".aac", ".ogg"}
MAX_FEEDS = 12
INDEXERS = {
    "thepiratebay": "The Pirate Bay",
    "knaben": "Knaben",
    "torrentproject2": "TorrentProject2",
    "yts": "YTS",
    "limetorrents": "LimeTorrents",
    "linuxtracker": "LinuxTracker",
    "torrentdownloads": "TorrentDownloads",
    "torrentgalaxyclone": "TorrentGalaxyClone",
}
INDEXER_CONFIG = Path("/home/carlvinjerry/tcl-remote/indexer/config/Jackett/ServerConfig.json")
PLEX_DB = Path("/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db")
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


def episode_key(title):
    match = re.search(r"(?i)\bS(\d{1,2})[ ._-]*E(\d{1,2})\b", title)
    return f"S{int(match[1]):02d}E{int(match[2]):02d}" if match else ""


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
        self.indexer_config = INDEXER_CONFIG
        self.plex_db = PLEX_DB

    def read(self):
        with LOCK:
            if not self.state_path.exists():
                return {"feeds": [], "tracked": [], "jobs": {}, "matches": [], "completed": [],
                        "candidate_links": {}, "notices": [], "dismissed": [], "last_plex_check": 0}
            data = json.loads(self.state_path.read_text())
            return {"feeds": data.get("feeds", []), "tracked": data.get("tracked", []),
                    "jobs": data.get("jobs", {}), "matches": data.get("matches", []),
                    "completed": data.get("completed", []),
                    "candidate_links": data.get("candidate_links", {}),
                    "notices": data.get("notices", []), "dismissed": data.get("dismissed", []),
                    "last_plex_check": data.get("last_plex_check", 0)}

    def plex_library(self):
        if not self.plex_db.is_file():
            return {"shows": [], "movies": []}
        uri = self.plex_db.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=3) as db:
            db.execute("PRAGMA query_only=ON")
            shows = db.execute("SELECT id,title FROM metadata_items WHERE metadata_type=2 AND deleted_at IS NULL").fetchall()
            episodes = db.execute("SELECT show.id,season.\"index\",episode.\"index\" FROM metadata_items episode JOIN metadata_items season ON episode.parent_id=season.id JOIN metadata_items show ON season.parent_id=show.id WHERE episode.metadata_type=4 AND episode.deleted_at IS NULL").fetchall()
            movies = db.execute("SELECT movie.title,movie.year,MAX(COALESCE(media.height,0)) FROM metadata_items movie LEFT JOIN media_items media ON media.metadata_item_id=movie.id WHERE movie.metadata_type=1 AND movie.deleted_at IS NULL GROUP BY movie.id").fetchall()
        owned = {}
        for show_id, season, episode in episodes:
            owned.setdefault(show_id, set()).add((season, episode))
        return {"shows": [{"title": title, "episodes": owned.get(show_id, set())} for show_id, title in shows],
                "movies": [{"title": title, "year": year, "height": height} for title, year, height in movies]}

    def refresh_plex_notices(self):
        state = self.read()
        if time.time() - state["last_plex_check"] < 21600:
            return
        library = self.plex_library()
        proposed = []
        today = time.strftime("%Y-%m-%d")
        for show in library["shows"][:30]:
            if not show["episodes"]:
                continue
            latest_owned = max(show["episodes"], default=(0, 0))
            try:
                matches = _json_url("https://api.tvmaze.com/search/shows?q=" + urllib.parse.quote(show["title"]))
                hit = next((x["show"] for x in matches if x["show"]["name"].casefold() == show["title"].casefold()), None)
                if not hit:
                    continue
                episodes = _json_url("https://api.tvmaze.com/shows/" + str(hit["id"]) + "/episodes")
                for episode in episodes:
                    season, number = episode.get("season"), episode.get("number")
                    airdate = episode.get("airdate") or ""
                    if not season or not number or not airdate or airdate > today or (season, number) <= latest_owned:
                        continue
                    key = f"episode:{hit['id']}:{season}:{number}"
                    proposed.append({"key": key, "kind": "episode", "title": show["title"],
                                     "detail": f"S{season:02d}E{number:02d} · {episode.get('name') or 'New episode'} · {airdate}",
                                     "query": f"{show['title']} S{season:02d}E{number:02d}"})
            except (OSError, ValueError, KeyError, TypeError, TimeoutError):
                continue
        for tracked in state["tracked"]:
            if tracked["category"] != "movie":
                continue
            movie = next((x for x in library["movies"] if x["title"].casefold() == tracked["title"].casefold()), None)
            if not movie:
                continue
            try:
                results = self.discover(movie["title"], "any", "indexers")["results"]
            except (OSError, ValueError, TimeoutError):
                continue
            for item in results:
                if item["category"] != "movie" or item["language"] == "other":
                    continue
                title = item["title"].casefold()
                base = movie["title"].casefold()
                if not title.startswith(base):
                    continue
                if movie["year"] and not re.search(r"\b" + str(movie["year"]) + r"\b", title) and not re.search(r"(?:part|chapter)\s*[2-9]|\b(?:ii|iii|iv)\b", title[len(base):len(base)+25]):
                    continue
                height = {"480p": 480, "720p": 720, "1080p": 1080, "2160p": 2160}.get(item["quality"], 0)
                sequel = bool(re.search(r"(?:part|chapter)\s*[2-9]|\b(?:ii|iii|iv)\b", title[len(base):len(base)+25]))
                if not sequel and height <= movie["height"]:
                    continue
                kind = "sequel" if sequel else "upgrade"
                key = kind + ":" + item["id"]
                proposed.append({"key": key, "kind": kind, "title": item["title"],
                                 "detail": ("Possible sequel" if sequel else "Possible quality upgrade") +
                                           f" · {item['quality']} · {item.get('seeders') or 0} seeders",
                                 "query": movie["title"]})
        with LOCK:
            state = self.read()
            known = {x["key"] for x in state["notices"]} | set(state["dismissed"])
            state["notices"] = (state["notices"] + [x for x in proposed if x["key"] not in known])[-100:]
            state["last_plex_check"] = int(time.time())
            self.save(state)

    def dismiss_notice(self, key):
        with LOCK:
            state = self.read()
            if not any(x["key"] == key for x in state["notices"]):
                raise ValueError("Unknown notice")
            state["notices"] = [x for x in state["notices"] if x["key"] != key]
            state["dismissed"].append(key)
            self.save(state)

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

    def indexer_search(self, name, query):
        if name not in INDEXERS or not self.indexer_config.is_file():
            raise ValueError("Indexer unavailable")
        key = json.loads(self.indexer_config.read_text())["APIKey"]
        params = urllib.parse.urlencode({"apikey": key, "t": "search", "q": query[:80]})
        url = ("http://127.0.0.1:9117/api/v2.0/indexers/" + name +
               "/results/torznab/api?" + params)
        with urllib.request.urlopen(url, timeout=18) as response:
            body = response.read(3_000_001)
        if len(body) > 3_000_000:
            raise ValueError("Indexer response too large")
        root = ET.fromstring(body)
        if root.tag == "error":
            raise ValueError("Indexer search failed")
        results = []
        for item in root.findall(".//item")[:60]:
            title = item.findtext("title") or "Untitled"
            attrs = {x.get("name"): x.get("value") for x in item if x.tag.endswith("attr")}
            link = attrs.get("magneturl") or item.findtext("link") or ""
            if not link:
                enclosure = item.find("enclosure")
                link = enclosure.get("url", "") if enclosure is not None else ""
            if not (link.startswith("magnet:?xt=urn:btih:") or
                    link.startswith("http://127.0.0.1:9117/dl/")):
                continue
            identity = attrs.get("infohash") or item.findtext("guid") or link
            ref = hashlib.sha256((name + "|" + identity).encode()).hexdigest()[:32]
            category_code = next((x.text for x in item.findall("category") if x.text), "")
            category = ("series" if category_code.startswith("5") else
                        "music" if category_code.startswith("3") else
                        "movie" if category_code.startswith("2") else category_of(title))
            try:
                seeders = int(attrs.get("seeders") or 0)
            except ValueError:
                seeders = None
            results.append({"title": title[:180], "source": "indexer", "indexer": name,
                            "indexer_name": INDEXERS[name], "id": ref, "_download": link,
                            "quality": quality_of(title), "seeders": seeders,
                            "language": language_of(title), "category": category,
                            "date": self._date(item.findtext("pubDate") or "")})
        return results

    def discover(self, query="", quality="any", source="all"):
        if quality not in QUALITY or source not in ("all", "archive", "feeds", "indexers") and not (
                source.startswith("indexer:") and source.split(":", 1)[1] in INDEXERS):
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
        if source in ("all", "indexers") or source.startswith("indexer:"):
            names = list(INDEXERS) if source in ("all", "indexers") else [source.split(":", 1)[1]]
            with ThreadPoolExecutor(max_workers=4) as pool:
                tasks = {pool.submit(self.indexer_search, name, query): name for name in names}
                for future in as_completed(tasks):
                    name = tasks[future]
                    try:
                        results.extend(future.result())
                    except (OSError, ValueError, ET.ParseError, KeyError, TimeoutError):
                        errors.append(INDEXERS[name] + " unavailable")
            links = {x["id"]: {"url": x.pop("_download"), "title": x["title"],
                                "indexer": x["indexer"], "saved_at": int(time.time())}
                     for x in results if x.get("source") == "indexer" and x.get("_download")}
            if links:
                with LOCK:
                    current = self.read()
                    current["candidate_links"].update(links)
                    current["candidate_links"] = dict(list(current["candidate_links"].items())[-500:])
                    self.save(current)
        if query:
            words = re.findall(r"\w+", query.casefold())
            results = [x for x in results if all(word in x["title"].casefold() for word in words)]
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
        results = self.discover("", "any", "archive")["results"] + self.discover("", "any", "feeds")["results"]
        for tracked in state["tracked"][:12]:
            if tracked["category"] in ("series", "movie"):
                results.extend(self.discover(tracked["title"], "any", "indexers")["results"])
        with LOCK:
            state = self.read()
            known = {item["key"] for item in state["matches"]}
            for item in results:
                for tracked in state["tracked"]:
                    if tracked["title"].casefold() not in item["title"].casefold():
                        continue
                    if self._match_occupied({**item, "tracked_title": tracked["title"]}, state):
                        continue
                    identity = item.get("url") or item.get("id") or item["title"]
                    key = hashlib.sha256((tracked["title"] + "|" + identity).encode()).hexdigest()[:24]
                    if key not in known and key not in state["dismissed"]:
                        state["matches"].insert(0, {**item, "key": key,
                                                    "tracked_title": tracked["title"],
                                                    "found_at": int(time.time()), "seen": False})
                        known.add(key)
            state["matches"] = state["matches"][:50]
            state["matches"] = [x for x in state["matches"] if not self._match_occupied(x, state)]
            self.save(state)
            return state["matches"]

    @staticmethod
    def _occupied_episodes(state):
        names = [x.get("title", "") for x in state["completed"]]
        names.extend(x.get("title", "") for x in state["jobs"].values() if not x.get("deleted"))
        occupied = set()
        for name in names:
            episode = episode_key(name)
            if episode:
                occupied.add((re.split(r"(?i)\bS\d{1,2}[ ._-]*E\d{1,2}\b", name, maxsplit=1)[0]
                              .strip(" ._-").casefold(), episode))
        return occupied

    def _match_occupied(self, match, state):
        episode = episode_key(match["title"])
        if episode:
            show = match["tracked_title"].casefold()
            return any(show in existing_show for existing_show, existing_episode in self._occupied_episodes(state)
                       if existing_episode == episode)
        tracked = match["tracked_title"].casefold()
        if any(x.get("category") == "movie" and
               re.match(r"^" + re.escape(tracked) + r"(?:\b|$)",
                        x.get("title", "").casefold().lstrip(" -._"))
               for x in state["completed"]):
            return True
        return any(x.get("source_ref") == (match.get("id") or match.get("url"))
                   for x in state["completed"]) or any(
                       x.get("source_ref") == (match.get("id") or match.get("url"))
                       for x in state["jobs"].values() if not x.get("deleted"))

    @staticmethod
    def _match_in_plex(match, library):
        episode = episode_key(match["title"])
        tracked = match["tracked_title"].casefold()
        if episode:
            season = int(episode[1:3])
            number = int(episode[4:6])
            return any(show["title"].casefold() == tracked and
                       (season, number) in show["episodes"] for show in library["shows"])
        return any(movie["title"].casefold() == tracked for movie in library["movies"])

    def active_matches(self):
        state = self.read()
        try:
            library = self.plex_library()
        except (OSError, sqlite3.Error):
            library = {"shows": [], "movies": []}
        results = []
        grouped = set()
        def rank(item):
            return ("audio description" not in item["title"].casefold(),
                    item.get("language") == "english", item.get("seeders") or 0,
                    {"2160p": 4, "1080p": 3, "720p": 2, "480p": 1}.get(item.get("quality"), 0))
        for item in sorted(state["matches"], key=rank, reverse=True):
            if item.get("seen") or self._match_occupied(item, state) or self._match_in_plex(item, library):
                continue
            episode = episode_key(item["title"])
            group = (item["tracked_title"].casefold(), episode) if episode else (item["tracked_title"].casefold(), item["key"])
            if group in grouped:
                continue
            grouped.add(group)
            results.append(item)
        return results

    def mark_match_seen(self, key):
        with LOCK:
            state = self.read()
            match = next((x for x in state["matches"] if x["key"] == key), None)
            if match is None:
                raise ValueError("Unknown tracked match")
            match["seen"] = True
            state["dismissed"].append(key)
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
        elif source == "indexer":
            candidate = self.read()["candidate_links"].get(identifier or "")
            if not candidate or time.time() - candidate.get("saved_at", 0) > 86400:
                raise ValueError("Search again to refresh this result")
            url, title = candidate["url"], candidate["title"]
            if not (url.startswith("magnet:?xt=urn:btih:") or
                    url.startswith("http://127.0.0.1:9117/dl/")):
                raise ValueError("Invalid indexer result")
        elif source != "feed" and source != "magnet":
            raise ValueError("Unknown source")
        if not url or not (url.startswith("magnet:?xt=urn:btih:") or
                           (url.startswith(("https://", "http://")) and ".torrent" in url.lower().split("?")[0]) or
                           source == "indexer" and url.startswith("http://127.0.0.1:9117/dl/")):
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
                                      "source_ref": identifier if source in ("archive", "indexer") else url}
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
                    imported_paths = self._import(torrent, job) or []
                    job["imported"] = True
                    job["imported_paths"] = imported_paths
                    job.pop("error", None)
                    if not any(x.get("source_ref") == job.get("source_ref") and
                               x.get("title") == job["title"] for x in state["completed"]):
                        state["completed"].insert(0, {"title": job["title"],
                                                      "category": job["category"],
                                                      "source_ref": job.get("source_ref"),
                                                      "imported_paths": imported_paths,
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
        imported_paths = []
        for path in files:
            destination = title_dir / clean_title(path.name)
            if destination.exists():
                continue
            try:
                os.link(path, destination)
            except OSError:
                shutil.copy2(path, destination)
            imported_paths.append(str(destination))
        return imported_paths

    def delete_imported_movie(self, source_ref):
        with LOCK:
            state = self.read()
            item = next((x for x in state["completed"] if x.get("source_ref") == source_ref and
                         x.get("category") == "movie"), None)
            if not item or not item.get("imported_paths"):
                raise ValueError("No Jack-imported movie files found")
            root = (self.media_root / "Movies").resolve()
            paths = [Path(p) for p in item["imported_paths"]]
            if any(p.is_symlink() or not p.resolve().is_relative_to(root) or
                   not p.resolve().is_file() for p in paths):
                raise ValueError("Movie files have changed; deletion cancelled")
            for path in paths:
                path.unlink()
            state["completed"].remove(item)
            for job in state["jobs"].values():
                if job.get("source_ref") == source_ref:
                    job["imported_paths"] = []
                    job["deleted"] = True
            self.save(state)
            return len(paths)
