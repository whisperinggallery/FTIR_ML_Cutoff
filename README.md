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

유사한 이름으로 파일을 추측하지 않습니다. 동일 파일을 여러 시료에 연결하거나 sample_id가 중복되면 해당 시료는 분석하지 않습니다. metadata에 연결되지 않은 파일도 `unassigned`로 보고합니다. 원본과 처리 데이터의 y값이 달라도 정상입니다.

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

창 크기는 홀수, 최소 5이며 **파수 폭이 아닌 점 개수**입니다. 파수 간격에 따라 실제 smoothing 폭이 달라집니다. 종료 코드는 0(정상 또는 빈 데이터), 1(시료 오류/미대응 파일), 2(설정·metadata 구조 오류)입니다. 한 시료의 오류가 다른 시료 분석을 막지는 않습니다.

## 3. cutoff와 노이즈 지표의 의미

- 처리된 각 파수를 가장 가까운 원본 파수와 비교합니다. 기본 절대 허용 오차는 0.01 cm⁻¹입니다. 모든 대응점이 원본의 중복 없는 연속 구간이어야 합니다. 재격자화, 누락된 중간 점, 원본 범위 밖의 데이터는 자동 해석하지 않습니다.
- 처리된 구간의 첫 점 앞에 원본 점이 존재하면 그 첫 파수를 `axis_cutoff_cm1`로 기록합니다. 즉, **남아 있는 가장 낮은 파수**이며 실제 절단 경계는 그 점과 직전 원본 점 사이입니다. 물리적인 신호 품질 임계값을 추정하는 알고리즘은 아닙니다.
- 원본의 최저 파수까지 남아 있으면 `no_low_frequency_trim` 상태이며 cutoff는 빈 칸입니다. 처리 데이터의 최저 파수를 임의의 cutoff로 쓰지 않습니다.
- smoothing: 원본 파수 좌표에서 이동 창의 2차 다항식 최소제곱 적합. 불균일한 파수 간격도 지원합니다. 점이 부족하면 smoothing 창을 가장 큰 가능한 홀수로 줄입니다. 끝에서는 창을 안쪽으로 이동합니다.
- residual = 원본 − smoothing. Local RMS = 주변 창 residual의 제곱 평균의 제곱근입니다. Local slope = 주변 창의 smoothing 값에 대한 파수 기준 선형 회귀 기울기입니다. local 창은 양 끝에서 짧아집니다.
- RMS는 잔차 지표입니다. 실제 흡수 피크와 smoothing 설정의 영향도 받으므로 순수한 검출기 노이즈나 측정 불확도로 해석하지 마세요. 자동 품질 cutoff 결정은 수행하지 않습니다.

## 4. 결과 확인

- `results/summary.csv`: 시료별 detector, 상태, 오류 이유, 축 범위, cutoff, 사람 선택값, 대응 오차, 점 개수와 창 설정.
- `results/run-*/시료명_diagnostics.csv`: 각 파수의 원본, smoothing, residual, RMS, slope.
- `results/run-*/시료명.png`: 원본·smoothing, RMS, slope의 세 패널. 빨간 점선은 축 cutoff, 초록 점선은 사람 선택값입니다. 처리 반사율은 별도 오른쪽 y축의 회색 선으로 표시합니다.

매 실행마다 별도 run 폴더를 만들어 이전 PNG와 구별합니다. summary.csv는 최신 실행으로 덮어씁니다. 오래된 run 폴더는 필요에 따라 삭제하세요. `results/`는 Git에 커밋하지 않습니다.

## 5. GitHub에서 자동 실행하기

1. GitHub 저장소에서 **Add file → Upload files**로 `data/raw/`, `data/processed/`에 파일을 올리고 metadata.csv를 수정하여 커밋합니다. 웹 업로드에서 폴더 위치를 확인하세요.
2. **Actions → FTIR analysis and tests**에서 실행 결과를 확인합니다. 데이터·코드·테스트·의존성 설정 변경 시 자동 실행되며, **Run workflow**로 수동 실행할 수도 있습니다.
3. 완료된 실행 화면의 **Artifacts → ftir-results**를 내려받아 압축을 풉니다. GitHub 로그인이 필요할 수 있습니다.

자동화는 Python 3.12에서 의존성 설치, 합성 데이터 테스트, 업로드 데이터 분석을 실행합니다. 잘못된 파일 대응이 있으면 실행을 실패로 표시하고, 생성된 summary와 다른 정상 시료 결과는 실패 시에도 업로드합니다. 실제 데이터가 없을 때는 테스트가 분석 기능을 검증하고 실제 분석은 빈 결과를 생성합니다. GitHub Actions에서의 실제 실행 여부는 커밋·업로드 후 확인해야 합니다.

의존성은 호환 버전 범위를 지정하며 완전한 버전 잠금은 하지 않았습니다. 장기 연구 재현성을 위해 사용한 입력 파일, 실행 옵션, 환경 버전을 함께 보관하세요.
