import importlib.util
from http.client import IncompleteRead
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("archive", ROOT / "scripts/archive.py")
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
BLOG = CONFIG["BLOG_URL"]
SAMPLE = (ROOT / "tests/fixtures/sample.xml").read_bytes()


class ArchiveTests(unittest.TestCase):
    def test_feed_metadata_deduplication_and_missing_date(self):
        posts = archive.parse_feed(SAMPLE, BLOG)
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0]["published_at"], "2026-10-29T04:41:24+00:00")
        self.assertEqual(posts[0]["category"], "개발 & 기록")
        self.assertIsNone(posts[1]["published_at"])
        self.assertIsNone(posts[1]["category"])
        self.assertNotIn("description", posts[0])

    def test_url_validation(self):
        for url in ["javascript:alert(1)", "https://evil.test/123", "https://blog.naver.com.evil.test/dowoolog/1",
                    "https://evil@blog.naver.com/dowoolog/1", "https://blog.naver.com:123/dowoolog/1",
                    "https://blog.naver.com/dowoolog/1\n", "//blog.naver.com/dowoolog/1",
                    "https://blog.naver.com/other/1", "https://blog.naver.com/dowoolog/abc"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                archive.post_url(url, BLOG)
        self.assertEqual(archive.post_url("http://m.blog.naver.com/dowoolog/123?tracking=1#x", BLOG),
                         "https://blog.naver.com/dowoolog/123")

    def test_invalid_dates_are_not_invented(self):
        for value in [None, "", "not a date", "Thu, 29 Oct 2026 13:41:24"]:
            self.assertIsNone(archive.parse_date(value))

    def test_old_posts_survive_and_existing_metadata_does_not_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "posts.json"
            archive.collect(CONFIG, path, lambda _: SAMPLE)
            original = path.read_bytes()
            modified = SAMPLE.replace(b"alert(1)", b"changed title")
            self.assertTrue(archive.collect(CONFIG, path, lambda _: modified))
            self.assertEqual(path.read_bytes(), original)
            new = b'<rss><channel><item><title>new</title><link>https://blog.naver.com/dowoolog/125</link></item></channel></rss>'
            archive.collect(CONFIG, path, lambda _: new)
            self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))), 3)

    def test_failures_preserve_file_byte_for_byte(self):
        errors = [URLError("offline"), TimeoutError("timeout"), HTTPError("url", 503, "unavailable", {}, None),
                  IncompleteRead(b"partial response")]
        payloads = [b"invalid xml", b"<html/>", b"<rss><channel/></rss>", b"x" * (archive.MAX_FEED_BYTES + 1),
                    b'<rss><channel><item><title>x</title><link>https://evil.test/1</link></item></channel></rss>']
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "posts.json"
            archive.collect(CONFIG, path, lambda _: SAMPLE)
            original = path.read_bytes()
            for error in errors:
                def fail(_):
                    raise error
                self.assertFalse(archive.collect(CONFIG, path, fail))
                self.assertEqual(path.read_bytes(), original)
            for payload in payloads:
                self.assertFalse(archive.collect(CONFIG, path, lambda _: payload))
                self.assertEqual(path.read_bytes(), original)

    def test_bad_storage_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "posts.json"
            for content in ["broken", "{}", '[{"title": "x"}]']:
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    archive.collect(CONFIG, path, lambda _: SAMPLE)
                self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_dtd_rejected_in_utf8_and_utf16(self):
        xml = '<!DOCTYPE rss [<!ENTITY x "hello">]><rss><channel/></rss>'
        for encoding in ["utf-8", "utf-16", "utf-32"]:
            with self.assertRaises(ValueError):
                archive.parse_feed(xml.encode(encoding), BLOG)

    def test_identical_save_does_not_touch_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "posts.json"
            posts = archive.parse_feed(SAMPLE, BLOG)
            self.assertTrue(archive.save_posts(path, posts))
            before = path.stat().st_mtime_ns
            self.assertFalse(archive.save_posts(path, posts))
            self.assertEqual(path.stat().st_mtime_ns, before)

    def test_failed_atomic_replace_preserves_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "posts.json"
            archive.save_posts(path, [])
            with patch.object(archive.os, "replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    archive.save_posts(path, archive.parse_feed(SAMPLE, BLOG))
            self.assertEqual(path.read_text(), "[]\n")
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_build_escaping_links_seo_and_sitemap_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            config = {**CONFIG, "SITE_URL": "https://example.com/archive", "GOOGLE_SITE_VERIFICATION": 'a"<b', "NAVER_SITE_VERIFICATION": "naver-token"}
            archive.build(config, archive.parse_feed(SAMPLE, BLOG), output)
            html = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt; &amp;', html)
            self.assertNotIn('<script', html)
            self.assertIn('href="https://blog.naver.com/dowoolog/123"', html)
            self.assertIn('rel="canonical" href="https://example.com/archive/"', html)
            self.assertIn('name="google-site-verification" content="a&quot;&lt;b"', html)
            self.assertIn('name="naver-site-verification" content="naver-token"', html)
            self.assertIn('lang="ko"', html)
            self.assertIn('작성일 미제공', html)
            self.assertNotIn('이 본문은', html)
            sitemap = ET.parse(output / "sitemap.xml")
            self.assertEqual([e.text for e in sitemap.findall('.//{*}loc')], ["https://example.com/archive/"])
            self.assertIn('Sitemap: https://example.com/archive/sitemap.xml', (output / "robots.txt").read_text())
            self.assertTrue((output / ".nojekyll").exists())

    def test_empty_site_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive.build(CONFIG, [], Path(tmp))
            html = (Path(tmp) / "index.html").read_text(encoding="utf-8")
            self.assertIn("아직 수집된 게시글이 없습니다.", html)


if __name__ == "__main__":
    unittest.main()
