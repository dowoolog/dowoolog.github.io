# 네이버 블로그 아카이브

네이버 RSS의 제목·링크·작성일·카테고리와 짧은 본문 미리보기를 모아 보여주는 정적 사이트입니다. Python 표준 라이브러리와 HTML/CSS만 사용하며, 공개 저장소의 GitHub Actions와 Pages 무료 제공 범위에서 운영합니다. 별도 서버·데이터베이스·Jekyll·Node.js 빌드·JavaScript가 필요하지 않습니다.

## 주요 기능

- 게시글 수와 최신순 목록을 PC·모바일에서 표시하고, 제목을 누르면 네이버 원문으로 이동합니다.
- 헤더에는 마지막 RSS 수집 성공 시간을 한국 시간(KST)으로 표시합니다. 새 글이 없어도 성공하면 갱신하며, 실패 시 이전 시간을 유지합니다. 기록이 없으면 `기록 없음`으로 표시합니다.
- URL 기준으로 중복을 제거하고, 수집한 글은 RSS에서 사라져도 JSON에 보관합니다.
- RSS 본문에서 HTML·스크립트를 제거한 미리보기를 제목 아래에 표시합니다.
- 이름·블로그 ID·주소·소개·미리보기 길이를 `config.json` 한 곳에서 관리합니다.
- 정적 HTML 링크, title·description·canonical·Open Graph, sitemap.xml·robots.txt·.nojekyll을 생성합니다.
- 1시간마다 자동 수집하고, 데이터가 변경된 경우에만 커밋한 뒤 Pages에 배포합니다.

## 포크해서 사용하기

1. 저장소를 포크하고 기본 브랜치를 `main`으로 설정합니다.
2. **`config.json`만 자신의 정보로 수정**합니다.

   | 설정                       | 용도                                                     |
   | -------------------------- | -------------------------------------------------------- |
   | `SITE_NAME`                | 사이트 제목·브라우저 제목·Open Graph·푸터에 공통 적용    |
   | `BLOG_ID`                  | 네이버 블로그 ID. 원문 및 RSS 주소 자동 생성             |
   | `SITE_URL`                 | 실제 Pages 주소. 예: `https://사용자.github.io/저장소명` |
   | `DESCRIPTION`              | 사이트 소개 및 검색·공유 설명                            |
   | `EXCERPT_LENGTH`           | 본문 미리보기 길이. 기본 180자, 0이면 숨김 (0~1000)      |
   | `GOOGLE_SITE_VERIFICATION` | Google 소유확인 토큰. 사용하지 않으면 빈 문자열          |
   | `NAVER_SITE_VERIFICATION`  | 네이버 소유확인 토큰. 사용하지 않으면 빈 문자열          |

3. **Settings → Pages → Source**에서 **GitHub Actions**를 선택합니다.
4. 포크한 저장소의 **Actions** 탭에서 워크플로를 활성화합니다. 설정 변경을 main에 푸시하거나 **Collect RSS and deploy Pages → Run workflow**를 실행합니다.

포크에 포함된 검색엔진 소유확인 토큰은 자신의 값으로 교체하거나 빈 문자열로 비웁니다. `SITE_URL`에는 저장소 주소가 아닌 실제 배포 주소를 입력합니다. 사용자 사이트는 `https://사용자.github.io`, 프로젝트 사이트는 `https://사용자.github.io/저장소명`입니다.

블로그별 데이터는 `data/<BLOG_ID>.json`에 저장됩니다. ID를 바꾸면 새 아카이브를 사용하므로 기존 데이터를 삭제할 필요가 없습니다. 이전 블로그의 JSON은 보존되지만 현재 사이트에는 표시되지 않습니다. 이름·소개만 바꾸면 기존 아카이브를 계속 사용합니다.

## 본문 미리보기

`EXCERPT_LENGTH`의 기본값은 **180자**이며, 0~1000 사이의 정수로 설정합니다. 긴 내용은 말줄임표를 포함해 설정한 길이로 자릅니다.

- RSS의 `description`만 사용하며 원문 페이지를 별도로 크롤링하지 않습니다. 전체 본문 대신 길이가 제한된 일반 텍스트를 JSON에 저장합니다.
- 미리보기는 정적 HTML에 포함됩니다. RSS에 본문이 없고 저장된 미리보기도 없으면 생략합니다.
- 기존 글도 RSS에 남아 있으면 다음 수집 때 미리보기를 보충·갱신합니다. 기존 제목·작성일·카테고리는 덮어쓰지 않습니다.
- 길이를 줄이면 다음 빌드부터 적용됩니다. 늘리면 다음 수집 때 보충되며, RSS에서 빠진 글은 이미 저장된 길이까지만 표시할 수 있습니다.
- `0`은 미리보기 표시·신규 수집을 끕니다. 이미 저장된 미리보기는 보존하므로 다시 켜면 사용할 수 있습니다.

## 파일 구성

```text
config.json                 사용자 설정
data/<BLOG_ID>.json          블로그별 누적 데이터
data/<BLOG_ID>.meta.json     마지막 수집 성공 시간
scripts/archive.py          RSS 수집 및 HTML·SEO 파일 생성
static/style.css            반응형 스타일
.github/workflows/pages.yml  자동 수집·커밋·배포
tests/                      샘플 RSS 및 테스트
_site/                      index.html·style.css·sitemap.xml·robots.txt·.nojekyll (Git 제외)
```

`_site/`를 직접 수정하지 말고 설정·생성 코드·스타일을 수정한 뒤 다시 빌드합니다.

## 검색엔진 등록

1. [Google Search Console](https://search.google.com/search-console)에서 Pages 주소를 **URL 접두어** 속성으로 추가하거나, [네이버 서치어드바이저](https://searchadvisor.naver.com/) 웹마스터 도구에 사이트를 등록합니다.
2. **HTML 태그** 소유확인을 선택하고 태그의 `content` 값만 `config.json`의 해당 토큰 항목에 넣습니다.
3. 다시 배포한 뒤 소유확인을 완료하고 **사이트 주소 + `/sitemap.xml`**을 제출합니다. 토큰은 확인 후에도 유지합니다.

사이트맵에는 아카이브 자체 URL만 포함합니다. 프로젝트 하위 경로로 배포할 때 `robots.txt`는 호스트 루트 사이트의 설정도 확인해야 합니다.

## 알아둘 점

- RSS에 있는 최근 글만 수집합니다. 카테고리 전체나 RSS에서 빠진 과거 글은 수집하지 않습니다.
- 날짜·카테고리가 없으면 임의로 만들지 않습니다. 화면 날짜는 한국 시간 기준이며 날짜 없는 글은 목록 끝에 표시합니다.
- 예약 실행은 지연·누락될 수 있고, 공개 저장소에서 60일간 활동이 없으면 자동 비활성화될 수 있습니다. Actions에서 상태를 확인하세요.
- 사이트 등록이나 사이트맵 제출이 네이버 원문의 색인 또는 검색 순위를 보장하지 않습니다.

## 실행과 자동 배포

Python 3.10 이상이 필요하며 별도 패키지 설치는 없습니다.

```bash
python scripts/archive.py validate
python -m unittest discover -s tests -v
python scripts/archive.py collect
python scripts/archive.py build
python -m http.server 8000 --bind 127.0.0.1 --directory _site
```

`http://127.0.0.1:8000/`에서 확인합니다. Windows에서는 `python` 대신 `py -3`도 사용할 수 있습니다.

수집 없이 기존 데이터로 사이트만 생성하려면 `build`만 실행합니다. 자동화는 **설정 검사 → 테스트 → RSS 수집 → 사이트 생성 → 데이터 커밋·푸시 → Pages 배포** 순서이며, 봇 커밋이 별도 워크플로를 시작하는 데 의존하지 않습니다. 설정 검사에서 JSON 오류가 나면 표시된 줄과 바로 앞줄의 쉼표·큰따옴표를 확인하고, 수정한 파일을 main에 커밋·푸시합니다.

- **1시간마다 매시 00분**, main 푸시, 수동 실행 시 수집·배포합니다. 헤더에도 업데이트 주기를 표시합니다.
- 주기는 `.github/workflows/pages.yml`의 `cron: '0 * * * *'`(UTC)을 수정합니다. 주기를 바꾸면 `scripts/archive.py`의 헤더 안내도 함께 수정하세요. 예약 실행은 지연될 수 있어 정확히 1시간 간격의 완료를 보장하지 않습니다.
- RSS 주소는 `BLOG_ID`로 생성합니다. 별도 주소가 필요하면 `config.json`에 `RSS_URL`을 추가할 수 있습니다. 네이버 RSS와 설정한 블로그의 글만 지원합니다.
- 게시글 또는 수집 시간 데이터가 변경되면 커밋하고, 같은 워크플로에서 Pages를 배포합니다. 새 글이 없어도 성공한 수집 시간은 기록됩니다. 수집 실패 시 기존 데이터와 시간을 유지해 배포합니다.
- Actions의 `contents: write` 권한과 봇의 main 푸시가 허용되어야 합니다. 브랜치 보호 규칙 및 `github-pages` Environment의 배포 허용 설정을 확인하세요.
- 빌드 중 main이 변경되어 중단된 경우 최신 main에서 다시 실행합니다.

수집 요청은 실행당 한 번이며 30초 제한과 5 MB 응답 제한을 적용합니다. 네트워크·XML 오류나 유효한 결과 0개는 기존 파일을 보존하고 Actions에 경고를 남깁니다. 저장된 JSON 자체의 손상이나 빌드 오류는 배포를 중단합니다.
