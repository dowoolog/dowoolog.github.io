"""RSS metadata archive and static site builder; Python standard library only."""

import argparse
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html import escape
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


def resolve_config(settings):
    """Derive blog addresses from the single user-editable blog ID."""
    blog_id = settings.get("BLOG_ID", "")
    if not isinstance(blog_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", blog_id):
        raise ValueError("BLOG_ID must be a valid Naver blog ID")
    return {
        **settings,
        "BLOG_URL": f"https://blog.naver.com/{blog_id}",
        "RSS_URL": settings.get("RSS_URL") or f"https://rss.blog.naver.com/{blog_id}.xml",
    }


def data_path(config):
    # Each blog has its own archive, including when this repository is forked.
    config = resolve_config(config)
    return ROOT / "data" / f"{config['BLOG_ID']}.json"


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


def parse_feed(raw, blog_url):
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
        if not isinstance(post, dict) or set(post) != {"title", "url", "published_at", "category"}:
            raise ValueError("Invalid stored post schema")
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
    existing = load_posts(path, config["BLOG_URL"])
    try:
        incoming = parse_feed(fetcher(config["RSS_URL"]), config["BLOG_URL"])
        if not incoming:
            raise ValueError("RSS contains zero usable posts")
    except (OSError, ValueError, HTTPException, ET.ParseError) as error:
        print(f"Warning: RSS collection failed; existing data preserved: {error}", file=sys.stderr)
        return False
    merged = {}
    for post in existing + incoming:
        url = post_url(post["url"], config["BLOG_URL"])
        merged.setdefault(url, {**post, "url": url})
    changed = save_posts(path, sort_posts(merged.values()))
    print(f"Archive: {len(merged)} posts; changed={changed}")
    return True


def build(config, posts, output):
    site_url = config["SITE_URL"].rstrip("/") + "/"
    site = web_url(site_url)
    if site.query or site.fragment:
        raise ValueError("SITE_URL must not contain query strings or fragments")
    web_url(config["BLOG_URL"])
    e = escape
    rows = []
    for post in sort_posts(posts):
        url = post_url(post["url"], config["BLOG_URL"])
        date = '<span class="date">작성일 미제공</span>'
        if post["published_at"]:
            day = datetime.fromisoformat(post["published_at"]).astimezone(KST).date().isoformat()
            date = f'<time datetime="{e(post["published_at"])}">{day}</time>'
        category = f'<span class="category">{e(post["category"])}</span>' if post["category"] else ""
        rows.append(f'<li><article><div class="meta">{date}{category}</div>'
                    f'<h3><a href="{e(url, quote=True)}">{e(post["title"])}</a></h3>'
                    '<span class="source">네이버 블로그 원문 ↗</span></article></li>')
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
<header><p class="eyebrow">BLOG ARCHIVE</p>
<h1>{e(config['SITE_NAME'])}</h1>
<p class="intro">{e(config['DESCRIPTION'])}</p>
<a class="blog-link" href="{e(config['BLOG_URL'], quote=True)}">네이버 블로그 방문 ↗</a></header>
<main id="posts"><section aria-labelledby="posts-heading">
<div class="section-heading"><h2 id="posts-heading">게시글 <span class="count">{len(posts)}</span></h2><p>최신순 · 날짜는 한국 시간 기준</p></div>
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
    parser.add_argument("command", choices=["collect", "build"])
    args = parser.parse_args()
    config = resolve_config(json.loads((ROOT / "config.json").read_text(encoding="utf-8")))
    path = data_path(config)
    if args.command == "collect":
        return 0 if collect(config, path) else 1
    build(config, load_posts(path, config["BLOG_URL"]), ROOT / "_site")
    return 0


if __name__ == "__main__":
    sys.exit(main())
