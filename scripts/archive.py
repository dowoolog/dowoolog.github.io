"""RSS metadata archive and static site builder; Python standard library only."""

import argparse
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html import escape
from html.parser import HTMLParser
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MAX_FEED_BYTES = 5_000_000
KST = timezone(timedelta(hours=9))


def excerpt_length(config):
    length = config.get("EXCERPT_LENGTH", 180)
    if type(length) is not int or not 0 <= length <= 1000:
        raise ValueError("EXCERPT_LENGTH must be an integer between 0 and 1000")
    return length


class PlainText(HTMLParser):
    """Extract readable RSS text without scripts, styles or embedded content."""
    ignored = {"script", "style", "iframe", "object", "template", "noscript"}
    breaks = {"p", "div", "br", "hr", "li", "ul", "ol", "section", "article",
              "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = []

    def handle_starttag(self, tag, attrs):
        if tag in self.ignored:
            self.hidden.append(tag)
        if tag in self.breaks and not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self.hidden:
            del self.hidden[self.hidden.index(tag):]
        if tag in self.breaks and not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def shorten(text, length):
    if not length:
        return ""
    return text if len(text) <= length else text[:length - 1].rstrip() + "…"


def make_excerpt(description, length):
    if not description or not length:
        return ""
    parser = PlainText()
    parser.feed(description)
    parser.close()
    return shorten(" ".join("".join(parser.parts).split()), length)


def resolve_config(settings):
    """Derive blog addresses from the single user-editable blog ID."""
    blog_id = settings.get("BLOG_ID", "")
    if not isinstance(blog_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", blog_id):
        raise ValueError("BLOG_ID must be a valid Naver blog ID")
    return {
        **settings,
        "EXCERPT_LENGTH": excerpt_length(settings),
        "BLOG_URL": f"https://blog.naver.com/{blog_id}",
        "RSS_URL": settings.get("RSS_URL") or f"https://rss.blog.naver.com/{blog_id}.xml",
    }


def data_path(config):
    # Each blog has its own archive, including when this repository is forked.
    config = resolve_config(config)
    return ROOT / "data" / f"{config['BLOG_ID']}.json"


def last_collected(path):
    metadata = path.with_suffix(".meta.json")
    if not metadata.exists():
        return None
    value = json.loads(metadata.read_text(encoding="utf-8"))["last_collected_at"]
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("Collection timestamp must have a timezone")
    return dt


def web_url(value):
    """Reject credentials, control characters and non-HTTP(S) URLs."""
    if not isinstance(value, str) or re.search(r'[\s<>"\\\x00-\x1f\x7f]', value):
        raise ValueError("Unsafe URL")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("An absolute HTTP(S) URL is required")
    if parts.username or parts.password or parts.port not in (None, 80, 443):
        raise ValueError("Credentials or unexpected port in URL")
    return parts


def post_url(value, blog_url):
    """Normalize Naver's desktop/mobile/PostView links to one post identity."""
    parts = web_url(value)
    blog = web_url(blog_url)
    blog_id = blog.path.strip("/")
    if blog.hostname != "blog.naver.com" or not re.fullmatch(r"[A-Za-z0-9_-]+", blog_id):
        raise ValueError("BLOG_URL must identify a Naver blog")
    if parts.hostname not in ("blog.naver.com", "m.blog.naver.com"):
        raise ValueError("Post URL is outside Naver Blog")
    match = re.fullmatch(r"/([A-Za-z0-9_-]+)/(\d+)/?", parts.path)
    if match:
        owner, number = match.groups()
    elif parts.path == "/PostView.naver":
        query = parse_qs(parts.query)
        owner = query.get("blogId", [""])[0]
        number = query.get("logNo", [""])[0]
    else:
        raise ValueError("Not a recognized post URL")
    if owner != blog_id or not re.fullmatch(r"\d+", number):
        raise ValueError("Post does not belong to the configured blog")
    return f"https://blog.naver.com/{owner}/{number}"


def parse_date(value):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        # Do not invent a timezone if the feed did not provide one.
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def sort_posts(posts):
    return sorted(posts, key=lambda p: (p["published_at"] or "", p["url"]), reverse=True)


def parse_feed(raw, blog_url, length=180):
    if len(raw) > MAX_FEED_BYTES:
        raise ValueError("RSS exceeds size limit")
    # RSS does not need DTDs. Also catches UTF-16/32 encodings before XML parsing.
    probe = raw.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in probe or b"<!ENTITY" in probe:
        raise ValueError("DTD/entity declarations are not allowed")
    root = ET.fromstring(raw)
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise ValueError("Expected an RSS channel")
    posts = {}
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        try:
            url = post_url((item.findtext("link") or "").strip(), blog_url)
        except ValueError:
            print("Warning: skipped an invalid post URL", file=sys.stderr)
            continue
        if not title:
            print("Warning: skipped an item without a title", file=sys.stderr)
            continue
        posts.setdefault(url, {
            "title": title,
            "url": url,
            "published_at": parse_date(item.findtext("pubDate")),
            "category": (item.findtext("category") or "").strip() or None,
            "excerpt": make_excerpt(item.findtext("description"), length),
        })
    return sort_posts(posts.values())


def fetch_feed(url):
    web_url(url)
    request = Request(url, headers={"User-Agent": "NaverBlogArchive/1.0 (daily RSS reader)"})
    with urlopen(request, timeout=30) as response:
        return response.read(MAX_FEED_BYTES + 1)


def load_posts(path, blog_url):
    """Fail closed on damaged storage; never replace it with an empty archive."""
    if not path.exists():
        return []
    posts = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(posts, list):
        raise ValueError("Archive JSON must contain a list")
    for post in posts:
        required = {"title", "url", "published_at", "category"}
        if not isinstance(post, dict) or not required <= set(post) or set(post) - required - {"excerpt"}:
            raise ValueError("Invalid stored post schema")
        if "excerpt" in post and not isinstance(post["excerpt"], str):
            raise ValueError("Invalid stored excerpt")
        if not isinstance(post["title"], str) or not post["title"].strip():
            raise ValueError("Invalid stored title")
        post_url(post["url"], blog_url)
        if post["category"] is not None and not isinstance(post["category"], str):
            raise ValueError("Invalid stored category")
        if post["published_at"] is not None:
            dt = datetime.fromisoformat(post["published_at"])
            if dt.tzinfo is None:
                raise ValueError("Stored dates must have timezones")
            post["published_at"] = dt.astimezone(timezone.utc).isoformat()
    return sort_posts(posts)


def save_posts(path, posts):
    content = json.dumps(posts, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_bytes() == content.encode("utf-8"):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return True


def collect(config, path, fetcher=fetch_feed):
    length = excerpt_length(config)
    existing = load_posts(path, config["BLOG_URL"])
    try:
        incoming = parse_feed(fetcher(config["RSS_URL"]), config["BLOG_URL"], length)
        if not incoming:
            raise ValueError("RSS contains zero usable posts")
    except (OSError, ValueError, HTTPException, ET.ParseError) as error:
        print(f"Warning: RSS collection failed; existing data preserved: {error}", file=sys.stderr)
        return False
    merged = {}
    for post in existing + incoming:
        url = post_url(post["url"], config["BLOG_URL"])
        merged.setdefault(url, {**post, "url": url})
    # Backfill old records and refresh previews when the configured length changes.
    # Missing descriptions or disabled previews never erase an archived excerpt.
    for post in incoming:
        if post["excerpt"]:
            merged[post["url"]]["excerpt"] = post["excerpt"]
    changed = save_posts(path, sort_posts(merged.values()))
    save_posts(path.with_suffix(".meta.json"), {
        "last_collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    print(f"Archive: {len(merged)} posts; changed={changed}")
    return True


def build(config, posts, output, collected_at=None):
    length = excerpt_length(config)
    site_url = config["SITE_URL"].rstrip("/") + "/"
    site = web_url(site_url)
    if site.query or site.fragment:
        raise ValueError("SITE_URL must not contain query strings or fragments")
    web_url(config["BLOG_URL"])
    e = escape
    collection_label = "최근 가져온 시간: 기록 없음"
    if collected_at is not None:
        local_time = collected_at.astimezone(KST)
        collection_label = (f'최근 가져온 시간: <time datetime="{local_time.isoformat()}">'
                            f'{local_time:%Y-%m-%d %H:%M} KST</time>')
    rows = []
    for post in sort_posts(posts):
        url = post_url(post["url"], config["BLOG_URL"])
        date = '<span class="date">작성일 미제공</span>'
        if post["published_at"]:
            day = datetime.fromisoformat(post["published_at"]).astimezone(KST).date().isoformat()
            date = f'<time datetime="{e(post["published_at"])}">{day}</time>'
        category = f'<span class="category">{e(post["category"])}</span>' if post["category"] else ""
        excerpt = shorten(post.get("excerpt", ""), length)
        preview = f'<p class="excerpt">{e(excerpt)}</p>' if excerpt else ""
        rows.append(f'<li><article><div class="meta">{date}{category}</div>'
                    f'<h3><a href="{e(url, quote=True)}">{e(post["title"])}</a></h3>'
                    f'{preview}'
                    '</article></li>')
    listing = '<ol class="posts">' + "\n".join(rows) + '</ol>' if rows else '<p class="empty">아직 수집된 게시글이 없습니다.</p>'
    verification = "\n".join(
        f'<meta name="{name}" content="{e(config.get(key, ""), quote=True)}">'
        for name, key in [("google-site-verification", "GOOGLE_SITE_VERIFICATION"),
                          ("naver-site-verification", "NAVER_SITE_VERIFICATION")]
        if config.get(key)
    )
    document = f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(config['SITE_NAME'])} | 네이버 블로그 글 모음</title>
<meta name="description" content="{e(config['DESCRIPTION'], quote=True)}">
<link rel="canonical" href="{e(site_url, quote=True)}">
<meta property="og:type" content="website">
<meta property="og:locale" content="ko_KR">
<meta property="og:site_name" content="{e(config['SITE_NAME'], quote=True)}">
<meta property="og:title" content="{e(config['SITE_NAME'], quote=True)}">
<meta property="og:description" content="{e(config['DESCRIPTION'], quote=True)}">
<meta property="og:url" content="{e(site_url, quote=True)}">
{verification}
<link rel="stylesheet" href="./style.css">
</head>
<body>
<a class="skip" href="#posts">게시글 목록으로 건너뛰기</a>
<div class="container">
<header><div class="collection-info"><p class="collection-time">{collection_label}</p>
<p class="collection-interval">업데이트 주기: 1시간 (매시 00분)</p></div>
<h1>{e(config['SITE_NAME'])}</h1>
<p class="intro">{e(config['DESCRIPTION'])}</p>
<a class="blog-link" href="{e(config['BLOG_URL'], quote=True)}">네이버 블로그 방문 ↗</a></header>
<main id="posts"><section aria-labelledby="posts-heading">
<div class="section-heading"><h2 id="posts-heading">게시글 <span class="count">{len(posts)}</span></h2><p>최신순</p></div>
{listing}
</section></main>
<footer><span>{e(config['SITE_NAME'])}</span><p>글의 제목과 작성일을 보관합니다. 전체 내용은 네이버 블로그에서 읽을 수 있습니다.</p></footer>
</div>
</body>
</html>
'''
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(document, encoding="utf-8")
    shutil.copyfile(ROOT / "static/style.css", output / "style.css")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    (output / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {site_url}sitemap.xml\n", encoding="utf-8")
    # Only this site's actual page belongs in its sitemap, never Naver URLs.
    sitemap = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{e(site_url)}</loc></url></urlset>\n'
    (output / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    print(f"Built {output} ({len(posts)} posts)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "collect", "build"])
    args = parser.parse_args()
    try:
        config = resolve_config(json.loads((ROOT / "config.json").read_text(encoding="utf-8")))
    except json.JSONDecodeError as error:
        parser.error(f"config.json:{error.lineno}:{error.colno}: {error.msg}. "
                     "Check commas between settings and double quotes around strings.")
    if args.command == "validate":
        print("config.json: OK")
        return 0
    path = data_path(config)
    if args.command == "collect":
        return 0 if collect(config, path) else 1
    build(config, load_posts(path, config["BLOG_URL"]), ROOT / "_site", last_collected(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
