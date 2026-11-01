import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from plex_extras import PlexExtras


class PlexExtrasTest(unittest.TestCase):
    def test_continue_watching_and_search_use_local_library(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "plex.db"
            with closing(sqlite3.connect(db_path)) as db:
                db.execute('CREATE TABLE metadata_items (id INTEGER,title TEXT,metadata_type INTEGER,duration INTEGER,guid TEXT,parent_id INTEGER,"index" INTEGER,deleted_at INTEGER,hash TEXT,user_thumb_url TEXT)')
                db.execute('CREATE TABLE metadata_item_settings (guid TEXT,view_offset INTEGER,last_viewed_at INTEGER)')
                db.execute('INSERT INTO metadata_items VALUES (1,?,?,?,?,?,?,?,?,?)',
                           ('Sample Movie', 1, 1000, 'movie:1', None, None, None, None, None))
                db.execute('INSERT INTO metadata_item_settings VALUES (?,?,?)', ('movie:1', 500, 100))
                db.commit()
            plex = PlexExtras(temp, db_path=db_path)
            self.assertEqual(plex.continue_watching()[0]['progress'], 50)
            self.assertEqual(plex.search('sample')[0]['title'], 'Sample Movie')

    def test_poster_rejects_invalid_local_path(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "plex.db"
            with closing(sqlite3.connect(db_path)) as db:
                db.execute('CREATE TABLE metadata_items (id INTEGER,metadata_type INTEGER,deleted_at INTEGER,hash TEXT,user_thumb_url TEXT)')
                db.execute('INSERT INTO metadata_items VALUES (1,1,NULL,?,?)',
                           ('a' * 40, 'metadata://posters/../private'))
                db.commit()
            plex = PlexExtras(temp, db_path=db_path)
            self.assertIsNone(plex.poster('movie', 1))


if __name__ == '__main__':
    unittest.main()
