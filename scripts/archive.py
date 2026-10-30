"""파이썬 표준 라이브러리로 RSS 글 정보를 보관하고 정적 사이트를 생성합니다.

GitHub Actions에서 collect로 글을 수집한 뒤 build로 배포할 파일을 만듭니다.
설정은 config.json, 글 데이터는 data/, 생성 결과는 _site/에 저장됩니다.
"""

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

# 실행 위치와 관계없이 이 파일의 상위 폴더를 프로젝트 기준 경로로 사용합니다.
ROOT = Path(__file__).resolve().parents[1]
MAX_FEED_BYTES = 5_000_000
KST = timezone(timedelta(hours=9))


def excerpt_length(config):
    # 미리보기 길이는 0~1000자만 허용하며, 0은 미리보기를 끕니다.
    length = config.get("EXCERPT_LENGTH", 180)
    if type(length) is not int or not 0 <= length <= 1000:
        raise ValueError("EXCERPT_LENGTH must be an integer between 0 and 1000")
    return length


class PlainText(HTMLParser):
    """RSS 본문에서 스크립트·스타일·삽입 콘텐츠를 제외한 텍스트를 추출합니다."""
    ignored = {"script", "style", "iframe", "object", "template", "noscript"}
    breaks = {"p", "div", "br", "hr", "li", "ul", "ol", "section", "article",
              "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = []

    def handle_starttag(self, tag, attrs):
        # 제외할 태그 내부의 내용은 숨기고, 문단 경계에는 공백을 넣습니다.
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
    # 말줄임표까지 포함하여 지정한 길이를 넘지 않도록 자릅니다.
    if not length:
        return ""
    return text if len(text) <= length else text[:length - 1].rstrip() + "…"


def make_excerpt(description, length):
    # HTML 태그를 제거하고 연속된 공백을 정리한 뒤 미리보기를 만듭니다.
    if not description or not length:
        return ""
    parser = PlainText()
    parser.feed(description)
    parser.close()
    return shorten(" ".join("".join(parser.parts).split()), length)


def resolve_config(settings):
    """사용자가 설정한 블로그 ID로 블로그 주소와 RSS 주소를 구성합니다."""
    blog_id = settings.get("BLOG_ID", "")
    if not isinstance(blog_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", blog_id):
        raise ValueError("BLOG_ID must be a valid Naver blog ID")
    return {
        **settings,
        "EXCERPT_LENGTH": excerpt_length(settings),
        "BLOG_URL": f"https://m.blog.naver.com/{blog_id}",
        "RSS_URL": settings.get("RSS_URL") or f"https://rss.blog.naver.com/{blog_id}.xml",
    }


def data_path(config):
    # 저장소를 포크하거나 블로그 ID를 바꿔도 블로그별 데이터를 따로 보관합니다.
    config = resolve_config(config)
    return ROOT / "data" / f"{config['BLOG_ID']}.json"


def collection_state_path(path):
    # 수집 상태와 시간은 게시글 데이터와 분리하여 임시 기록으로 저장합니다.
    return path.parent / ".runtime" / path.name


def collection_state(path):
    # 반환값은 (최근 수집 성공 시각, 수집 실패 여부)입니다.
    metadata = collection_state_path(path)
    if not metadata.exists():
        return None, False
    state = json.loads(metadata.read_text(encoding="utf-8"))
    if state["status"] != "success":
        return None, True
    dt = datetime.fromisoformat(state["collected_at"])
    if dt.tzinfo is None:
        raise ValueError("Collection timestamp must have a timezone")
    return dt, False


def web_url(value):
    """인증 정보·제어 문자가 포함된 주소와 HTTP(S)가 아닌 주소를 거부합니다."""
    if not isinstance(value, str) or re.search(r'[\s<>"\\\x00-\x1f\x7f]', value):
        raise ValueError("Unsafe URL")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("An absolute HTTP(S) URL is required")
    if parts.username or parts.password or parts.port not in (None, 80, 443):
        raise ValueError("Credentials or unexpected port in URL")
    return parts


def post_url(value, blog_url):
    """네이버 PC·모바일·PostView 주소를 통일하여 같은 글의 중복을 막습니다."""
    parts = web_url(value)
    blog = web_url(blog_url)
    blog_id = blog.path.strip("/")
    if blog.hostname not in ("blog.naver.com", "m.blog.naver.com") or not re.fullmatch(r"[A-Za-z0-9_-]+", blog_id):
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
    return f"https://m.blog.naver.com/{owner}/{number}"


def parse_date(value):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        # RSS에 시간대가 없으면 임의로 추정하지 않고 날짜 미제공으로 처리합니다.
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def sort_posts(posts):
    # 최신 글부터 정렬하고, 작성일이 없는 글은 목록 끝에 배치합니다.
    return sorted(posts, key=lambda p: (p["published_at"] or "", p["url"]), reverse=True)


def parse_feed(raw, blog_url, length=180):
    if len(raw) > MAX_FEED_BYTES:
        raise ValueError("RSS exceeds size limit")
    # RSS에 필요 없는 DTD·엔티티 선언을 차단합니다.
    # 널 바이트를 제거하여 UTF-16/32로 인코딩된 선언도 파싱 전에 확인합니다.
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
    # 응답 크기 초과를 감지할 수 있도록 제한보다 1바이트 더 읽습니다.
    web_url(url)
    request = Request(url, headers={"User-Agent": "NaverBlogArchive/1.0 (daily RSS reader)"})
    with urlopen(request, timeout=30) as response:
        return response.read(MAX_FEED_BYTES + 1)


def load_posts(path, blog_url):
    """저장 데이터가 손상되면 중단하고, 빈 데이터로 덮어쓰지 않습니다."""
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
    # 내용이 같으면 파일을 다시 쓰지 않아 불필요한 변경을 방지합니다.
    content = json.dumps(posts, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_bytes() == content.encode("utf-8"):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    # 같은 폴더에 임시 파일을 완성한 뒤 교체하여 저장 중 원본 손상을 방지합니다.
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
    # 실패할 수 있는 작업 전에 상태를 초기화하여 과거의 성공 기록을 재사용하지 않습니다.
    save_posts(collection_state_path(path), {"status": "failed"})
    existing = load_posts(path, config["BLOG_URL"])
    try:
        incoming = parse_feed(fetcher(config["RSS_URL"]), config["BLOG_URL"], length)
        if not incoming:
            raise ValueError("RSS contains zero usable posts")
    except (OSError, ValueError, HTTPException, ET.ParseError) as error:
        print(f"Warning: RSS collection failed; existing data preserved: {error}", file=sys.stderr)
        return False
    merged = {}
    # 기존 글을 먼저 보존하고 새 글을 추가하므로 RSS에서 빠진 글도 남습니다.
    for post in existing + incoming:
        url = post_url(post["url"], config["BLOG_URL"])
        merged.setdefault(url, {**post, "url": url})
    # RSS에 남아 있는 글의 제목과 미리보기를 최신 정보로 갱신합니다.
    # 본문이 없거나 미리보기를 꺼도 기존에 보관한 미리보기는 지우지 않습니다.
    for post in incoming:
        merged[post["url"]]["title"] = post["title"]
        if post["excerpt"]:
            merged[post["url"]]["excerpt"] = post["excerpt"]
    changed = save_posts(path, sort_posts(merged.values()))
    save_posts(collection_state_path(path), {
        "status": "success",
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    print(f"Archive: {len(merged)} posts; changed={changed}")
    return True


def build(config, posts, output, collected_at=None, collection_failed=False):
    # 저장된 글로 HTML과 배포 파일을 생성합니다. 이 단계에서는 RSS를 수집하지 않습니다.
    length = excerpt_length(config)
    site_url = config["SITE_URL"].rstrip("/") + "/"
    site = web_url(site_url)
    if site.query or site.fragment:
        raise ValueError("SITE_URL must not contain query strings or fragments")
    web_url(config["BLOG_URL"])
    # 외부 텍스트의 특수문자를 이스케이프하여 HTML 태그로 해석되지 않게 합니다.
    e = escape
    collection_label = "최근 가져온 시간: 기록 없음"
    if collection_failed:
        collection_label = "이번 수집 실패 · 보관된 글 표시 중"
    elif collected_at is not None:
        local_time = collected_at.astimezone(KST)
        collection_label = (f'최근 가져온 시간: <time datetime="{local_time.isoformat()}">'
                            f'{local_time:%Y-%m-%d %H:%M} KST</time>')
    rows = []
    # 게시글마다 날짜·카테고리·제목·미리보기로 구성된 목록 항목을 만듭니다.
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
    # 페이지 구조와 문구를 수정하려면 아래 HTML을, 디자인은 static/style.css를 수정합니다.
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
<header><p class="collection-time">{collection_label}</p>
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
    # GitHub Pages에 올릴 정적 파일을 준비합니다.
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(document, encoding="utf-8")
    shutil.copyfile(ROOT / "static/style.css", output / "style.css")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    (output / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {site_url}sitemap.xml\n", encoding="utf-8")
    # 사이트맵에는 이 사이트의 실제 페이지만 넣고 네이버 원문 주소는 넣지 않습니다.
    sitemap = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{e(site_url)}</loc></url></urlset>\n'
    (output / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    print(f"Built {output} ({len(posts)} posts)")


def main():
    # validate: 설정 검사 / collect: RSS 수집·저장 / build: 정적 사이트 생성
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
    collected_at, failed = collection_state(path)
    build(config, load_posts(path, config["BLOG_URL"]), ROOT / "_site", collected_at, failed)
    return 0


if __name__ == "__main__":
    # 종료 코드로 GitHub Actions에 실행 성공(0) 또는 실패를 전달합니다.
    sys.exit(main())
