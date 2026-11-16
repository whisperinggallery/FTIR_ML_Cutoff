# Windows 폴더 → GitHub 자동 동기화

동기화는 **내 Windows 컴퓨터**에서 실행됩니다. Codex 클라우드는 내 컴퓨터의 폴더나 작업 스케줄러에 접근하지 않습니다. 아래 설치 스크립트를 직접 실행해야 합니다. 기존 FTIR 분석 명령과 GitHub Actions는 그대로 사용합니다.

## 1. 설치 준비

1. [Python 공식 사이트](https://www.python.org/downloads/windows/)에서 Python 3.12 이상을 설치하세요. 설치 화면의 **Add Python to PATH**와 Python Launcher를 활성화하세요.
2. [Git for Windows](https://gitforwindows.org/)를 설치하세요. 기본 설치에 포함된 **Git Credential Manager(GCM)**를 선택하고, Git을 명령줄에서도 사용할 수 있는 기본 옵션으로 설치하세요.
3. GitHub 계정으로 저장소에 쓰기 권한이 있는지 확인하세요. 기본 브랜치 보호 규칙이 직접 Push를 막는 경우 아래의 별도 브랜치 안내를 따르세요.
4. 시작 메뉴에서 **Windows PowerShell**을 새로 여세요. 관리자 권한은 필요하지 않습니다. 다음 명령으로 설치를 확인하세요.

```powershell
py -3 --version
git --version
git credential-manager --version
```

이 기능은 Python 3.10 이상에서도 동작합니다. Windows 작업 스케줄러 스크립트는 Windows PowerShell 5.1 이상을 대상으로 합니다.

## 2. 동기화 전용 Clone 만들기

먼저 이 기능이 포함된 코드가 GitHub의 사용할 브랜치에 반영되어 있어야 합니다. 개발 중인 Clone과 별개로 **동기화 전용 Clone**을 하나 만드세요. GitHub 저장소는 서버에 있는 원본이고, Clone은 내 컴퓨터에 만드는 복사본입니다.

```powershell
New-Item -ItemType Directory -Force C:\FTIR | Out-Null
cd C:\FTIR
git clone https://github.com/whisperinggallery/FTIR_ML_Cutoff.git FTIR-sync
cd C:\FTIR\FTIR-sync
git config user.name "내 이름"
git config user.email "내 GitHub 이메일 또는 GitHub noreply 이메일"
```

코드가 아직 main에 없다면, 기능이 포함된 브랜치를 체크아웃해야 합니다. `git branch -r`로 원격 브랜치 목록을 확인하세요. 자동 동기화는 **현재 브랜치가 추적하는 origin 브랜치**로 Push합니다. main에 강제로 합치거나 브랜치를 자동 생성하지 않습니다.

main 직접 Push가 금지된 저장소라면, 기능이 포함된 브랜치에서 다음처럼 별도의 데이터 브랜치를 준비할 수 있습니다.

```powershell
git switch -c measurement-data
git push -u origin measurement-data
```

현재 Actions는 브랜치에 상관없이 data 변경 Push를 분석합니다. 이 경우 데이터는 measurement-data에 보관되며 main으로의 병합은 별도 Pull Request로 진행하세요. 해당 브랜치에도 Push 권한이 필요합니다.

전용 Clone에는 측정 원본을 직접 저장하거나 코드 수정·다른 작업의 커밋을 하지 마세요. 원본 폴더는 Clone **밖에** 둡니다. 이미 Git 변경이 있으면 자동 동기화가 안전을 위해 멈춥니다.

## 3. GitHub 인증

Git for Windows의 GCM을 설정하고, 최초 인증은 사람이 직접 수행합니다.

```powershell
git credential-manager configure
git fetch origin
git push --dry-run origin HEAD
```

브라우저 로그인 안내가 나오면 본인 GitHub 계정으로 로그인하고, 조직 계정이면 필요한 SSO 인증도 완료하세요. 마지막 명령은 원격 데이터를 변경하지 않는 Push 점검입니다. 실제 Push에는 브랜치 보호나 서버 규칙이 추가로 적용될 수 있습니다.

**GitHub 비밀번호나 토큰을 Python 코드, JSON 설정, 원격 URL에 쓰지 마세요.** GCM이 Windows의 안전한 자격 증명 저장소를 이용합니다. 자동 실행에서는 대화형 로그인 창을 열지 않습니다. 인증이 만료되면 수동 터미널에서 위 명령으로 다시 로그인하세요. `git credential-manager` 명령이 없다면 Git for Windows 설치를 수정하여 GCM을 추가하세요.

## 4. 폴더 선택과 2분 자동 실행 등록

예를 들어 다음 두 폴더를 미리 만드세요. 서로 다른 드라이브나 위치여도 됩니다.

- Raw: `D:\FTIR measurements\original`
- Processed: `E:\FTIR work\trimmed`

전용 Clone에서 실행하세요.

```powershell
cd C:\FTIR\FTIR-sync
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\scripts\Install-Sync.ps1
```

회사 정책이 스크립트 실행을 막는다면 관리자에게 허용을 요청하세요. 정책을 강제로 우회하지 마세요. 다운로드된 ZIP의 스크립트라면 내용을 확인하고 파일 속성에서 차단 해제해야 할 수 있습니다. Git Clone으로 받는 방식을 권장합니다.

설치 스크립트는 다음 작업을 수행합니다.

1. Git/GCM과 원격 접근을 확인합니다.
2. Raw folder, Processed folder의 전체 경로를 각각 묻습니다. 경로에 공백·한글이 있어도 됩니다.
3. 전용 Clone의 `.venv`에 필요한 Python 패키지를 설치합니다.
4. `%LOCALAPPDATA%\FTIR-Sync\<Clone별 ID>\config.json`에 경로를 저장합니다. 기존 설정은 날짜가 붙은 백업을 만듭니다. 비밀번호·토큰은 저장하지 않습니다.
5. 동기화를 한 번 실행합니다. **이때 안정된 완성 파일 쌍은 실제로 커밋·푸시됩니다.** 실패하면 자동 작업 등록 전에 중단하므로 원인을 해결한 뒤 다시 실행하세요.
6. 최초 실행이 성공하면 `FTIR-Sync-<ID>`라는 이름으로 **2분 간격** 작업을 등록합니다.

작업은 로그인한 현재 Windows 사용자 권한으로 실행됩니다. 로그아웃·절전·컴퓨터 전원 꺼짐 상태에서는 주기 실행되지 않습니다. 네트워크 드라이브도 그 사용자에게 접근 가능해야 합니다. 실행 중인 작업이 있으면 새 작업은 중복 실행하지 않습니다. 비밀번호를 저장해 로그아웃 상태에서 실행하도록 설정하지 않습니다. 실행 중 짧게 콘솔 창이 보일 수 있습니다.

폴더를 변경하려면 먼저 아래 방법으로 작업을 중지하고 설치 스크립트를 다시 실행하세요. 같은 Clone의 작업 이름을 재사용합니다.

## 5. 어떤 파일이 반영되나요?

- 각 지정 폴더의 **바로 아래** 파일만 봅니다(하위 폴더는 검색하지 않음).
- `DL1.dpt`와 `DL1.dat`처럼 확장자를 제외한 이름이 정확히 같은 쌍만 처리합니다. 확장자의 대소문자는 무시합니다. 이름 대소문자가 다르거나 대소문자만 다른 중복 파일이 있으면 추측하지 않고 보류합니다.
- `.dpt`만 있거나 `.dat`만 있으면 아무것도 업로드하지 않습니다. 다음 실행에서 다시 확인합니다.
- 최근 5초 안에 수정된 파일은 보류합니다. 후보 파일은 5초 간격의 크기·수정 시각 확인과 두 번의 내용 읽기를 통과해야 합니다. 읽기 잠금·내용 변경·빈 파일은 다음 실행으로 넘깁니다.
- 파일명에 대소문자 구분 없이 `bolo`가 있으면 새 시료 detector를 Bolometer, 없으면 DTGS로 등록합니다.
- 시료 ID는 기존 분석기와 동일하게 영문·숫자로 시작하는 영문·숫자·`_`·`.`·`-`만 지원합니다. 예: `DL2_Bolo`. 이 규칙 밖의 파일은 로그를 남기고 보류합니다.
- 기존 sample_id의 detector, human_cutoff_cm1, 사용자 추가 열과 값은 유지합니다. 새 행이 없으면 metadata 파일 자체를 다시 쓰지 않습니다. 기존 파일 대응과 충돌하면 해당 쌍을 보류합니다. 중복 sample_id나 파일 배정도 임의로 정리하지 않습니다.
- 내용이 바뀐 파일만 반영합니다. 시각만 바뀌었거나 같은 내용이면 커밋하지 않습니다.
- 새 파일 쌍과 변경된 파일은 한 번의 실행에서 **한 커밋·한 Push**로 묶습니다. 커밋 대상은 `data/raw`, `data/processed`, `data/metadata.csv`뿐입니다. 삭제는 전파하지 않습니다.
- 원본의 수정·삭제·이름 변경은 GitHub 데이터를 자동으로 삭제하지 않습니다. 이름을 바꾼 완성 쌍은 새 시료가 될 수 있습니다.

**복사 완료의 한계:** 파일 크기와 시각이 잠시 멈췄다고 생산 프로그램의 저장 작업이 영구히 끝났음을 증명할 수는 없습니다. 저장 중 5초 이상 멈추는 프로그램이라면 config.json의 `stability_seconds`를 30 또는 60으로 늘리세요(허용 범위 1–60초). 가장 확실한 방법은 임시 확장자로 저장한 후 두 파일의 쓰기를 모두 끝내고 최종 `.dpt`/`.dat` 이름으로 바꾸는 것입니다. 스펙트럼 두 열 형식 등 과학적 데이터 검증은 기존 GitHub 분석 단계에서 수행합니다.

## 6. 정상 동작 확인

1. 설치가 출력한 작업 이름, config.json 경로, sync.log 경로를 기록하세요.
2. Raw 폴더에 `DL1.dpt`만 넣고 기다립니다. GitHub에는 아직 올라가지 않아야 합니다.
3. Processed 폴더에 `DL1.dat`를 완전히 저장하고 약 2–4분 기다립니다.
4. GitHub 현재 동기화 브랜치에서 두 파일과 metadata.csv를 확인합니다. `Sync FTIR measurement data` 커밋이 생깁니다.
5. **Actions → FTIR analysis and tests**에서 실행을 확인하고 **Artifacts → ftir-results**를 다운로드합니다. 테스트용 파일도 기존 분석기에 맞는 숫자 두 열 스펙트럼이어야 합니다.
6. 변경 없이 기다렸을 때 불필요한 새 커밋이 생기지 않는지 확인합니다.

작업 스케줄러(`taskschd.msc`)의 작업 스케줄러 라이브러리에서도 `FTIR-Sync-...` 작업을 찾을 수 있습니다. 마지막 실행 결과 0은 프로그램 정상 종료이며, 보류된 쌍도 있을 수 있으므로 로그를 함께 확인하세요. 작업이 아직 실행 중이면 완료 후 결과를 확인하세요.

수동 실행 예시(경로는 설치 시 출력된 실제 값으로 바꾸세요):

```powershell
.\.venv\Scripts\python.exe -m ftir_cutoff.sync --config "$env:LOCALAPPDATA\FTIR-Sync\실제ID\config.json"
Get-Content "$env:LOCALAPPDATA\FTIR-Sync\실제ID\sync.log" -Tail 30
```

로그는 최대 약 2 MB씩 4개까지 순환 보관합니다. Git 오류는 명령 종류와 종료 코드로 기록하며, 자격 증명이 포함될 수 있는 원격 URL·Git stderr 전체는 로그에 복사하지 않습니다.

## 7. 자동 실행 중지·재개·제거

`FTIR-Sync-실제ID`를 설치 시 출력된 작업 이름으로 바꾸세요.

```powershell
# 앞으로의 실행 중지. 실행 중인 작업은 완료될 때까지 기다리세요.
Disable-ScheduledTask -TaskName 'FTIR-Sync-실제ID'
# 다시 활성화
Enable-ScheduledTask -TaskName 'FTIR-Sync-실제ID'
# 지금 한 번 실행
Start-ScheduledTask -TaskName 'FTIR-Sync-실제ID'
# 예약 작업만 제거 (측정 데이터와 설정은 유지)
Unregister-ScheduledTask -TaskName 'FTIR-Sync-실제ID' -Confirm:$false
```

쓰기·커밋 도중 강제로 프로세스를 종료하는 대신 주기 실행을 끄고 현재 실행을 기다리세요.

## 8. 오류 해결: 자동으로 덮어쓰거나 강제 Push하지 않습니다

- **인증·네트워크 실패:** 로그에서 `Git fetch failed` 또는 `Git push failed`를 확인하세요. 터미널에서 `git fetch origin`, `git push --dry-run origin HEAD`를 실행해 구체적인 오류와 GCM 인증을 확인하세요. 프로그램은 비밀번호를 묻지 않습니다. 일반적인 Push 실패 후에는 로컬 커밋을 보존하고 다음 실행에서 같은 커밋을 재전송합니다.
- **브랜치 보호:** 허용된 데이터 브랜치로 변경하고 원격 추적 설정을 확인하거나 저장소 관리자에게 요청하세요. 프로그램은 강제 Push하지 않습니다.
- **전용 Clone의 미커밋 변경:** `git status`로 확인하세요. 자동화가 사용자 변경을 커밋하거나 지우지 않습니다. 필요한 작업을 별도로 보관하고 Git 상태를 정리하세요.
- **로컬의 예상치 못한 커밋·원격 동시 변경:** 자동 실행을 중지하고 `git status`, `git log --oneline --graph --all -10`을 확인하세요. 프로그램은 원격을 fetch하고 fast-forward 가능한 변경만 받으며, 자동 rebase·충돌 해결·reset --hard는 하지 않습니다. 실패한 Push 뒤 원격에도 새 커밋이 생겼다면 사람이 먼저 이력을 통합해야 합니다. Git이 익숙하지 않다면 Clone을 보존하고 도움을 요청하세요. 원본 측정 폴더를 지우지 마세요.
- **커밋 전 오류:** 이 실행이 쓴 데이터만 이전 상태로 복원합니다. Push 실패 시에는 이미 생성된 로컬 커밋을 유지합니다. 디스크 오류·강제 종료로 복원이 불가능하면 다음 실행은 dirty 상태를 감지하고 중단합니다. 자동 삭제나 강제 복구를 하지 않습니다.
- **동기화는 되지만 Actions 실패:** metadata 파일 대응, 스펙트럼 형식, 파수축 겹침·대응 경고를 기존 README와 Actions의 summary.csv에서 확인하세요. 동기화는 두 파일의 과학적 대응까지 추정하지 않습니다.

코드를 업데이트할 때는 예약 실행을 중지하고 전용 Clone이 깨끗한지 확인한 후 `git pull --ff-only`와 `.\.venv\Scripts\python.exe -m pip install -e .`을 실행하고 다시 활성화하세요.
