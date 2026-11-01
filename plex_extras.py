"""Read-only Plex catalog and small cached artwork for the phone UI."""

import os
import re
import sqlite3
import subprocess
import threading
from contextlib import closing
from pathlib import Path

PLEX_ROOT = Path("/var/lib/plexmediaserver/Library/Application Support/Plex Media Server")
PLEX_DB = PLEX_ROOT / "Plug-in Support/Databases/com.plexapp.plugins.library.db"
POSTER_LOCK = threading.Lock()


def normalized(value):
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


class PlexExtras:
    def __init__(self, app_root, db_path=PLEX_DB, metadata_root=None):
        self.db_path = Path(db_path)
        self.metadata_root = Path(metadata_root) if metadata_root else PLEX_ROOT / "Metadata"
        self.cache = Path(app_root) / ".posters"

    def query(self, statement, args=()):
        if not self.db_path.is_file():
            return []
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=3)) as db:
            db.execute("PRAGMA query_only=ON")
            return db.execute(statement, args).fetchall()

    def catalog(self):
        rows = self.query("SELECT id,title,metadata_type FROM metadata_items WHERE metadata_type IN (1,2) AND deleted_at IS NULL")
        return [{"id": ident, "title": title, "kind": "movie" if kind == 1 else "series"}
                for ident, title, kind in rows]

    def artwork_for(self, title, category, catalog=None):
        if category not in ("movie", "series"):
            return None
        target = normalized(title)
        matches = sorted((x for x in (catalog if catalog is not None else self.catalog())
                          if x["kind"] == category), key=lambda x: len(x["title"]), reverse=True)
        for item in matches:
            base = normalized(item["title"])
            if target == base or target.startswith(base + " "):
                return f"/api/plex/poster/{category}/{item['id']}"
        return None

    def decorate(self, items):
        try:
            catalog = self.catalog()
        except (OSError, sqlite3.Error):
            catalog = []
        decorated = []
        for item in items:
            artwork = self.artwork_for(item.get("title", ""), item.get("category", ""), catalog)
            if not artwork and item.get("source") == "archive" and item.get("id"):
                artwork = "https://archive.org/services/img/" + item["id"]
            decorated.append({**item, "artwork": artwork})
        return decorated

    def continue_watching(self):
        rows = self.query('''SELECT m.id,m.title,m.metadata_type,m.duration,s.view_offset,
                            show.id,show.title,season."index",m."index"
                            FROM metadata_item_settings s
                            JOIN metadata_items m ON s.guid=m.guid
                            LEFT JOIN metadata_items season ON m.parent_id=season.id AND m.metadata_type=4
                            LEFT JOIN metadata_items show ON season.parent_id=show.id
                            WHERE s.view_offset>0 AND m.metadata_type IN (1,4) AND m.deleted_at IS NULL
                              AND (m.duration IS NULL OR s.view_offset<m.duration*0.95)
                            ORDER BY s.last_viewed_at DESC LIMIT 20''')
        results = []
        seen = set()
        for ident, title, kind, duration, offset, show_id, show_title, season, episode in rows:
            if ident in seen:
                continue
            seen.add(ident)
            label = (f"{show_title} · S{season:02d}E{episode:02d}" if kind == 4 and
                     show_title and season is not None and episode is not None else title)
            poster_kind, poster_id = ("series", show_id) if kind == 4 and show_id else ("movie", ident)
            results.append({"title": label, "progress": round(100 * offset / duration) if duration else 0,
                            "artwork": f"/api/plex/poster/{poster_kind}/{poster_id}"})
            if len(results) >= 12:
                break
        return results

    def search(self, query):
        term = normalized(query)
        if len(term) < 2:
            return []
        return [{**item, "artwork": f"/api/plex/poster/{item['kind']}/{item['id']}"}
                for item in self.catalog() if term in normalized(item["title"])][:24]

    def poster(self, kind, ident):
        metadata_type = {"movie": 1, "series": 2}.get(kind)
        if not metadata_type:
            return None
        rows = self.query("SELECT hash,user_thumb_url FROM metadata_items WHERE id=? AND metadata_type=? AND deleted_at IS NULL",
                          (ident, metadata_type))
        if not rows:
            return None
        digest, thumb = rows[0]
        if not digest or not re.fullmatch(r"[0-9a-f]{40}", digest):
            return None
        if not thumb or not thumb.startswith("metadata://posters/"):
            return None
        filename = thumb.split("/", 3)[-1]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
            return None
        folder = "Movies" if kind == "movie" else "TV Shows"
        source = (self.metadata_root / folder / digest[0] / (digest[1:] + ".bundle") /
                  "Contents/_combined/posters" / filename)
        if not source.is_file() or source.stat().st_size > 15_000_000:
            return None
        target = self.cache / f"{kind}-{ident}.jpg"
        if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
            return target
        with POSTER_LOCK:
            if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.stem + ".new.jpg")
            try:
                subprocess.run(["ffmpeg", "-loglevel", "error", "-nostdin", "-y", "-i", str(source),
                                "-vf", "scale=240:-2", "-frames:v", "1", "-q:v", "5", str(temp)],
                               capture_output=True, timeout=12, check=True)
                os.replace(temp, target)
            except (OSError, subprocess.SubprocessError):
                temp.unlink(missing_ok=True)
                return None
        return target
