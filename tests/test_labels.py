import csv
import json
from pathlib import Path

import numpy as np
import pytest

from ftir_cutoff.cli import read_spectrum, run
from ftir_cutoff.labels import group_split, spectrum_identity, write_training_labels


def raw(data, name, offset=0, reverse=False, signed_zero=False):
    path = data / 'raw' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    x = np.arange(0., 201.)
    y = .3 + .001*x + offset
    if signed_zero:
        x[0] = -0.
    values = np.column_stack((x, y))
    np.savetxt(path, values[::-1] if reverse else values, delimiter=',' if reverse else ' ')
    return path


def result(sample, filename, cutoff, verified=True):
    return dict(sample_id=sample, raw_file=filename, processed_file=sample+'.dat', detector='DTGS',
                status='ok' if verified else 'warning', axis_alignment='exact_overlap' if verified else 'resampled_or_unmatched',
                cutoff_confidence='axis_verified' if verified else 'candidate_only',
                axis_cutoff_cm1=cutoff if verified else '', cutoff_candidate_cm1=cutoff,
                human_cutoff_cm1='199')


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def test_numeric_identity_ignores_name_order_format_signed_zero(tmp_path):
    paths = [raw(tmp_path, 'old.dpt'), raw(tmp_path, 'different.dpt', reverse=True, signed_zero=True)]
    identities = [spectrum_identity(*read_spectrum(p)) for p in paths]
    assert identities[0] == identities[1]
    assert identities[0] != spectrum_identity(*read_spectrum(raw(tmp_path, 'other.dpt', offset=1e-10)))
    x, y = read_spectrum(paths[0])
    assert identities[0] != spectrum_identity(x+.01, y)
    assert identities[0] != spectrum_identity(x, y*100)


def test_max_verified_superseded_and_uncertain_preserved(tmp_path):
    raw(tmp_path, 'one.dpt')
    raw(tmp_path, 'renamed.dpt', reverse=True)
    inputs = [result('a', 'one.dpt', 67.50), result('b', 'renamed.dpt', 95.47),
              result('c', 'one.dpt', 100.29), result('uncertain', 'one.dpt', 150, False)]
    before = {p: p.read_bytes() for p in (tmp_path/'raw').iterdir()}
    rows, groups = write_training_labels(tmp_path, inputs, tmp_path/'out')
    assert len(groups) == 1
    assert groups[0]['final_cutoff_cm1'] == 100.29
    assert groups[0]['group_review_required'] == 'true'
    assert [r['label_status'] for r in rows] == ['superseded', 'superseded', 'selected', 'needs_review']
    assert [r['sample_id'] for r in rows if r['use_for_regression'] == 'true'] == ['c']
    assert json.loads(groups[0]['superseded_cutoffs_cm1']) == [67.5, 95.47]
    assert len(json.loads(groups[0]['excluded_records'])) == 3
    assert len({r['dataset_split'] for r in rows}) == 1
    assert all(p.read_bytes() == content for p, content in before.items())
    assert len(read_csv(tmp_path/'out/training_labels.csv')) == 4


def test_equal_maxima_selected_once_and_order_independent(tmp_path):
    raw(tmp_path, 'one.dpt')
    inputs = [result('z', 'one.dpt', 100), result('a', 'one.dpt', 100)]
    rows, groups = write_training_labels(tmp_path, inputs, tmp_path/'out')
    assert rows[0]['label_status'] == 'duplicate_final'
    assert rows[1]['label_status'] == 'selected'
    _, reverse = write_training_labels(tmp_path, inputs[::-1], tmp_path/'reverse')
    assert groups == reverse


def test_same_filename_does_not_imply_identity(tmp_path):
    x = np.arange(10.)
    a = spectrum_identity(x, x)
    b = spectrum_identity(x, x+.1)
    assert a != b


def test_uncertain_and_error_groups_have_no_regression_label(tmp_path):
    raw(tmp_path, 'one.dpt')
    inputs = [result('a', 'one.dpt', 95, False), result('missing', 'missing.dpt', 100)]
    rows, groups = write_training_labels(tmp_path, inputs, tmp_path/'out')
    assert all(r['label_status'] == 'needs_review' and r['use_for_regression'] == 'false' for r in rows)
    assert groups[0]['final_cutoff_cm1'] == ''
    assert groups[0]['dataset_split'] == 'review'
    assert rows[1]['raw_group_id'] == ''


def test_split_is_by_content_and_stable():
    groups = ['raw-v1-' + str(i) for i in range(100)]
    assignments = {g: group_split(g, 'fixed') for g in groups}
    assert set(assignments.values()) == {'train', 'test'}
    assert assignments == {g: group_split(g, 'fixed') for g in reversed(groups)}
    assert assignments != {g: group_split(g, 'other') for g in groups}


def test_shared_raw_file_distinct_processed_files_are_supported(tmp_path):
    data = tmp_path/'data'
    original = raw(data, 'same.dpt')
    (data/'processed').mkdir()
    x, y = read_spectrum(original)
    for sample, boundary in [('first', 60), ('last', 100)]:
        mask = x >= boundary
        np.savetxt(data/'processed'/f'{sample}.dat', np.column_stack((x[mask], y[mask])))
    (data/'metadata.csv').write_text('sample_id,detector,raw_file,processed_file\nfirst,DTGS,same.dpt,first.dat\nlast,DTGS,same.dpt,last.dat\n')
    assert run(data, tmp_path/'out') == 0
    rows = read_csv(tmp_path/'out/training_labels.csv')
    assert [r['label_status'] for r in rows] == ['superseded', 'selected']
    assert all(float(r['final_cutoff_cm1']) == 100 for r in rows)


@pytest.mark.parametrize('fraction', [0, 1, -1, float('nan')])
def test_invalid_split_fraction(tmp_path, fraction):
    with pytest.raises(ValueError, match='test_fraction'):
        run(tmp_path/'data', tmp_path/'out', test_fraction=fraction)


def test_empty_data_creates_header_only_label_files(tmp_path):
    assert run(tmp_path/'absent', tmp_path/'out') == 0
    assert read_csv(tmp_path/'out/training_labels.csv') == []
    assert read_csv(tmp_path/'out/training_groups.csv') == []


def test_real_measurements_label_selection(tmp_path):
    data = Path(__file__).resolve().parents[1]/'data'
    if not (data/'raw/FIR_DL1.dpt').exists() or not (data/'raw/DL2_FR.dpt').exists():
        pytest.skip('Real measurement files not present')
    assert run(data, tmp_path/'out') == 0
    rows = read_csv(tmp_path/'out/training_labels.csv')
    by_sample = {r['sample_id']: r for r in rows}
    for members, winner, expected in [
        (['DL1_DTGS', 'DL1_FR_25.09', 'DL1_FR'], 'DL1_FR', 100.28874),
        (['DL2_FR_25.10', 'DL2_FR'], 'DL2_FR', 100.28874),
    ]:
        assert len({by_sample[s]['raw_group_id'] for s in members}) == 1
        assert len({by_sample[s]['dataset_split'] for s in members}) == 1
        for sample in members:
            row = by_sample[sample]
            assert float(row['final_cutoff_cm1']) == pytest.approx(expected)
            assert row['label_status'] == ('selected' if sample == winner else 'superseded')
    for sample in ['DL2_FIR_Bolo', 'DL4_FIR_Bolo']:
        assert by_sample[sample]['label_status'] == 'needs_review'
        assert by_sample[sample]['use_for_regression'] == 'false'
        assert by_sample[sample]['final_cutoff_cm1'] == ''
    selected = [r for r in rows if r['use_for_regression'] == 'true']
    assert len(selected) == len({r['raw_group_id'] for r in selected})
    assert {r['raw_group_id'] for r in rows if r['dataset_split'] == 'train'}.isdisjoint(
        {r['raw_group_id'] for r in rows if r['dataset_split'] == 'test'})
    for group in read_csv(tmp_path/'out/training_groups.csv'):
        ids = json.loads(group['sample_ids'])
        paths = [data/'raw'/by_sample[s]['raw_file'] for s in ids]
        first_x, first_y = read_spectrum(paths[0])
        for path in paths[1:]:
            x, y = read_spectrum(path)
            np.testing.assert_array_equal(first_x, x)
            np.testing.assert_array_equal(first_y, y)
