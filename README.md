# Dowoo's Archive

네이버 블로그 [dowoolog](https://blog.naver.com/dowoolog)의 RSS에서 **제목, 원문 URL, 게시일, 카테고리**만 수집하여 보관하는 정적 웹사이트입니다. 제목을 클릭하면 네이버 블로그 원문으로 이동합니다. 본문 전체를 저장하지 않습니다.

Python 3 표준 라이브러리, HTML5, CSS3, RSS, GitHub Actions, GitHub Pages만 사용합니다. JavaScript, Jekyll, 프론트엔드 프레임워크, Node.js 빌드, 데이터베이스, 외부 호스팅, 유료 API가 필요하지 않습니다. 공개 저장소와 GitHub의 무료 제공 범위에서 운영합니다.

## 구조

```text
.github/workflows/pages.yml  # 매일 수집 → 데이터 커밋 → Pages 배포
config.json                  # 사이트 주소, RSS, 검색엔진 소유확인 토큰
data/posts.json              # 누적 메타데이터 (Git으로 보관)
scripts/archive.py           # RSS 수집 / 정적 사이트 생성
static/style.css             # PC·모바일 반응형 스타일
tests/test_archive.py        # 표준 unittest 테스트
tests/fixtures/sample.xml    # 테스트 전용 RSS (실제 데이터 아님)
_site/                       # 생성 결과, Git에서는 제외
  index.html
  style.css
  sitemap.xml
  robots.txt
  .nojekyll
```

## 로컬 실행

Python 3.10 이상을 설치하고 저장소 루트에서 실행합니다. pip 설치는 필요하지 않습니다. GitHub Actions는 Python 3.13을 사용합니다.

```bash
python -m unittest discover -s tests -v
python scripts/archive.py collect
python scripts/archive.py build
python -m http.server 8000 --bind 127.0.0.1 --directory _site
```

브라우저에서 `http://127.0.0.1:8000/`을 엽니다. Windows에서 `python` 대신 `py -3`을 사용할 수도 있습니다. 이 작업 환경에서는 Python이 없어 테스트용 휴대용 Python을 `.tools/python/`에 받았습니다. 해당 환경에서는 `.\.tools\python\python.exe`로 실행할 수 있지만 `.tools/`는 배포하거나 커밋하지 않습니다.

수집 없이 기존 JSON으로 페이지를 만들려면 `build`만 실행합니다. `_site/`를 직접 수정하면 다음 빌드에서 덮어씁니다. 문구는 `config.json`, HTML은 `scripts/archive.py`, 스타일은 `static/style.css`를 수정합니다.

## 데이터와 실패 처리

저장 형식은 아래와 같습니다. 이 예시는 형식 설명용이며 실데이터는 `data/posts.json`에 있습니다.

```json
[
  {
    "title": "RSS에서 제공된 제목",
    "url": "https://blog.naver.com/dowoolog/123",
    "published_at": "2026-10-29T04:41:24+00:00",
    "category": null
  }
]
```

- RSS의 `pubDate`를 UTC ISO 8601로 저장하며 화면 날짜는 한국 시간으로 표시합니다. 누락·해석 불가능·시간대 미제공 날짜는 `null`이며 화면에는 `작성일 미제공`으로 표시합니다. 카테고리 미제공도 `null`입니다.
- URL을 키로 신규 글만 추가합니다. 기존 글의 제목·날짜·카테고리는 이후 RSS 내용으로 덮어쓰지 않습니다. RSS에서 사라진 글도 유지합니다.
- 같은 글의 HTTP/HTTPS, 모바일 주소, `PostView.naver` 주소 및 추적 매개변수를 대표 게시글 URL로 정규화하여 중복을 제거합니다. 네이버의 설정된 블로그 게시글만 허용합니다.
- 날짜가 있는 글을 최신순으로, 날짜가 없는 글은 끝에 표시합니다. 같은 날짜는 URL 역순으로 정렬합니다.
- 한 번 실행할 때 RSS를 한 번 요청하며 30초 제한과 5 MB 응답 제한을 적용합니다. 전체 본문·개별 게시글 크롤링이나 반복 재시도는 하지 않습니다.
- 요청 실패, 잘못된 XML, DTD/entity 선언, 유효한 결과 0개는 경고와 종료 코드 1을 반환하고 기존 파일을 그대로 보존합니다. 제목 없는 항목과 위험한 URL은 건너뜁니다.
- 임시 파일을 만든 후 원자적으로 교체합니다. 동일 데이터는 파일을 다시 쓰지 않습니다. 저장된 JSON 자체가 손상되면 수집·빌드를 중지하며 빈 데이터로 초기화하지 않습니다.
- 첫 실행에 수집 실패 시 기본 `[]`로 빈 상태 화면을 배포할 수 있습니다. 실패는 Actions 경고에 남습니다. 기존 JSON 손상이나 빌드 오류는 배포를 중단합니다.

## GitHub Pages 활성화

1. 이 코드를 공개 저장소 `dowoolog/dowoolog.github.io`의 **main** 브랜치에 커밋하고 푸시합니다. 기본 브랜치도 main으로 설정합니다.
2. **Settings → Pages → Build and deployment → Source**를 **GitHub Actions**로 선택합니다. 브랜치/Jekyll 배포를 선택하지 않습니다.
3. `config.json`의 `SITE_URL`이 실제 주소 `https://dowoolog.github.io`와 일치하는지 확인합니다. 프로젝트 저장소라면 `https://사용자.github.io/저장소명`처럼 경로까지 설정합니다.
4. **Actions → Collect RSS and deploy Pages → Run workflow → main**으로 최초 실행합니다.
5. 배포 완료 후 사이트, `/sitemap.xml`, `/robots.txt`가 열리는지 확인합니다.

커스텀 도메인을 사용할 경우 Pages 설정과 DNS는 직접 구성하고 `SITE_URL`도 변경합니다. `robots.txt`는 검색로봇이 호스트 루트에서 찾으므로 프로젝트 하위 경로로 배포할 때는 호스트 루트 사이트의 robots 설정도 확인해야 합니다.

## GitHub Actions 설정 및 처리 순서

- Actions 실행과 공식 `actions/*` Action 사용을 저장소/조직 정책에서 허용해야 합니다.
- 자동 토큰 `GITHUB_TOKEN`을 사용하므로 PAT나 별도 비밀 키가 필요하지 않습니다. 빌드 작업에만 `contents: write`, 배포 작업에만 `pages: write`, `id-token: write`를 부여합니다. 저장소/조직에서 쓰기를 막았다면 이 워크플로의 데이터 커밋이 가능하도록 정책을 조정해야 합니다.
- main 보호 규칙이나 ruleset이 봇의 직접 푸시를 차단하면 수집 데이터 커밋이 실패합니다. 해당 저장소 운영 정책에 맞게 봇의 데이터 커밋을 허용해야 합니다. 강제 푸시는 하지 않습니다.
- `github-pages` Environment의 배포 허용 브랜치는 main을 포함해야 합니다. 필수 승인 규칙이 있으면 자동 배포가 승인 대기 상태가 될 수 있습니다.
- `push`(main), `schedule`, `workflow_dispatch`를 지원합니다. 수동 실행도 main에서 합니다.
- 실행 순서는 **테스트 → RSS 수집 → 정적 사이트 빌드 → JSON 변경 커밋·푸시 → 아티팩트 업로드 → Pages 배포**입니다. RSS 실패만 기존 자료로 계속 진행합니다.
- 데이터 커밋 후 새 `push` 워크플로에 의존하지 않습니다. `GITHUB_TOKEN`으로 생성한 푸시는 보통 다른 push 워크플로를 시작하지 않으므로 같은 실행의 후속 배포 작업으로 연결합니다.
- 동시 실행은 같은 concurrency 그룹에서 직렬화하며 진행 중 작업을 취소하지 않습니다. 대기 실행은 GitHub 정책에 따라 더 새 실행으로 대체될 수 있습니다. 빌드 도중 원격 main에 다른 커밋이 생기면 오래된 결과를 밀어 넣지 않고 중단합니다. 최신 main으로 수동 재실행하세요.
- 변경 여부는 `data/posts.json`의 실제 Git diff로 판단합니다. 변경이 없으면 커밋하지 않고 사이트 배포만 수행합니다. 생성 시각 같은 값으로 불필요한 변경을 만들지 않습니다.

## RSS 주소 및 수집 주기 변경

`config.json`의 `RSS_URL`을 수정합니다. 다른 네이버 블로그로 바꾸려면 `BLOG_URL`도 함께 변경해야 URL 검증을 통과합니다. 이 구현은 네이버 RSS 2.0과 네이버 게시글 URL을 대상으로 하며 임의의 외부 RSS/Atom까지 지원하지 않습니다. 블로그를 바꾸면 기존 아카이브의 소유 블로그 검증도 달라지므로 기존 JSON은 별도 백업하고 새 저장소에서 시작하는 것을 권장합니다.

`.github/workflows/pages.yml`의 다음 값을 수정하면 수집 주기를 바꿀 수 있습니다.

```yaml
schedule:
  - cron: '17 21 * * *' # UTC 21:17 = 다음 날 한국 시간 06:17
```

기본은 하루 한 번입니다. main 푸시와 수동 실행 때에도 수집합니다. 과도한 요청이 되지 않도록 빈번한 예약 실행은 피하세요.

## Google Search Console 등록

1. [Search Console](https://search.google.com/search-console)에 로그인해 **URL 접두어** 속성으로 실제 `SITE_URL`을 추가합니다. `github.io` 도메인의 DNS는 소유하지 않으므로 기본 GitHub Pages 주소에는 URL 접두어 방식이 적합합니다.
2. 소유권 확인에서 **HTML 태그**를 선택하고 `google-site-verification` 메타태그의 **content 값만** 복사합니다.
3. `config.json`의 `GOOGLE_SITE_VERIFICATION`에 그 값을 넣고 main에 푸시합니다. 확인 코드는 공개 HTML에 들어가는 값이며 비밀 키가 아닙니다.
4. Pages 배포 후 홈페이지 소스의 `<head>`에 태그가 들어갔는지 확인하고 Search Console에서 소유권 확인을 완료합니다. 확인 후에도 토큰을 유지합니다.
5. Sitemaps 메뉴에서 `https://dowoolog.github.io/sitemap.xml`(또는 변경한 실제 주소)을 제출합니다. URL 검사로 홈페이지의 크롤링·색인 상태를 확인할 수 있습니다.

## 네이버 서치어드바이저 등록

1. [네이버 서치어드바이저](https://searchadvisor.naver.com/)의 웹마스터 도구에서 **GitHub Pages 사이트 주소**를 등록합니다.
2. HTML 태그 소유확인 방식에서 받은 `naver-site-verification`의 **content 값만** `config.json`의 `NAVER_SITE_VERIFICATION`에 입력합니다.
3. main에 푸시하고 배포가 완료되면 사이트 소스를 확인한 뒤 소유확인을 진행합니다. 태그는 정적 HTML의 `<head>`에 생성됩니다.
4. 요청 메뉴의 사이트맵 제출에서 이 사이트의 `sitemap.xml` 주소를 제출하고 robots.txt 검증/수집 상태를 확인합니다.
5. 원본 네이버 RSS를 GitHub Pages 사이트의 RSS인 것처럼 제출하지 않습니다. 이 프로젝트는 별도의 출력 RSS를 만들지 않습니다.

등록 대상은 소유권 확인이 가능한 GitHub Pages 사이트입니다. 네이버 블로그 원문을 이 사이트 소유의 페이지로 주장하지 않습니다.

## SEO 범위와 수집 한계

정적 `<a href>`에 제목을 넣고, 페이지 title·description·canonical·Open Graph·한국어 lang·UTF-8·viewport·시맨틱 구조를 제공합니다. 사이트맵에는 실제로 생성한 **이 사이트의 홈페이지 URL 하나만** 포함합니다. 원문 URL이나 존재하지 않는 개별 상세 페이지를 넣지 않으며 확인되지 않은 수정 시각도 만들지 않습니다.

**GitHub Pages가 색인되어도 네이버 원문의 색인은 보장되지 않습니다. 검색 순위 상승이나 원문 자동 색인을 보장하지 않습니다.** 크롤러가 링크를 발견할 수 있게 하는 구조이며 색인과 순위는 각 검색엔진의 판단과 원문 사이트의 접근 정책 등에 달려 있습니다.

RSS는 최근 일부 게시글만 제공할 수 있습니다. 처음 실행하기 전에 RSS에서 빠진 과거 글은 이 방식으로 복구할 수 없습니다. 하루 사이에 많은 글이 올라와 RSS 범위를 벗어난 글도 놓칠 수 있습니다. 수집에 성공한 시점 이후의 기록을 누적하며 본문 백업 서비스는 아닙니다. 비공개·삭제된 글의 과거 메타데이터도 남으므로 필요 시 JSON에서 수동 제거해야 합니다. 해당 글이 여전히 RSS에 있으면 다음 수집에 다시 추가됩니다.

예약 실행은 기본 브랜치의 워크플로를 사용하며 정확한 시각 실행을 보장하지 않습니다. 부하에 따라 지연되거나 실행이 누락될 수 있습니다. 공개 저장소가 60일간 활동이 없으면 예약 실행이 자동 비활성화될 수 있습니다. 새 게시글이 없어 자동 커밋도 없을 때 특히 Actions 상태를 확인하고 비활성화된 예약을 다시 활성화하세요. GitHub 서비스 장애, 정책·사용 한도, 저장소 설정 역시 실행에 영향을 줍니다.

## 검증 기록

- 2026-10-29 실제 `https://rss.blog.naver.com/dowoolog.xml`에 접근하여 **HTTP 200 / RSS 항목 2개**를 확인했습니다. `rss/channel/item` 아래에 `title`, `link`, `pubDate`, `category`가 있고 제목·링크·카테고리는 CDATA를 사용합니다. Python 수집기로 2개를 실제 저장했습니다.
- 현재 도구 샌드박스에서는 외부 연결이 차단되어 첫 요청이 실패했지만, 허용된 외부 네트워크 실행에서는 성공했습니다. 이는 RSS 서비스 자체의 접근 불가가 아니었습니다.
- 샘플 RSS로 중복 URL, 누락 날짜, 네트워크/HTTP/XML 오류, 빈 RSS, 크기 제한, 저장 파일 손상, 원자적 저장 실패, HTML 이스케이프, 사이트맵 범위를 검사합니다. 샘플 게시글을 실제 아카이브에 넣지 않습니다.
- GitHub 서버에서의 예약 실행·자동 커밋·Pages 배포, Search Console/서치어드바이저 소유확인 및 실제 색인은 로컬 테스트로 검증할 수 없습니다. 저장소 설정 후 최초 워크플로 실행과 각 서비스 화면에서 확인해야 합니다.
- 로컬에서 자동 테스트 11개와 실제 데이터 정적 빌드가 통과했습니다. 이 세션에 연결 가능한 브라우저가 없어 PC·모바일의 실제 렌더링 검증은 수행하지 못했습니다.

## 참고한 공식 문서

- [Google: 크롤링 가능한 링크](https://developers.google.com/search/docs/crawling-indexing/links-crawlable)
- [Google: canonical 설정](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls)
- [Google: 사이트맵 개요](https://developers.google.com/search/docs/crawling-indexing/sitemaps/overview)
- [Google: 사이트 소유권 확인](https://support.google.com/webmasters/answer/9008080)
- [네이버: 사이트 등록 및 소유확인](https://searchadvisor.naver.com/guide/faq-start-register)
- [GitHub: 사용자 정의 Pages 워크플로](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [GitHub: schedule 이벤트와 제약](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
