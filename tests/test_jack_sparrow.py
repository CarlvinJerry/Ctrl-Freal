import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from jack_sparrow import JackSparrow, clean_title, quality_of, language_of
import xml.etree.ElementTree as ET


class JackSparrowTest(unittest.TestCase):
    def test_title_and_quality(self):
        self.assertEqual(clean_title("Movie: Name/2026"), "Movie Name2026")
        self.assertEqual(quality_of("Show.S01E02.1080p.WEB"), "1080p")
        self.assertEqual(language_of("Film.English.1080p"), "english")
        self.assertEqual(language_of("Film.French.1080p"), "other")

    def test_feed_language_and_type(self):
        xml = ET.fromstring('<rss><channel><item><title>Example Show S01E02 English 720p</title><link>magnet:?xt=urn:btih:abc</link><language>en</language><seeders>12</seeders></item></channel></rss>')
        with tempfile.TemporaryDirectory() as temp:
            jack = JackSparrow(temp)
            with patch('jack_sparrow._xml_url', return_value=xml):
                item = jack._feed_items('https://example.org/feed')[0]
            self.assertEqual((item['language'], item['category'], item['seeders']),
                             ('english', 'series', 12))

    def test_english_file_is_selected_when_variants_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            jack = JackSparrow(temp)
            jack.save({'feeds': [], 'tracked': [], 'jobs': {'7': {
                'title': 'Film', 'category': 'movie', 'quality': 'any',
                'source': 'feed', 'imported': False, 'wanted_names': [], 'selected': False}}})
            torrent = {'id': 7, 'name': 'Film', 'percentDone': 0, 'status': 4,
                       'downloadDir': str(jack.staging), 'files': [
                           {'name': 'Film.French.mkv'}, {'name': 'Film.English.mkv'},
                           {'name': 'Film.English.mp3'}]}
            def rpc(method, arguments=None):
                return {'torrents': [torrent]} if method == 'torrent-get' else {}
            with patch.object(jack, 'rpc', side_effect=rpc) as mocked:
                jack.jobs()
            mocked.assert_any_call('torrent-set', {'ids': [7], 'files-wanted': [1],
                                                   'files-unwanted': [0, 2]})

    def test_track_and_feed_persistence(self):
        with tempfile.TemporaryDirectory() as temp:
            jack = JackSparrow(temp)
            jack.track("Example Show", "series")
            jack.track("Example Show", "series")
            self.assertEqual(len(jack.read()["tracked"]), 1)
            with patch.object(jack, "_feed_items", return_value=[]):
                jack.add_feed("https://example.org/rss")
            self.assertEqual(jack.read()["feeds"], ["https://example.org/rss"])
            jack.remove_feed("https://example.org/rss")
            jack.untrack("Example Show", "series")
            self.assertEqual(jack.read()["feeds"], [])
            self.assertEqual(jack.read()["tracked"], [])

    def test_manage_only_known_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            jack = JackSparrow(temp)
            with self.assertRaises(ValueError):
                jack.manage(5, "remove")
            jack.save({"feeds": [], "tracked": [], "jobs": {"5": {"title": "Example"}}})
            with patch.object(jack, "rpc", return_value={}) as rpc:
                jack.manage(5, "pause")
                rpc.assert_called_with("torrent-stop", {"ids": [5]})
                jack.manage(5, "remove")
            self.assertEqual(jack.read()["jobs"], {})

    def test_tracked_release_inbox_is_persistent(self):
        with tempfile.TemporaryDirectory() as temp:
            jack = JackSparrow(temp)
            jack.track("Example Show", "series")
            candidate = {"title": "Example Show S01E03 720p", "source": "feed",
                         "url": "magnet:?xt=urn:btih:example", "quality": "720p", "seeders": 4}
            with patch.object(jack, "discover", return_value={"results": [candidate], "errors": []}):
                jack.refresh_matches()
                jack.refresh_matches()
            matches = jack.read()["matches"]
            self.assertEqual(len(matches), 1)
            self.assertFalse(matches[0]["seen"])
            jack.mark_match_seen(matches[0]["key"])
            self.assertTrue(JackSparrow(temp).read()["matches"][0]["seen"])

    def test_completed_history_survives_queue_removal(self):
        with tempfile.TemporaryDirectory() as temp:
            jack = JackSparrow(temp)
            jack.save({'feeds': [], 'tracked': [], 'jobs': {'7': {
                'title': 'Example Show S01E01', 'category': 'series', 'quality': 'any',
                'source': 'feed', 'source_ref': 'magnet:?xt=urn:btih:abc',
                'imported': False, 'selected': True}}})
            torrent = {'id': 7, 'name': 'Example Show', 'percentDone': 1, 'status': 6,
                       'rateDownload': 0, 'peersConnected': 0, 'eta': -1,
                       'downloadDir': str(jack.staging), 'files': []}
            with patch.object(jack, 'rpc', return_value={'torrents': [torrent]}), \
                 patch.object(jack, '_import'):
                jack.jobs()
            self.assertEqual(len(jack.read()['completed']), 1)
            with patch.object(jack, 'rpc', return_value={}):
                jack.manage(7, 'remove')
            self.assertEqual(len(jack.read()['completed']), 1)

    def test_import_completed_episode_into_plex_season(self):
        with tempfile.TemporaryDirectory() as temp:
            media = Path(temp)
            source = media / ".jack-sparrow-downloads"
            source.mkdir()
            file = source / "Example.Show.S01E02.1080p.mkv"
            file.write_bytes(b"x" * 1_000_000)
            (media / "Series").mkdir()
            jack = JackSparrow(temp, media_root=temp)
            torrent = {"downloadDir": str(source), "files": [{"name": file.name}]}
            jack._import(torrent, {"title": "Example.Show.S01E02.1080p", "category": "series"})
            self.assertTrue((media / "Series" / "Example Show" / "Season 01" / file.name).exists())

    def test_import_movie_uses_plex_year_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            media = Path(temp)
            source = media / ".jack-sparrow-downloads"
            source.mkdir()
            file = source / "Example.Movie.2026.1080p.mkv"
            file.write_bytes(b"x" * 1_000_000)
            (media / "Movies").mkdir()
            JackSparrow(temp, media_root=temp)._import(
                {"downloadDir": str(source), "files": [{"name": file.name}]},
                {"title": "Example.Movie.2026.1080p", "category": "movie"})
            self.assertTrue((media / "Movies" / "Example Movie (2026)" / file.name).exists())

    def test_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            media = Path(temp)
            (media / ".jack-sparrow-downloads").mkdir()
            (media / "Movies").mkdir()
            jack = JackSparrow(temp, media_root=temp)
            with self.assertRaises(RuntimeError):
                jack._import({"downloadDir": str(media / ".jack-sparrow-downloads"),
                              "files": [{"name": "../evil.mp4"}]},
                             {"title": "Example", "category": "movie"})


if __name__ == "__main__":
    unittest.main()
