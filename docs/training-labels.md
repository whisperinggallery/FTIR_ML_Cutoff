# ML 학습 정답 데이터셋 만들기

이 기능은 모델을 학습하지 않습니다. 기존 분석 결과를 보존하면서, **동일한 원본 스펙트럼에서 검증된 low-frequency cutoff의 최댓값**을 최종 정답으로 선택합니다. 실험자가 cutoff를 더 높은 값으로만 수정했다는 프로젝트 규칙을 적용합니다. 다른 실험 이력의 데이터에 적용하기 전에는 이 전제를 확인하세요.

## 실행과 결과 다운로드

기존 분석 명령을 실행하면 정답 파일도 자동 생성됩니다. 의존성을 추가로 설치할 필요는 없습니다.

```bash
python -m ftir_cutoff
# 같은 원본 그룹을 기준으로 나누며, 기본 test 배정 확률은 20%입니다.
python -m ftir_cutoff --test-fraction 0.2 --split-seed 0
```

GitHub에서도 data 변경으로 기존 Actions가 실행되면 **Artifacts → ftir-results** 안에 다음 파일이 포함됩니다.

- `training_labels.csv`: 기존 분석 기록별 한 행. 선택·제외 상태, 원본 그룹, 개별 cutoff와 후보값, 최종 검증 cutoff, 분할 정보.
- `training_groups.csv`: 숫자 값이 동일한 원본 그룹별 한 행. 구성 sample_id·raw 파일 목록, 최종 검증 cutoff, 선택된 시료, 제외 기록 전체.

기존 `summary.csv`, 노이즈 진단 CSV·PNG도 계속 생성합니다. 입력 `.dpt`, `.dat`, metadata.csv와 기존 cutoff 기록은 수정하거나 삭제하지 않습니다. 결과 CSV는 실행 시 최신 데이터로 다시 생성됩니다. 데이터가 없으면 헤더만 있는 두 파일을 생성합니다. 잘못된 시료는 검토 대상으로 남기고 다른 정상 시료의 정답을 계속 저장합니다.

## 동일한 원본의 정의

파일명·시료명·detector로 추측하지 않습니다. `.dpt`를 읽고 파수 오름차순으로 정렬한 **모든 파수와 반사율 숫자 쌍**을 비교하는 방식입니다. 숫자를 일정한 float64 형식으로 표현한 뒤 SHA-256을 계산하여 `raw_group_id`를 만듭니다.

- 파일명이 다르거나 줄 순서가 반대여도 숫자가 같으면 같은 그룹입니다.
- 공백·쉼표·지수 표기·주석 차이는 숫자 값이 같다면 영향을 주지 않습니다. +0과 -0은 같은 값으로 정규화합니다.
- 반사율 크기를 바꾸거나 파수 보간·반올림·노이즈 차이가 있으면 **별도 원본**입니다. 비슷해 보이는 데이터를 허용 오차로 자동 합치지 않습니다. 원본 식별에는 cutoff 축 대응 허용 오차를 적용하지 않습니다.
- detector가 다르게 등록되었더라도 원본 숫자가 완전히 같다면 같은 그룹입니다. 기록이 맞는지는 별도로 확인하세요.

metadata의 서로 다른 sample_id가 같은 raw_file을 참조하고 **서로 다른 processed_file**을 참조하는 이력도 분석할 수 있습니다. 같은 처리 파일을 여러 시료에 중복 배정하는 것은 여전히 오류입니다. 기존 Windows 자동 동기화는 원래의 파일 대응 충돌 방지 규칙을 유지하므로, 여러 이력의 명시적 raw 공유는 metadata를 사람이 관리하는 분석 용도입니다.

## 정답 선택 규칙과 상태

정답 후보 자격은 기존 분석의 `status=ok`, `cutoff_confidence=axis_verified`, 축 대응 방식 `exact_overlap` 또는 `exact_low_frequency_segment`입니다. cutoff가 유한하고 원본 파수 범위 안이어야 합니다. 현재 `axis_verified`는 축 경계 대응을 확인했다는 의미이며 물리적 노이즈 한계를 검증했다는 의미는 아닙니다.

| label_status | 의미 | use_for_regression |
| --- | --- | --- |
| `selected` | 동일 원본 그룹의 가장 높은 검증 cutoff | `true` |
| `superseded` | 같은 원본에 더 높은 검증 cutoff가 있어 제외 | `false` |
| `duplicate_final` | 최댓값이 동일한 중복 기록; 대표 행이 이미 선택됨 | `false` |
| `needs_review` | 보간·불일치 축, cutoff 없음, 분석 오류 또는 원본 미확인 | `false` |

같은 최댓값이 여러 번 등장하면 sample_id, raw_file, processed_file 순으로 대표 기록 하나를 결정하여 동일한 원본에 중복 가중치가 생기지 않게 합니다. 행 순서와 파일 업로드 시각을 정답 선택에 사용하지 않습니다.

불확실한 `cutoff_candidate_cm1` 값이나 `human_cutoff_cm1` 입력만으로 정답을 확정하지 않습니다. 사람이 입력한 cutoff 값은 보존하지만 현재 별도의 검증 승인 필드가 없으므로 자동으로 axis_verified로 승격하지 않습니다.

한 그룹에 검증값과 불확실한 후보가 함께 있으면 **검증값 중 최댓값**만 선택하고, 불확실한 기록은 계속 needs_review로 남깁니다. `group_review_required=true`가 이 미해결 기록의 존재를 알립니다. 따라서 `final_cutoff_cm1`은 현재 검증된 기록에 대한 최댓값이며, 검토 후 바뀔 수 있습니다. 그룹 내 검토가 모두 끝난 데이터만 쓰고 싶다면 이 값이 false인 그룹까지 추가로 필터링하세요. 검증값이 하나도 없으면 최종 cutoff를 빈 칸으로 남기고 split을 review로 기록합니다.

`superseded_cutoffs_cm1`은 낮은 검증 cutoff의 중복 없는 목록입니다. `excluded_records`에는 낮은 값뿐 아니라 동률 중복과 검토 대상의 sample_id, 개별 cutoff/후보값, 제외 이유를 JSON 목록으로 남깁니다. 이 JSON 목록들은 CSV의 한 셀 안에 저장되며 원본 기록을 삭제하지 않습니다.

## Train/test 누수 방지

`dataset_split`은 **raw_group_id와 split_seed만**으로 결정합니다. 파일명, 선택 cutoff, detector, 행 순서가 바뀌어도 원본 값과 seed가 같다면 분할이 유지됩니다. 같은 그룹의 selected·superseded·검토 기록은 모두 같은 split을 가집니다. 아직 검증 정답이 없는 그룹은 review입니다.

실제 학습에 사용할 때 반드시 다음 두 단계를 지키세요.

1. `use_for_regression`이 문자열 `true`인 행만 사용합니다. 최종 cutoff가 반복 기재되어 있다는 이유로 전체 행을 학습에 넣지 마세요.
2. train과 test는 `dataset_split` 열을 따릅니다. 파일명이나 개별 행을 다시 무작위 분할하지 마세요.

예시(추가 라이브러리 없이 정답 행 확인):

```python
import csv

with open('results/training_labels.csv', encoding='utf-8-sig', newline='') as f:
    selected = [r for r in csv.DictReader(f) if r['use_for_regression'] == 'true']
train = [r for r in selected if r['dataset_split'] == 'train']
test = [r for r in selected if r['dataset_split'] == 'test']
assert not ({r['raw_group_id'] for r in train} & {r['raw_group_id'] for r in test})
```

`test_fraction`은 그룹 해시의 배정 확률이며 정확한 개수 비율을 강제하지 않습니다. 작은 데이터에서는 한쪽이 비어 있을 수도 있습니다. 테스트 결과를 보고 유리한 split_seed를 고르는 방식은 피하고, 연구에서 사용할 seed를 고정하여 보관하세요. 나중에 별도의 학습 코드를 작성할 때도 이 그룹과 분할 정보를 사용해야 합니다.

## 실제 데이터 검증 결과

검증 기준 데이터: main 커밋 `c0ea07b`, 측정 14쌍. 수치는 데이터에서 계산하며 프로그램에 하드코딩하지 않습니다.

| 같은 원본으로 확인된 기록 | 검증 cutoff 목록 (cm⁻¹) | 최종 (cm⁻¹) |
| --- | --- | ---: |
| DL1_DTGS, DL1_FR_25.09, DL1_FR | 67.50203, 95.46716, 100.28874 | 100.28874 |
| DL2_FR_25.10, DL2_FR | 81.96676, 100.28874 | 100.28874 |

14개 기록 → 원본 11그룹 → selected 9개, superseded 3개, needs_review 2개입니다. 기본 seed=0에서 selected는 train 8개·test 1개입니다. DL2_FIR_Bolo와 DL4_FIR_Bolo는 보간·불일치 축이므로 정답에서 제외됩니다. DL3_FIR와 DL3_FR_25.09는 원본 숫자가 달라 별도 그룹으로 유지합니다.
