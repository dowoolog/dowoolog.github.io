import importlib.util
import contextlib
import io
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
# Fixtures must keep working when someone customizes config.json after a fork.
CONFIG = archive.resolve_config({
    "BLOG_ID": "dowoolog",
    "SITE_NAME": "Test Archive",
    "SITE_URL": "https://example.com",
    "DESCRIPTION": "테스트 아카이브",
})
BLOG = CONFIG["BLOG_URL"]
SAMPLE = (ROOT / "tests/fixtures/sample.xml").read_bytes()


class ArchiveTests(unittest.TestCase):
    def test_collection_time_tracks_success_even_without_new_posts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'posts.json'
            self.assertIsNone(archive.last_collected(path))
            first = archive.datetime(2026, 10, 29, 1, tzinfo=archive.timezone.utc)
            second = first + archive.timedelta(hours=1)
            for instant in [first, second]:
                with patch.object(archive, 'datetime', wraps=archive.datetime) as clock:
                    clock.now.return_value = instant
                    self.assertTrue(archive.collect(CONFIG, path, lambda _: SAMPLE))
                self.assertEqual(archive.last_collected(path), instant)
            saved = path.with_suffix('.meta.json').read_bytes()
            self.assertFalse(archive.collect(CONFIG, path, lambda _: b'<rss><channel/></rss>'))
            self.assertEqual(path.with_suffix('.meta.json').read_bytes(), saved)

    def test_collection_time_header_uses_kst_and_unknown_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            archive.build(CONFIG, [], output)
            self.assertIn('최근 가져온 시간: 기록 없음', (output / 'index.html').read_text(encoding='utf-8'))
            instant = archive.datetime(2026, 10, 29, 16, 30, tzinfo=archive.timezone.utc)
            archive.build(CONFIG, [], output, instant)
            html = (output / 'index.html').read_text(encoding='utf-8')
            self.assertIn('2026-10-30 01:30 KST', html)
            self.assertIn('<p>최신순</p>', html)
            self.assertNotIn('날짜는 한국 시간 기준', html)
            self.assertNotIn('BLOG ARCHIVE', html)

    def test_validate_reports_bad_config_without_exposing_values(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(archive, 'ROOT', Path(tmp)):
            path = Path(tmp) / 'config.json'
            path.write_text('{"BLOG_ID": "test"\n"TOKEN": "private-value"}', encoding='utf-8')
            errors = io.StringIO()
            with patch('sys.argv', ['archive.py', 'validate']), contextlib.redirect_stderr(errors):
                with self.assertRaises(SystemExit) as result:
                    archive.main()
            self.assertEqual(result.exception.code, 2)
            self.assertIn('config.json:2:1', errors.getvalue())
            self.assertNotIn('private-value', errors.getvalue())
            path.write_text('{"BLOG_ID": "test"}', encoding='utf-8')
            with patch('sys.argv', ['archive.py', 'validate']):
                self.assertEqual(archive.main(), 0)

    def test_excerpt_plain_text_and_length(self):
        text = '<p>안녕 <b>세계</b>&nbsp;&amp; 기록</p><br/><p>다음</p><img src="x"><script>bad()</script><style>bad</style><iframe>bad</iframe>'
        self.assertEqual(archive.make_excerpt(text, 180), '안녕 세계 & 기록 다음')
        self.assertEqual(archive.make_excerpt(text, 6), '안녕 세계…')
        self.assertEqual(archive.make_excerpt(text, 0), '')
        self.assertEqual(archive.make_excerpt('<img src="x">', 180), '')
        self.assertEqual(archive.make_excerpt(None, 180), '')
        for value in [-1, 1001, True, '180', 1.5]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                archive.resolve_config({**CONFIG, 'EXCERPT_LENGTH': value})

    def test_old_records_get_excerpt_without_losing_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'posts.json'
            old = archive.parse_feed(SAMPLE, BLOG)
            for post in old:
                post.pop('excerpt')
            archive.save_posts(path, old)
            self.assertEqual(archive.load_posts(path, BLOG), old)
            archive.collect(CONFIG, path, lambda _: SAMPLE)
            updated = archive.load_posts(path, BLOG)
            self.assertEqual(updated[0]['excerpt'], '미리보기 & 테스트 다음 문장입니다.')
            self.assertEqual({k: v for k, v in updated[0].items() if k != 'excerpt'}, old[0])
            before = path.read_bytes()
            archive.collect({**CONFIG, 'EXCERPT_LENGTH': 0}, path, lambda _: SAMPLE)
            self.assertEqual(path.read_bytes(), before)
            archive.collect(CONFIG, path, lambda _: SAMPLE.replace(b'description', b'unused'))
            self.assertEqual(path.read_bytes(), before)

    def test_excerpt_rendering_escaping_and_disabled(self):
        posts = archive.parse_feed(SAMPLE, BLOG)
        posts[0]['excerpt'] = '<script>unsafe</script> & text'
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            archive.build(CONFIG, posts, output)
            html = (output / 'index.html').read_text(encoding='utf-8')
            self.assertIn('<p class="excerpt">&lt;script&gt;unsafe&lt;/script&gt; &amp; text</p>', html)
            self.assertEqual(html.count('class="excerpt"'), 1)
            archive.build({**CONFIG, 'EXCERPT_LENGTH': 5}, posts, output)
            self.assertIn('&lt;scr…</p>', (output / 'index.html').read_text(encoding='utf-8'))
            archive.build({**CONFIG, 'EXCERPT_LENGTH': 0}, posts, output)
            self.assertNotIn('class="excerpt"', (output / 'index.html').read_text(encoding='utf-8'))

    def test_fork_settings_propagate_without_original_posts(self):
        config = archive.resolve_config({**CONFIG, "BLOG_ID": "another_blog",
                                         "SITE_NAME": "다른 이름 & Archive", "RSS_URL": ""})
        self.assertEqual(config["RSS_URL"], "https://rss.blog.naver.com/another_blog.xml")
        self.assertEqual(config["BLOG_URL"], "https://blog.naver.com/another_blog")
        with tempfile.TemporaryDirectory() as tmp, patch.object(archive, "ROOT", Path(tmp)):
            old_path = archive.data_path(CONFIG)
            archive.save_posts(old_path, archive.parse_feed(SAMPLE, BLOG))
            original = old_path.read_bytes()
            path = archive.data_path(config)
            self.assertEqual(archive.load_posts(path, config["BLOG_URL"]), [])
            self.assertTrue(archive.collect(config, path, lambda _: SAMPLE.replace(b"dowoolog", b"another_blog")))
            posts = archive.load_posts(path, config["BLOG_URL"])
            self.assertEqual(len(posts), 2)
            self.assertEqual(old_path.read_bytes(), original)
        with tempfile.TemporaryDirectory() as tmp:
            archive.build(config, posts, Path(tmp))
            html = (Path(tmp) / "index.html").read_text(encoding="utf-8")
            for old in ["dowoolog", "Dowoo", "DOWOO", "Test Archive"]:
                self.assertNotIn(old, html)
            self.assertEqual(html.count("다른 이름 &amp; Archive"), 5)
            self.assertIn('href="https://blog.naver.com/another_blog"', html)

    def test_invalid_blog_id_cannot_escape_data_directory(self):
        for value in ["../other", "", "a/b", None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                archive.data_path({**CONFIG, "BLOG_ID": value})

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
            self.assertIn('미리보기 &amp; 테스트 다음 문장입니다.', html)
            self.assertNotIn('hiddenScript', html)
            self.assertNotIn('hiddenStyle', html)
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
