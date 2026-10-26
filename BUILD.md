# Windows 빌드와 포터블 패키징

저장소 루트에서 PowerShell을 사용합니다. 아래 경로는 이 저장소 기준입니다.
Windows x64, Python 3.12, Microsoft Edge, .NET Framework 컴파일러가 필요합니다.
실제 배포본은 이 순서로 제작했지만 **깨끗한 다른 PC에서 아래 전체 명령을 재실행한 검증은 아직 없습니다.**

## 1. Python 환경

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

## 2. WebView2 SDK DLL 준비

이진 DLL은 Git에 포함하지 않습니다. Microsoft 공식 NuGet SDK에서 가져옵니다.

```powershell
New-Item -ItemType Directory -Path work -Force | Out-Null
Invoke-WebRequest 'https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/1.0.4314.35/microsoft.web.webview2.1.0.4314.35.nupkg' -OutFile work/webview2.zip
Expand-Archive work/webview2.zip work/webview2-sdk
Copy-Item work/webview2-sdk/lib/net462/Microsoft.Web.WebView2.Core.dll .
Copy-Item work/webview2-sdk/lib/net462/Microsoft.Web.WebView2.WinForms.dll .
Copy-Item work/webview2-sdk/runtimes/win-x64/native/WebView2Loader.dll .
```

라이선스를 보존하세요. SDK `desktop/WebView2-LICENSE.txt`는 저장소에 있습니다.

## 3. 데스크톱 셸

```powershell
.\desktop\build.ps1
.\제작으로부자되자.exe
```

빌드는 app.ico를 EXE에 넣습니다. 아이콘을 다시 만들 때는 assets/app-icon.png를 Pillow로 16~256px 다중 크기 ICO로 변환하세요.
이 실행 파일은 같은 위치의 .venv/Python 소스 또는 engine/engine.exe를 사용합니다.
같은 8765 포트를 쓰는 기존 웹/앱은 먼저 닫으세요.

## 4. Python 엔진 묶기

```powershell
$iconPath = (Resolve-Path app.ico).Path
.venv\Scripts\python.exe -m PyInstaller --noconfirm --onedir --console --name engine --icon $iconPath --distpath work/portable-dist --workpath work/portable-build --specpath work app.py
```

`--specpath work`이므로 아이콘에는 절대 경로를 전달해야 합니다.
Playwright는 외부에 설치된 Edge를 사용하므로 Chromium 전체를 별도로 설치하거나 묶을 필요가 없습니다. Node 드라이버는 PyInstaller가 함께 수집합니다.

## 5. 설치 없는 WebView2 준비

Microsoft 공식 Fixed Version x64 CAB을 다운로드하고 전체를 압축 해제합니다.
당시 사용한 버전은 155.0.4283.62입니다. 공식 API에 과거 버전이 없으면 제공되는 버전을 사용하고 재검증하세요.

```powershell
$entries = Invoke-RestMethod 'https://developer.microsoft.com/microsoft-edge/api/webview2'
$selected = $entries | Where-Object version -eq '155.0.4283.62' | Select-Object -First 1
if (-not $selected) { throw 'Select an available official runtime version and verify it.' }
$download = $selected.builds | Where-Object architecture -eq 'x64' | Select-Object -First 1
Invoke-WebRequest $download.url -OutFile work/webview2-fixed.cab
New-Item -ItemType Directory -Path work/fixed-runtime -Force | Out-Null
expand.exe work/webview2-fixed.cab -F:* work/fixed-runtime
```

## 6. 개인 자료 없는 배포 폴더

```powershell
.venv\Scripts\python.exe desktop/package_portable.py --engine work/portable-dist/engine --runtime work/fixed-runtime/Microsoft.WebView2.FixedVersionRuntime.155.0.4283.62.x64 --output dist/제작으로부자되자
```

출력 폴더는 **없어야** 합니다. 기존 사용자의 data를 덮어쓰지 않도록 재실행은 거절합니다.
패키징은 허용한 파일·licenses와 빈 data만 복사합니다. .venv나 개발자의 data를 복사하지 않습니다.

## 7. 실제 사이트를 열지 않는 검증

```powershell
.venv\Scripts\python.exe -m unittest discover -p "test_*.py" -q
node test_ui.js
.\dist\제작으로부자되자\engine\engine.exe --portable-self-test
.\dist\제작으로부자되자\제작으로부자되자.exe --smoke-test
```

엔진 점검은 계산과 외부 Edge about:blank만 사용합니다. 셸 점검은 계산 창과 동봉 WebView2를 확인합니다.
실행 창을 닫고 8765 포트가 해제되는지 확인하세요.
점검 실행 후 data에 브라우저 프로필이 생깁니다. **테스트한 폴더의 data를 ZIP에 넣지 마세요.**
배포용은 새 폴더에 다시 패키징하거나 ZIP 생성 시 data 내용은 제외하고 빈 data 디렉터리만 넣으세요.

포터블 ZIP은 대형 파일이므로 Git 커밋 대신 GitHub Releases 첨부나 별도 파일 전달을 사용합니다.
이 저장소에 포터블 Releases 게시를 자동화한 기능은 없습니다.
