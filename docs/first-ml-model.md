# 첫 번째 cutoff 예측 모델: 예비 실험

**현재 유효한 정답은 원본 스펙트럼 9개뿐입니다.** 이 결과는 구현과 데이터 흐름을 점검하는 예비 실험이며 실제 측정에서의 예측 성능을 입증하지 않습니다. 실험자가 최종 cutoff를 확인해야 하며, 특히 실제보다 낮게 예측한 경우를 함께 점검해야 합니다.

## 1. 입력과 실행 순서

학습에는 `.dpt` 원본 반사율과 detector를 입력으로 사용하고, 기존 정답 생성기가 만든 `training_labels.csv`의 **use_for_regression=true**, **selected**, **axis_verified** 행만 정답으로 사용합니다. 기존 FTIR 분석·최대 cutoff 정답 선택·Windows 자동 동기화 코드는 유지합니다. 정답 CSV를 만드는 단계와 ML 모델을 평가하는 단계는 별도 명령입니다.

ML을 실행할 컴퓨터에서 기존 가상환경을 활성화한 후:

```bash
python -m pip install -e ".[test,ml]"
# 기존 분석 및 정답 생성
python -m ftir_cutoff
# 먼저 feature 분석과 두 baseline만 평가
python -m ftir_cutoff.ml evaluate --baselines-only --output-dir results/ml_baselines
# 같은 feature와 같은 LOGO 분할로 RF와 baseline을 비교
python -m ftir_cutoff.ml evaluate --output-dir results/ml
# 모든 Python 테스트
python -m pytest -q
```

Windows 자동 동기화만 사용하는 경우에는 ML 패키지를 추가 설치할 필요가 없습니다. GitHub Actions에서는 `[test,ml]` 설치 후 기존 분석, baseline 평가, Random Forest 비교 순서로 실행하며, 결과는 모두 **Artifacts → ftir-results**에서 다운로드할 수 있습니다. 개별 시료의 분석 오류가 있어도 이전과 같이 다른 시료의 결과는 보존합니다. 정답 CSV가 없다면 먼저 분석 단계 오류를 해결하세요.

정답이 0개나 1개인 경우 교차검증할 수 없으므로 `experiment.json`에 insufficient_groups를 기록하고 헤더 CSV를 생성하며 정상 종료합니다. 해당 실행에서 모델·평가 PNG를 만들지 않으며 같은 출력 폴더의 예전 모델과 PNG는 제거하여 최신 결과와 혼동하지 않게 합니다.

## 2. feature는 무엇을 나타내나요?

고정된 **0–50, 50–100, 100–150, 150–200 cm⁻¹** 네 구간을 사용합니다. 이 경계는 개별 정답 cutoff나 처리 파일 범위를 보고 결정하지 않습니다. 원본의 0–200 cm⁻¹ 데이터만으로 기존과 같은 11점 2차 다항식 smoothing과 21점 local RMS·slope를 계산합니다. 따라서 고주파의 이상값도 저주파 feature 적합에 들어가지 않습니다. 점 개수에 따른 물리적 smoothing 폭은 원본 분해능에 따라 달라집니다.

각 파수에서 다음 지표를 계산합니다.

| 지표 | 정의와 의미 |
| --- | --- |
| normalized local RMS | 원본−smoothing 잔차의 local RMS를 local 반사율 크기로 나눔. 상대적인 흔들림 크기 |
| absolute normalized slope | smoothing의 파수 기준 local 선형 기울기 절댓값을 local 반사율 크기로 나눔. 상대적인 변화율(cm 단위에 대응) |
| normalized slope variation | 주변 21점 slope의 표준편차를 local 반사율 크기로 나눔. 기울기가 얼마나 불규칙하게 변하는지 나타냄 |

분모는 주변 21점 **smoothing 절댓값의 중앙값**입니다. 0 근처에서 나눗셈이 폭증하지 않도록 원본 저주파 반사율 절댓값 중앙값의 5%를 하한으로 둡니다. 전체 저주파 크기가 1e-12 이하이면 정규화가 유효하지 않아 해당 기록을 제외하고 이유를 남깁니다. 음의 반사율도 허용하지만 실제 측정상 타당한지는 별도로 확인해야 합니다.

각 지표에 대해 네 구간별 **중앙값과 90백분위수**를 추출하므로 4×3×2=24개 수치 feature가 됩니다. detector를 DTGS=0, Bolometer=1로 표현한 feature 한 개를 더하여 **총 25개**입니다. 구간에 측정점이 없으면 결측값으로 두고, Random Forest 학습 fold 안에서만 중앙값을 추정하여 대체합니다. 해당 학습 fold 전체에 값이 없는 열은 0으로 채웁니다.

반사율이 절대값 1로 수렴한다고 가정하거나 1과의 차이를 feature로 쓰지 않습니다. 양의 배율로 원본 반사율을 재스케일해도 정규화 지표는 같으며 이를 테스트합니다. 상수 offset을 더하는 것까지 불변인 지표는 아닙니다. 피크·드리프트·smoothing 선택도 잔차와 기울기에 영향을 주므로 순수 검출기 노이즈라고 단정하지 않습니다.

`feature_summary.csv`에서 feature별 유효값 수·결측 수·중앙값·최솟값·최댓값을 먼저 확인하세요. 현재 실제 데이터에는 25개 feature 모두 결측이 없습니다. normalized RMS의 구간별 90백분위수 중앙값은 약 0.236, 0.00776, 0.00118, 0.000932로, 가장 낮은 파수 구간에서 잔차가 상대적으로 큽니다. 이 관찰만으로 정답 cutoff를 규정하거나 feature를 사후 선택하지 않았습니다.

## 3. 세 모델의 역할

### Detector 평균 baseline

같은 detector의 **학습 fold 정답 평균**을 예측합니다. spectrum feature를 사용하지 않는 단순한 비교 기준입니다. 학습 fold에 해당 detector가 없으면 학습 fold 전체 정답 평균으로 대체하고 predictions.csv에 기록합니다. 평가 대상 시료의 정답은 평균 계산에 들어가지 않습니다.

### 고정 노이즈 규칙

아래 세 조건이 모두 유지되는 최초 저주파 구간의 시작점을 예측합니다.

- normalized RMS ≤ 0.02
- absolute normalized slope ≤ 0.005 (cm⁻¹ 변화당 상대 변화율)
- normalized slope variation ≤ 0.002 (같은 기울기 단위)

안정 구간은 최소 10 cm⁻¹ 폭과 최소 5점을 동시에 만족해야 합니다. 조건을 만족하는 구간이 없으면 0–200 cm⁻¹에서 관측된 가장 높은 파수를 반환하고 fallback이라고 기록합니다. 현재 cutoff 정답을 보고 임계값을 탐색하지 않았으며 detector별로 별도 튜닝하지 않습니다. 이 규칙은 신호 안정성 기준과 실제 실험자의 cutoff 선택이 얼마나 다른지 확인하기 위한 비교 모델입니다.

### Random Forest Regression

25개 raw-only feature로 cutoff를 회귀합니다. 300개 트리, 최대 깊이 3, leaf 최소 2개, random_state=0, 단일 CPU로 고정합니다. 9개 표본에서 과도한 복잡성과 튜닝을 피하려는 초기 설정이며 최적 설정이라는 뜻은 아닙니다. 하이퍼파라미터 탐색과 결과를 보고 feature를 선택하는 과정은 수행하지 않습니다.

## 4. 원본 그룹 단위 평가와 데이터 누출 방지

- 원본 파수·반사율의 숫자 해시를 다시 계산하여 label의 raw_group_id와 일치하는지 확인합니다. 원본이 바뀌었으면 제외하고 정답 재생성을 요청합니다.
- 동일 원본의 selected 행이 중복되면 정답·detector가 같은 한 사례만 유지합니다. 정답이나 detector가 충돌하면 그 그룹은 자동 학습하지 않고 제외 이유를 기록합니다.
- **Leave-One-Group-Out(LOGO):** 원본 그룹 하나를 평가에 남겨두고 나머지 그룹만으로 모델을 적합합니다. 9그룹이면 9회 평가하며 각 원본에 out-of-fold 예측 하나씩을 만듭니다.
- 결측 대체와 detector 평균은 매 fold의 학습 데이터에서만 계산합니다. Random Forest도 fold마다 새로 적합합니다.
- ML 모듈은 처리된 `.dat`를 읽지 않습니다. cutoff, human cutoff, 처리 파일의 최저 파수·길이·보정값, sample_id, 파일명, raw_group_id는 estimator의 feature가 아닙니다. ID는 추적과 그룹 분할에만 사용합니다. 실제 cutoff는 오직 학습 target과 평가 오차 계산에 사용합니다.
- 기존 정답 CSV의 고정 train/test 배정 대신 이번 **LOGO 예비 실험은 유효한 9그룹 전체를 순환 평가**합니다. 별도의 독립적 최종 holdout 성능으로 주장하지 않습니다. 이후 데이터가 늘면 개발에 사용하지 않은 측정 그룹의 별도 최종 평가가 필요합니다.

`predictions.csv`에 각 fold의 학습 그룹 목록을 기록하므로 평가 원본이 학습 목록에 없는지 확인할 수 있습니다. 테스트에서는 평가 시료의 target만 바꾸어도 그 fold의 예측이 바뀌지 않는지, 결측 대체가 fold 내부에서만 적합되는지, 처리 파일 없이도 feature와 예측을 계산하는지 검증합니다.

## 5. 출력과 지표

`results/ml_baselines/`에는 먼저 평가한 baseline 결과가, `results/ml/`에는 세 모델 비교 결과가 저장됩니다.

| 파일 | 내용 |
| --- | --- |
| `features.csv` | 시료별 raw-only 25개 feature와 추적 ID. cutoff 정답은 포함하지 않음 |
| `feature_summary.csv` | feature의 값 분포와 결측 수 |
| `excluded_labels.csv` | superseded·검토 대상·원본 변경·충돌 등 제외 이유 |
| `predictions.csv` | 평가 fold, 실제값, out-of-fold 예측, 오차, 과소예측 여부, 학습 그룹 |
| `metrics.csv` | 모델별 MAE와 과소예측 횟수, 표본 수 |
| `cutoff_scatter.png` | 실제값 대비 out-of-fold 예측. 점선은 일치선 |
| `sample_errors.png` | 시료별 예측−실제 오차. 음수는 실제보다 낮게 예측함 |
| `experiment.json` | 예비 실험 표시, feature·규칙·모델 설정, 원본 그룹, 실행 버전 |
| `model.joblib` | LOGO가 끝난 뒤 9개 전체로 다시 적합한 예비 RF 모델. baseline-only 실행에서는 생성 안 함 |

MAE는 `mean(abs(예측−실제))`이며 단위는 cm⁻¹입니다. 과소예측 횟수는 `예측 < 실제`인 평가 사례 수입니다. 이는 통계적 신뢰구간이나 cutoff 측정 오차막대를 제공하지 않습니다. 최종 model.joblib의 학습 데이터 적합도를 평가 점수로 보고하지 않으며, metrics.csv에는 LOGO out-of-fold 결과만 들어갑니다.

현재 9개 원본에 대한 결과(데이터 main `7740120`, scikit-learn 1.9.1):

| 모델 | LOGO MAE (cm⁻¹) | 실제보다 낮게 예측 |
| --- | ---: | ---: |
| Detector 평균 | 9.1788 | 4/9 |
| 고정 노이즈 규칙 | 32.5724 | 8/9 |
| Random Forest | 7.2900 | 4/9 |

이 작은 표본에서 RF의 MAE가 낮았지만 우월한 일반화 성능을 입증한 것은 아닙니다. 특히 유효한 Bolometer는 2개뿐이며, RF도 과소예측 4건이 있습니다. 현재 결과만으로 자동 절단·삭제를 수행하지 않습니다. 패키지 버전에 따라 결과가 달라질 수 있으므로 experiment.json과 입력·정답·결과를 함께 보관하세요.

## 6. 새 원본에서 예측하기

전체 데이터로 적합한 예비 model.joblib을 만든 뒤 원본 파일과 detector만 지정합니다. `.dat`나 정답 CSV는 추론에 필요하지 않습니다.

```bash
python -m ftir_cutoff.ml predict --model results/ml/model.joblib --raw "새원본.dpt" --detector DTGS --output results/ml/new_prediction.csv
```

Bolometer라면 `--detector Bolometer`로 바꾸세요. 예측 CSV에 RF cutoff와 고정 규칙 cutoff, 예비 모델 표시, 원본이 모델의 학습 원본과 동일한지 나타내는 same_as_training_original을 기록합니다. 이 값이 true이면 새로운 독립 시료에서의 성능 확인으로 해석하면 안 됩니다. feature를 만들려면 원본의 0–200 cm⁻¹ 구간에 최소 5점이 필요합니다. 이 범위와 다른 실험·분해능·detector 장치의 데이터에 대한 정확도는 검증하지 않았습니다.

저장 모델은 자신이 생성했거나 출처를 신뢰하는 joblib 파일만 로드하고, 생성 시의 패키지 버전을 맞추세요. CLI 예측은 측정 데이터·metadata·사람 선택 cutoff를 수정하지 않습니다.
