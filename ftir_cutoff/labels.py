"""Build auditable regression labels without training an ML model."""
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

LABEL_FIELDS = [
    'sample_id', 'detector', 'raw_file', 'processed_file', 'raw_group_id',
    'analysis_status', 'axis_alignment', 'cutoff_confidence', 'axis_cutoff_cm1',
    'cutoff_candidate_cm1', 'human_cutoff_cm1', 'label_status', 'use_for_regression',
    'dataset_split', 'final_cutoff_cm1', 'selected_sample_id',
    'superseded_cutoffs_cm1', 'excluded_records', 'group_review_required',
    'label_message', 'split_seed', 'test_fraction',
]
GROUP_FIELDS = ['raw_group_id', 'sample_ids', 'raw_files', 'final_cutoff_cm1',
                'selected_sample_id', 'dataset_split', 'superseded_cutoffs_cm1',
                'excluded_records', 'group_review_required', 'split_seed', 'test_fraction']


def spectrum_identity(x, y):
    """Hash sorted numeric pairs, not names, text formatting, or reflectance units.

    Inputs from read_spectrum are finite and sorted. Equality is exact float64
    equality (except +0/-0); noisy, rescaled and interpolated spectra stay distinct.
    """
    values = np.column_stack((x, y)).astype('<f8')
    values[values == 0] = 0  # Normalize signed zero; do not round physical values.
    return 'raw-v1-' + hashlib.sha256(values.tobytes(order='C')).hexdigest()


def group_split(group_id, seed='0', test_fraction=.2):
    """Stable group assignment independent of filename, cutoff, and row order."""
    payload = json.dumps([str(seed), group_id], separators=(',', ':')).encode('utf-8')
    bucket = int.from_bytes(hashlib.sha256(payload).digest(), 'big')
    return 'test' if bucket < test_fraction * (1 << 256) else 'train'


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def write_training_labels(data, results, output, test_fraction=.2, split_seed='0'):
    """Keep every analysis record and select exactly one verified maximum per group."""
    from .cli import read_spectrum, resolve_file

    if not np.isfinite(test_fraction) or not 0 < test_fraction < 1:
        raise ValueError('test_fraction must be finite and between 0 and 1 (exclusive)')
    records, grouped, cache = [], defaultdict(list), {}
    for result in results:
        row = {k: result.get(k, '') for k in LABEL_FIELDS}
        row.update(analysis_status=result.get('status', ''), raw_group_id='',
                   label_status='needs_review', use_for_regression='false', dataset_split='review',
                   final_cutoff_cm1='', selected_sample_id='', superseded_cutoffs_cm1='[]',
                   excluded_records='[]', group_review_required='true', label_message='',
                   split_seed=str(split_seed), test_fraction=test_fraction)
        records.append(row)
        try:
            if not result.get('sample_id') or result.get('status') == 'unassigned':
                raise ValueError('No unambiguous metadata record; cannot create a training label')
            path = resolve_file(Path(data) / 'raw', result.get('raw_file', ''), result['sample_id'], '.dpt')
            if path not in cache:
                x, y = read_spectrum(path)
                cache[path] = (spectrum_identity(x, y), float(x[0]), float(x[-1]))
            group_id, lower, upper = cache[path]
            row['raw_group_id'] = group_id
            grouped[group_id].append(row)
            value = result.get('axis_cutoff_cm1', '')
            verified = (result.get('status') == 'ok' and
                        result.get('cutoff_confidence') == 'axis_verified' and
                        result.get('axis_alignment') in ('exact_overlap', 'exact_low_frequency_segment'))
            if verified and value != '' and np.isfinite(float(value)) and lower < float(value) <= upper:
                row['axis_cutoff_cm1'] = float(value)
                row['label_status'] = 'verified'
            else:
                row['label_message'] = result.get('message') or 'No axis-verified low-frequency cutoff; manual review required'
        except (ValueError, OSError, KeyError) as exc:
            row['label_message'] = str(exc)

    groups = []
    for group_id, rows in sorted(grouped.items()):
        eligible = [r for r in rows if r['label_status'] == 'verified']
        final = max((r['axis_cutoff_cm1'] for r in eligible), default=None)
        winners = [r for r in eligible if r['axis_cutoff_cm1'] == final]
        # Equal maxima describe the same input/target; one deterministic record avoids weighting it twice.
        winner = min(winners, key=lambda r: (r['sample_id'], r['raw_file'], r['processed_file'])) if winners else None
        split = group_split(group_id, split_seed, test_fraction) if winner else 'review'
        needs_review = any(r['label_status'] == 'needs_review' for r in rows)
        for row in eligible:
            if row is winner:
                row.update(label_status='selected', use_for_regression='true',
                           label_message='Highest axis-verified cutoff for this numeric original spectrum')
            elif row['axis_cutoff_cm1'] < final:
                row.update(label_status='superseded', label_message='A higher axis-verified cutoff exists for the same original')
            else:
                row.update(label_status='duplicate_final', label_message='Equal maximum already represented by selected_sample_id')
        superseded = sorted({r['axis_cutoff_cm1'] for r in rows if r['label_status'] == 'superseded'})
        excluded = [dict(sample_id=r['sample_id'], cutoff_cm1=r['axis_cutoff_cm1'],
                         candidate_cm1=r['cutoff_candidate_cm1'], reason=r['label_status'])
                    for r in sorted(rows, key=lambda r: (r['sample_id'], r['processed_file']))
                    if r is not winner]
        group = dict(raw_group_id=group_id, sample_ids=_json(sorted({r['sample_id'] for r in rows})),
                     raw_files=_json(sorted({r['raw_file'] for r in rows})),
                     final_cutoff_cm1='' if final is None else final,
                     selected_sample_id=winner['sample_id'] if winner else '', dataset_split=split,
                     superseded_cutoffs_cm1=_json(superseded), excluded_records=_json(excluded),
                     group_review_required=str(needs_review).lower(), split_seed=str(split_seed), test_fraction=test_fraction)
        groups.append(group)
        for row in rows:
            for key in ('final_cutoff_cm1', 'selected_sample_id', 'dataset_split', 'superseded_cutoffs_cm1',
                        'excluded_records', 'group_review_required'):
                row[key] = group[key]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for name, fields, rows in [('training_labels.csv', LABEL_FIELDS, records),
                               ('training_groups.csv', GROUP_FIELDS, groups)]:
        with (output / name).open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    print(f'Training labels: {len(groups)} numeric original groups; '
          f'{sum(r["use_for_regression"] == "true" for r in records)} selected; no model trained')
    return records, groups
