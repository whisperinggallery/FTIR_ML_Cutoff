# FTIR reflectance cutoff 자동 분석

원본 상대 반사율(`.dpt`)과 보정·절단된 반사율(`.dat`)의 **파수축**을 비교하여 저파수 cutoff를 기록하는 Python 프로그램입니다. 원본 smoothing, local RMS noise, local slope를 계산하고 CSV와 PNG로 저장합니다. 머신러닝 학습 기능은 포함하지 않습니다.

## 1. 파일 준비

```text
data/
  raw/            # 원본 .dpt
  processed/      # 처리된 .dat
  metadata.csv    # 파일 대응 및 사람이 선택한 cutoff
```

두 스펙트럼 파일은 파수(cm⁻¹), 반사율 순서의 **숫자 두 열**이어야 합니다. 공백·탭·쉼표 구분, 오름차순·내림차순, 지수 표기(E 또는 D)를 지원합니다. 빈 줄과 `#` 또는 `;`로 시작하는 주석은 무시합니다. 일반 텍스트 헤더는 앞에 `#`를 붙이세요. 중복 파수, 비유한 값, 5개 미만의 점은 오류로 기록합니다. 반사율 단위(0–1 또는 %)는 입력 그대로 사용합니다.

`metadata.csv` 작성 예시(실제 파일을 준비한 후 행을 추가하세요):

```csv
sample_id,detector,raw_file,processed_file,human_cutoff_cm1
sample01,DTGS,sample01.dpt,sample01.dat,120
sample02,Bolometer,original02.dpt,corrected02.dat,
```

- `sample_id`: 중복 없이 영문·숫자로 시작하고 영문·숫자·`_`·`.`·`-`만 사용합니다. 실제 한글 시료명은 별도 `sample_name` 열에 보관할 수 있습니다(분석 출력은 sample_id 사용).
- `detector`: 정확히 `DTGS` 또는 `Bolometer`. 현재 detector별 임계값은 적용하지 않습니다.
- `raw_file`, `processed_file`: 각 폴더 바로 아래 파일명. 빈 칸이면 정확히 `sample_id.dpt`, `sample_id.dat`만 찾습니다.
- `human_cutoff_cm1`: 사람이 선택한 파수. 선택 사항이며 원본 파수 범위 안에 있어야 합니다.

유사한 이름으로 파일을 추측하지 않습니다. 동일 처리 파일을 여러 시료에 연결하거나 sample_id가 중복되면 해당 시료는 분석하지 않습니다. 서로 다른 cutoff 이력을 위해 같은 원본 파일을 여러 sample_id가 공유하는 것은 허용합니다. metadata에 연결되지 않은 파일도 `unassigned`로 보고합니다. 원본과 처리 데이터의 y값이 달라도 정상입니다.

## 2. 설치하고 실행하기

Python 3.10 이상이 필요합니다. 터미널에서 저장소 폴더로 이동한 뒤 실행하세요.

```bash
python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows PowerShell에서는 위 줄 대신: .venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m ftir_cutoff
python -m pytest -q
```

기본 입력은 `data/`, 출력은 `results/`입니다. 데이터가 하나도 없으면 헤더만 있는 summary.csv를 생성하고 정상 종료합니다. metadata가 없는데 스펙트럼이 있으면 대응을 추측하지 않고 unassigned로 보고합니다.

설정 예시:

```bash
python -m ftir_cutoff --data-dir data --output-dir results --axis-tolerance 0.01 --smoothing-window 11 --local-window 21
```

창 크기는 홀수, 최소 5이며 **파수 폭이 아닌 점 개수**입니다. 파수 간격에 따라 실제 smoothing 폭이 달라집니다. 종료 코드는 0(정상·축 대응 경고 또는 빈 데이터), 1(시료 오류/미대응 파일), 2(설정·metadata 구조 오류)입니다. 한 시료의 오류가 다른 시료 분석을 막지는 않습니다.

## 3. cutoff와 노이즈 지표의 의미

- 원본 `.dpt`의 파수 범위와 겹치는 처리 데이터만 축 비교에 사용합니다. MIR·다른 고주파 영역이 결합되어 있어도 전체 `.dat`를 거부하지 않습니다. 비교의 기본 절대 허용 오차는 0.01 cm⁻¹입니다.
- 처리 파일의 최저 파수는 항상 `cutoff_candidate_cm1`에 기록합니다. y값의 보정이나 차이는 축 판정에 사용하지 않습니다. 후보를 원본 범위에 억지로 맞추거나 보간해서 정답을 만들어 내지 않습니다.
- **축 검증:** 후보가 원본 범위 안에 있고, 저파수 시작점부터 최소 5개 처리점이 원본의 중복 없는 연속점과 허용 오차 내에서 대응하면 `axis_cutoff_cm1`에 기록합니다. `exact_overlap`은 겹침 전체가 대응하고, `exact_low_frequency_segment`는 저파수 시작 구간만 대응합니다. 후자는 뒤쪽 결합·격자 변경 가능성을 메시지와 로그에 알립니다. 5점은 짧은 우연 일치를 피하기 위한 운영 기준이며 통계적 신뢰도는 아닙니다.
- 이때 cutoff는 **남아 있는 가장 낮은 파수**입니다. 절단 경계는 그 점과 직전 원본 점 사이입니다(`cutoff_raw_lower_cm1`, `cutoff_raw_upper_cm1`). `axis_verified`는 축 경계를 확인했다는 뜻이며, 물리적 노이즈 한계나 파일의 실험적 동일성을 검증했다는 뜻은 아닙니다.
- **보간·불일치 축:** `resampled_or_unmatched`, `candidate_only`, `warning`으로 기록하고 `axis_cutoff_cm1`은 비웁니다. 최소 파수 후보와 원본의 양옆 파수는 보관하지만, 두 축만으로 보간 여부나 정확한 cutoff를 증명할 수 없으므로 사람이 확인해야 합니다. 이 경고는 분석 실패로 종료하지 않습니다.
- 겹침 없음(`no_overlap`), 5점 미만 겹침(`insufficient_overlap`), 처리 최저 파수가 원본보다 낮음(`extends_below_raw`)도 경고를 남기고 노이즈 CSV와 PNG를 저장합니다. 원본 저파수 끝까지 정확히 남아 있으면 `no_low_frequency_trim`이며 검증 cutoff는 빈 칸입니다.
- smoothing: 원본 파수 좌표에서 이동 창의 2차 다항식 최소제곱 적합. 불균일한 파수 간격도 지원합니다. 점이 부족하면 smoothing 창을 가장 큰 가능한 홀수로 줄입니다. 끝에서는 창을 안쪽으로 이동합니다.
- residual = 원본 − smoothing. Local RMS = 주변 창 residual의 제곱 평균의 제곱근입니다. Local slope = 주변 창의 smoothing 값에 대한 파수 기준 선형 회귀 기울기입니다. local 창은 양 끝에서 짧아집니다.
- RMS는 잔차 지표입니다. 실제 흡수 피크와 smoothing 설정의 영향도 받으므로 순수한 검출기 노이즈나 측정 불확도로 해석하지 마세요. 자동 품질 cutoff 결정은 수행하지 않습니다.

## 4. 결과 확인

- `results/summary.csv`: 시료별 detector, 상태, 오류 이유, 축 범위, cutoff, 사람 선택값, 대응 방식·확실성, 겹침 구간, 연속 대응 점 개수, 대응 오차, 점 개수와 창 설정. `axis_match_max_error_cm1`은 겹침 전체의 최근접 축 오차이고, `low_frequency_match_max_error_cm1`은 저파수 연속 대응 구간의 오차입니다. 둘 다 반사율이나 물리적 cutoff의 오차막대가 아닙니다.
- `results/run-*/시료명_diagnostics.csv`: 각 파수의 원본, smoothing, residual, RMS, slope.
- `results/run-*/시료명.png`: 원본·smoothing, RMS, slope의 세 패널. 빨간 점선은 축 검증 cutoff, 주황 점선은 미검증 후보, 초록 점선은 사람 선택값입니다. x축은 원본의 FIR 범위로 제한하여 결합된 MIR 때문에 저파수 그래프가 압축되지 않도록 합니다. 전체 처리 데이터의 파수 범위는 summary에 기록합니다. 처리 반사율은 별도 오른쪽 y축의 회색 선으로 표시합니다.

매 실행마다 별도 run 폴더를 만들어 이전 PNG와 구별합니다. summary.csv는 최신 실행으로 덮어씁니다. 오래된 run 폴더는 필요에 따라 삭제하세요. `results/`는 Git에 커밋하지 않습니다.

## 5. GitHub에서 자동 실행하기

1. GitHub 저장소에서 **Add file → Upload files**로 `data/raw/`, `data/processed/`에 파일을 올리고 metadata.csv를 수정하여 커밋합니다. 웹 업로드에서 폴더 위치를 확인하세요.
2. **Actions → FTIR analysis and tests**에서 실행 결과를 확인합니다. 데이터·코드·테스트·의존성 설정 변경 시 자동 실행되며, **Run workflow**로 수동 실행할 수도 있습니다.
3. 완료된 실행 화면의 **Artifacts → ftir-results**를 내려받아 압축을 풉니다. GitHub 로그인이 필요할 수 있습니다.

자동화는 Python 3.12에서 의존성 설치, 합성 데이터 테스트, 업로드 데이터 분석을 실행합니다. 잘못된 파일 대응이 있으면 실행을 실패로 표시하고, 생성된 summary와 다른 정상 시료 결과는 실패 시에도 업로드합니다. 실제 데이터가 없을 때는 테스트가 분석 기능을 검증하고 실제 분석은 빈 결과를 생성합니다. GitHub Actions에서의 실제 실행 여부는 커밋·업로드 후 확인해야 합니다.

의존성은 호환 버전 범위를 지정하며 완전한 버전 잠금은 하지 않았습니다. 장기 연구 재현성을 위해 사용한 입력 파일, 실행 옵션, 환경 버전을 함께 보관하세요.

## 6. Windows 로컬 폴더 자동 동기화

서로 다른 위치의 Raw `.dpt`와 Processed `.dat` 폴더를 지정하고, 이름이 일치하는 안정된 파일 쌍을 GitHub로 자동 반영할 수 있습니다. 파일명 `Bolo`로 detector를 판별하고 기존 사람 입력 metadata는 보존합니다. 로컬 삭제는 원격 삭제로 전파하지 않습니다.

**[Windows 설치·GitHub 인증·2분 자동 실행·중지 방법](docs/windows-sync.md)**을 순서대로 따라 하세요. 전용 Clone에서 `scripts/Install-Sync.ps1`을 실행하면 폴더 경로를 입력하고 Windows 작업 스케줄러를 등록할 수 있습니다. Windows 등록은 사용자의 컴퓨터에서 직접 실행해야 합니다.


## 7. 머신러닝 학습 정답 정리 (모델 학습 없음)

기존 분석 실행 시 `results/training_labels.csv`와 `results/training_groups.csv`를 자동 생성하며, GitHub Actions Artifacts에도 함께 포함합니다. 원본 `.dpt`의 파수·반사율 숫자가 같은 기록을 하나의 그룹으로 만들고 **검증된 cutoff의 최댓값**만 regression 정답으로 선택합니다. 낮은 값은 `superseded`, 불확실한 값은 `needs_review`로 남깁니다. 기존 입력 데이터는 보존합니다.

학습에 사용할 행은 반드시 `use_for_regression=true`로 필터링하고 `dataset_split`의 그룹 단위 train/test 배정을 따르세요. 상세 규칙·열 설명·실제 데이터 검증은 **[학습 정답 데이터셋 안내](docs/training-labels.md)**를 확인하세요. 모델 학습 기능은 포함하지 않습니다.

## 8. feat/·fix/ 브랜치의 Pull Request 자동 생성

`feat/새기능` 또는 `fix/버그수정` 형태의 브랜치에 Push하면 **Auto-create pull request** 워크플로가 `main`을 대상으로 PR을 생성합니다. 하위 경로가 있는 `feat/windows/sync`도 지원합니다. 기존 열린 PR이 같은 브랜치에 있으면 제목·본문을 덮어쓰거나 새 PR을 만들지 않습니다. main보다 앞선 변경이 없으면 생성하지 않습니다. 자동 Merge는 수행하지 않으므로 Checks와 변경 내용을 사람이 검토한 뒤 병합하세요.

### 저장소 관리자가 한 번 설정할 항목

1. GitHub 저장소의 **Settings → Actions → General**에서 Actions 사용이 허용되어 있는지 확인하세요. 제한된 Actions 정책을 쓰면 `actions/checkout`, `actions/setup-node`, `actions/github-script` 등 이 저장소의 Actions도 허용해야 합니다.
2. 같은 화면의 **Workflow permissions → Allow GitHub Actions to create and approve pull requests**를 체크하고 **Save**를 누르세요. 이 기능은 PR 생성만 사용하며 승인·병합을 자동 실행하지 않습니다.
3. 체크박스가 비활성화되어 있거나 설정 후에도 403이 나면 조직·Enterprise의 Actions 정책이 제한하는지 관리자에게 확인하세요. 이 설정은 Codex에서 대신 변경하지 않습니다.

인증은 Actions가 실행할 때 자동 제공하는 `GITHUB_TOKEN`만 사용합니다. 비밀번호·개인 토큰을 코드나 Secrets에 추가할 필요가 없습니다. PR 워크플로는 필요한 **`contents: read`, `pull-requests: write`**만 명시하고 나머지 권한은 부여하지 않습니다. checkout에도 인증 정보를 남기지 않습니다. 전역 Workflow permissions를 모든 항목 쓰기 권한으로 넓히지 않고 워크플로의 최소 권한 선언을 사용합니다.

### 적용과 확인

새 워크플로가 포함된 이 브랜치의 첫 Push부터 실행될 수 있지만, 저장소 권한 설정이 먼저 필요합니다. 이후 다른 feat/fix 브랜치도 최신 main의 워크플로를 포함해야 합니다. 아직 포함되지 않은 오래된 브랜치에는 최신 main을 반영하세요.

1. feat/fix 브랜치에 커밋·Push합니다.
2. **Actions → Auto-create pull request** 실행에서 생성된 PR URL 또는 기존 PR을 유지했다는 로그를 확인합니다.
3. **FTIR analysis and tests**의 Python 분석·정답 생성·Windows 동기화 테스트와 별도 `test-auto-pr` 작업의 Node.js 테스트를 확인합니다. `.github/scripts/` 변경도 이 검증을 실행합니다. PR 생성 작업과 코드 검증은 서로 독립적으로 실행되므로 PR 생성 성공이 테스트 통과를 뜻하지 않습니다.

자동 제공된 GITHUB_TOKEN이 생성한 PR은 보통 다른 `pull_request` 워크플로를 다시 시작하지 않습니다. 코드 검증은 기존 **Push 기반** 분석 워크플로를 사용합니다. 기존 분석 워크플로의 경로 필터(코드·데이터·테스트·의존성·워크플로 등)에 해당하는 변경에서 실행되며, README만 변경한 경우에는 분석을 자동 실행하지 않을 수 있습니다. 필요하면 **FTIR analysis and tests → Run workflow**에서 해당 브랜치를 선택해 실행하세요.

권한 부족 시 PR 생성 작업은 실패로 표시하고 설정 안내·수동 비교 링크를 로그에 남깁니다. 같은 실행 안에서 생성을 반복하지 않습니다. 설정을 수정한 뒤 실패한 작업을 한 번 재실행하거나 새 Push를 하세요. 기존 FTIR 분석과 Windows 데이터 동기화 기능은 PR 생성 실패와 무관하게 유지됩니다.

로컬 검증 명령:

```bash
python -m pytest -q
# PR 자동화 테스트에는 Node.js 24가 필요합니다. Windows 데이터 동기화 자체에는 필요 없습니다.
node --test tests/test_auto_pr.cjs
```
