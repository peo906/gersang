# 다음 작업자를 위한 인수인계

기준일: **2026-10-26 (한국 시간)**. 프로그램명: **제작으로부자되자**.
이 파일은 대화 기록 없이 이 저장소를 분석·수정하기 위한 현재 상태 설명서다.

## 목표와 현재 상태

거상에서 재료가 저렴할 때 제작해 육의전에 판매하려는 사용자를 위한 로컬 도구다.
청룡 서버, 거니버스 제작법, 거타 육의전 매물을 사용한다.
웹 버전 → 내부 브라우저를 넣은 데스크톱 버전 → **계산 화면만 내장하고 조회는 외부 Edge를 쓰는 데스크톱 버전**으로 전환했다.
내부 WebView2로 거타를 열었을 때 접근 차단이 발생했기 때문에 내부 외부 조회 방식을 다시 도입하지 말 것.
사용자의 일반 브라우저와 자동 조회 전용 Edge 프로필은 서로 다르며, 외부 Edge 전환으로 차단 해제가 보장되지는 않는다.

현재 소스에는 웹 실행, C# 데스크톱 셸, 포터블 패키징 도구, 테스트가 모두 있다.
제작으로부자되자.exe의 창 제목·내부 브랜드·아이콘이 변경됐고, Python을 함께 묶은 포터블도 로컬에서 제작했다.
아이콘은 `assets/app-icon.png`, Windows 다중 크기 ICO는 `app.ico`다.
내부 구현의 예전 GersangWorkshopDesktop mutex 이름 등은 호환을 위해 남아 있다.

## 파일 지도

| 파일 | 역할 |
|---|---|
| app.py | 127.0.0.1:8765 HTTP 서버, 상태·조회 작업·계산·저장·데스크톱 종료 API |
| collector.py | 제작법·매물 DOM 수집, 파서, JSON 캐시, 페이지 추가 조회, 검색 표기 후보, 조회 간격 |
| optimizer.py | scipy.optimize.milp 정수계획으로 재료 수지·매물 수량·제작 선택을 함께 계산, 판매가 비교 |
| index.html | 프레임워크 없는 단일 HTML/CSS/JS. 트리·보유분·판매가·완료일·최근 조회 표시 |
| desktop/Program.cs | .NET Framework WinForms + WebView2 계산 창. 숨긴 Python 서버 실행·종료 |
| desktop/build.ps1 | C# 5 컴파일러로 제작으로부자되자.exe 생성 |
| desktop/package_portable.py | 허용한 파일만 새 배포 폴더로 복사, 개인 data와 .venv 제외 |
| desktop/포터블-사용방법.txt | 공유 ZIP의 먼저읽기.txt 원본 |
| portable_check.py | 포터블 엔진 계산·대체 검색·외부 Edge 빈 페이지 자가 점검 |
| test_app.py | 계산·캐시·파서·페이지·문자 후보·조회 간격 등 회귀 검사 |
| test_desktop.py | 외부 Edge 실행 경로·실패 정리·배포 파일 필터 검사 |
| test_ui.js | Node VM으로 브라우저 UI 계산 로직 오프라인 검사 |

`desktop_browser.py`와 내부 조회용 Python↔WebView2 명령 브리지는 제거했다. 다시 import하면 안 된다.

## 반드시 유지할 조회 동작

1. 검색한 최상위 아이템의 **직속 재료**부터 보여준다. 전체 제작 트리를 무조건 재귀 조회하지 않는다.
2. 하위 재료는 사용자가 제작법/하위 재료 보기를 눌렀을 때만 불러온다. 이미 저장된 제작법은 재사용한다.
3. 트리는 들여쓰기와 연결 구조로 표현한다. 폴더 이모지는 쓰지 않는다.
4. 트리 펼치기와 계산 반영은 별개다. 재료별 구매 / 하위 재료 제작 / 자동 비교 선택으로 실제 비용 경로를 결정한다.
5. 저장 DB에 TTL이 없다. 제작법과 가격은 사용자 갱신 요청 전까지 재사용한다. 새 재료·부족 수량에 필요한 추가 페이지는 예외적으로 읽는다.
6. 외부 요청은 단일 작업에서 순차 처리하며 **조회 완료 후 최소 1.5초**를 기다린다. 페이지가 오래 걸린다고 동시에 다음 요청을 시작하지 않는다.
7. HTTP 거절·보안 확인·통신/파서 오류는 매물 없음으로 간주하지 않고 중단한다. 무한 재시도·차단 우회를 구현하지 않는다.

### 수량과 가격

- 재료 매물은 `searchType=synchronized`, `orderType=price`, `orderDirection=asc`로 읽는다.
- 1페이지 실제 수량이 부족하면 다음 페이지를 확인하며 필요한 수량이 모이면 멈춘다. 1페이지만 강제하는 규칙은 폐기됐다.
- 전체 마지막 페이지까지 부족하면 `searchType=archived`, 최신순 첫 페이지의 **첫 번째 매물 단가**를 부족분 참고가로 사용한다. 기록 매물 수량은 무시한다.
- 현재 매물을 우선 사용하고 부족분만 참고가로 채운다. 참고가가 더 싸다고 현재 매물을 전부 참고가로 대체하면 안 된다.
- 참고가 적용 재료 이름 옆에는 빨간 `※매물부족` 표시. 현재·참고 매물 모두 없으면 어떤 재료가 부족한지 명확히 표시한다.
- 200페이지를 넘으면 불완전한 자료를 전체 매물로 취급하지 않고 중단한다.
- 완제품 판매 단가는 별도 조회 버튼으로 확인한다. 현재 매물이 없으면 과거 최신순 첫 페이지 단가들의 **단순 산술평균**을 예상치로 쓴다. 재료의 첫 금액 규칙과 다르다.
- 완제품 매물가 카드 우하단에 마지막 조회 시각을 표시한다. 매물가는 체결가가 아니며 차익은 판매 공제·향후 시세 변동을 반영하지 않는다.
- 보유 재료는 필요한 구매량에서 차감하고 추가 지출 0원으로 취급한다. 공유 재료와 보유량은 전체 제작 계획에서 중복 사용하지 않는다.
- 제작 산출량·수수료를 반영한다. 페이지에 산출량이 없으면 1개로 처리하고 UI에서 수정할 수 있다.

### 띄어쓰기와 호환 한자

문제 사례: `정기의구슬조각(雷)`는 결과 없음, `정기의구슬조각(雷)`는 결과 있음.
`雷`=U+96F7, `雷`=U+F949. 문자 코드가 달라 거타 검색에서 다르게 취급된다.

- 원래 이름 → 공백 제거 이름 → Unicode CJK 호환 한자 변형 순서로 검색한다. 명시적 결과 없음/제작법 404일 때만 다음 후보로 간다.
- collector.COMPAT_HANJA는 U+F900–FAFF 및 U+2F800–2FA1F 범위를 시작 시 한 번 스캔해 NFC 대응표를 만든다.
- 후보는 여러 글자의 조합과 공백 변형을 포함해 **최대 8개**. 모든 조합을 확인하는 기능은 아니다.
- collector.same_name으로 NFC와 공백 제거 후 이름을 비교한다.
- optimizer.key는 공백만 제거한다. 기존 data/database.json 키를 보존하기 위해 전역 한자 정규화로 바꾸지 않았다.
- 성공한 매물 검색 이름은 marketSearchName으로 DB에 보존하고 다음 페이지와 다음 작업에서 재사용한다.
- 과거에 저장한 빈 매물은 자동 재조회하지 않는다. 사용자가 재료 시세 갱신을 눌러야 새 후보 검색이 적용된다.
- ‘비슷하게 생긴 모든 한자’를 판별하지 않는다. Unicode에 같은 글자로 정의된 호환 한자만 대상이다.

### 제작 완료일

거니버스 제작법의 `소요기간 X일`을 읽어 durationDays에 저장한다. 사용자가 직접 기간을 입력하는 방식이 아니다.
한국 날짜로 오늘 0일, 내일 1일. 예: 2026-10-26에 3일 제작을 맡기면 2026-10-29 완료 예상.
하위 재료 제작과 대기 시간은 이 날짜에 자동 합산하지 않는다. 기간을 못 읽으면 모름으로 표시한다.

## 데이터와 API

- data/database.json: version=1, items 딕셔너리. 이름 키 아래 recipes, children, offers와 조회 시각/페이지/참고가를 누적 저장.
- recipes: materials(이름 키→수량), fee, output, durationDays, method.
- offers: price, quantity, seller, listed, page.
- marketScope, marketPageCount, marketLastPage, marketSearchName: 현재 매물 조회 범위와 성공한 검색 표기.
- fallbackQuote/fallbackCheckedAt: 재료 부족분의 과거 첫 매물 참고가.
- archivedQuote/archivedCheckedAt: 완제품 과거 첫 페이지 평균 및 표본.
- data/latest.json: 마지막 완료 화면. 작업 실패 시 기존 완료 화면은 보존한다. 개별 재료 조회 성공분은 DB에 먼저 저장될 수 있다.
- data/history.json: 최근 아이템 최대 5개.
- data/last-request.json: 직전 조회 대기 종료 시각. 재실행 시에도 간격을 유지한다.
- data/browser: 외부 Edge 전용 프로필. 개인 평소 브라우저 프로필과 별개.
- data/desktop/calculator: 내부 계산 화면 프로필. 보유 수량·수동 단가 등 localStorage가 저장된다.

GET /api/state; POST /api/scan, /api/calculate, /api/stop, /api/history.
scan mode: search / market / recipe / expand / finished / fallback.
화면 변경 요청은 로컬 Host·Origin·X-Local-Token·JSON 형식을 검증한다. 외부 인터페이스에 서버를 바인딩하지 말 것.
POST /api/desktop/close는 별도 임의 GERSANG_DESKTOP_TOKEN으로 인증하고 자기 서버를 종료한다.

## 실행·빌드·검증

README.md와 BUILD.md 참조. 웹은 start.bat 또는 Python app.py, 데스크톱은 C# 빌드한 EXE.
Python 3.12 + scipy 1.15.3 + Playwright. 검증된 정확한 환경은 requirements-lock.txt.
배포는 PyInstaller onedir 엔진 + WinForms EXE + DLL + WebView2 Fixed Runtime. EXE 하나만 배포하는 방식은 아니다.
PyInstaller 엔진이 root/engine/engine.exe에 놓이므로 frozen 모드의 ROOT는 실행 파일의 상위 두 단계다. 일반 실행 ROOT는 소스 폴더다.
설치된 WebView2가 있으면 우선 사용하고, 없으면 동봉 Runtime을 사용한다. --smoke-test는 동봉 Runtime을 우선 사용해 검증한다.
Windows 10 Fixed Runtime에는 AppContainer의 읽기·실행 권한이 필요하여 동봉 Runtime 폴더에만 부여한다. 네트워크 UNC 경로에서는 실행하지 않는다.
포터블 묶음에는 data가 빈 디렉터리로 포함된다. 제작자의 쿠키·조회 기록·보유량을 포함하면 안 된다.

2026-10-26 검증 결과:

- Python unittest **61개 통과**.
- node test_ui.js 통과.
- 동봉 Python 엔진 --portable-self-test: 210 비용 계산, 호환 한자, 외부 Edge about:blank DOM 읽기 통과.
- 동봉 WebView2 Runtime 사용 데스크톱 실행과 새 제목·얼굴 아이콘 확인.
- 데스크톱 종료 후 8765 서버 포트 해제 확인.
- 포터블 ZIP 무결성 검사, data 파일 0개 확인.

실제 거타/거니버스 조회는 최종 변경 후 수행하지 않았다. 다른 PC/Windows 10에서 별도 검증하지 않았다.
이 결과를 실제 사이트 차단 해결이나 모든 PC 지원이 확인됐다는 뜻으로 보고하면 안 된다.

## 전달·배포 상태

로컬에서 포터블 ZIP 약 390MB(408800670 bytes)를 제작했다. SHA-256:
`8fee878d33fce82442bc14509162d785361bdb2c09d24744a5affe1ba1282189`.
이 체크섬은 당시 묶음에만 해당하며 다시 빌드하면 바뀐다. ZIP·DLL·Runtime·.venv는 이 소스 저장소에서 제외한다.
과거 5초/1초 웹 버전 체크포인트는 원래 작업 PC에만 있으며 이 저장소에 없다.
이전 프로그램명은 청룡공방. 원본 PC의 기존 사용자 자료는 유지한 채 새 EXE를 만들었다.
소스는 GitHub에 올려 다른 컴퓨터에서 클론해 이어갈 수 있지만 대화 기록과 로컬 개인 DB가 자동 이동하지는 않는다.

## 현재 제한과 다음 변경 시 주의

- 외부 사이트 DOM 의존. 표 구조·문구가 바뀌면 안전하게 중단하고 파서 fixtures를 갱신해야 한다.
- 호환 한자 조합 최대 8개, 매물 페이지 최대 200개. 의도한 조회량 상한이다.
- 오래된 캐시 가격·과거 참고가·판매 매물가로 수익을 보장할 수 없다.
- 포터블 Fixed Runtime은 155.0.4283.62로 자동 갱신되지 않는다. 재배포할 때 공식 배포판 갱신을 검토한다.
- 최초 포터블 실행은 빈 DB다. 집에서 개인 기록을 이어가려면 앱 종료 후 자신의 data를 별도로 안전하게 옮긴다. 공개 GitHub에는 넣지 않는다.
- 아이콘의 원작 캐릭터 권리는 원작자에게 있다. 임의의 오픈소스 라이선스를 추가하지 않았다.
