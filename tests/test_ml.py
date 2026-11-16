import csv
import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip('sklearn', reason='Install .[test,ml] to execute ML tests')
from ftir_cutoff import ml
from ftir_cutoff.cli import run
from ftir_cutoff.labels import spectrum_identity


def dataset(tmp_path, count=3):
    data = tmp_path/'data'
    (data/'raw').mkdir(parents=True)
    rows = []
    for i in range(count):
        x = np.arange(0., 251.)
        y = .2 + i*.01 + .0001*x + (.001+i*.001)*np.sin(x/(i+1))
        np.savetxt(data/'raw'/f's{i}.dpt', np.column_stack((x, y)))
        rows.append(dict(sample_id=f's{i}', raw_file=f's{i}.dpt', raw_group_id=spectrum_identity(x, y),
                         detector='Bolometer' if i == 0 else 'DTGS', final_cutoff_cm1=50+i*10,
                         use_for_regression='true', cutoff_confidence='axis_verified', label_status='selected'))
    labels = tmp_path/'training_labels.csv'
    save_labels(labels, rows)
    return data, labels, rows


def save_labels(path, rows):
    fields = ['sample_id', 'raw_file', 'raw_group_id', 'detector', 'final_cutoff_cm1',
              'use_for_regression', 'cutoff_confidence', 'label_status']
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def test_features_scale_invariant_and_no_high_frequency_input():
    x = np.arange(0., 301.)
    y = .3+.001*np.sin(x)
    a, rule, _ = ml.extract_features(x, y, 'DTGS')
    b, scaled_rule, _ = ml.extract_features(x, y*137, 'DTGS')
    np.testing.assert_allclose(a, b, atol=1e-9)
    assert rule == scaled_rule
    y[x>200] = 1e6
    np.testing.assert_allclose(a, ml.extract_features(x, y, 'DTGS')[0])
    assert len(a) == len(ml.FEATURE_NAMES) == 25
    assert not any('cutoff' in name or 'processed' in name for name in ml.FEATURE_NAMES)
    assert ml.extract_features(x, y, 'Bolometer')[0][0] == 1
    assert a[0] == 0


@pytest.mark.parametrize('level', [.15, 8., -.4])
def test_rule_does_not_require_reflectance_one(level):
    x = np.arange(0., 201.)
    f, rule, note = ml.extract_features(x, np.full(len(x), level), 'DTGS')
    assert rule == 0
    assert np.max(f[1:]) < 1e-10


def test_unstable_rule_fallback_is_explicit():
    x = np.arange(0., 201.)
    _, cutoff, note = ml.extract_features(x, .01+np.cos(x), 'DTGS')
    assert cutoff == 200
    assert 'no sustained stable segment' in note


def test_only_selected_labels_deduplicated_and_no_dat_required(tmp_path):
    data, labels, rows = dataset(tmp_path)
    rows.append(dict(rows[0], sample_id='duplicate'))
    rows.append(dict(rows[1], sample_id='superseded', use_for_regression='false', final_cutoff_cm1='not-a-target'))
    save_labels(labels, rows)
    cases, excluded = ml.load_cases(data, labels)
    assert len(cases) == 3
    assert len({c.group for c in cases}) == 3
    assert len(excluded) == 2
    assert not (data/'processed').exists()
    old_features = {c.group: c.features.copy() for c in cases}
    rows[0]['final_cutoff_cm1'] = 99
    rows[-2]['final_cutoff_cm1'] = 99  # duplicated row uses the same revised label
    save_labels(labels, rows)
    revised, _ = ml.load_cases(data, labels)
    for case in revised:
        np.testing.assert_array_equal(case.features, old_features[case.group])


def test_conflicting_labels_and_stale_raw_identity_excluded(tmp_path):
    data, labels, rows = dataset(tmp_path)
    rows.append(dict(rows[0], sample_id='conflict', final_cutoff_cm1=77))
    rows[1]['raw_group_id'] = 'fake-group'
    save_labels(labels, rows)
    cases, excluded = ml.load_cases(data, labels)
    assert len(cases) == 1
    assert len(excluded) == 3


def test_logo_no_target_leakage_and_baseline_unseen_detector(tmp_path):
    data, labels, _ = dataset(tmp_path)
    cases, _ = ml.load_cases(data, labels)
    original, metrics = ml.evaluate(cases)
    held_out = next(c for c in cases if c.detector == 'Bolometer')
    actual_training = [c.target for c in cases if c.group != held_out.group]
    baseline = next(r for r in original if r['model'] == 'detector_mean' and r['raw_group_id'] == held_out.group)
    assert baseline['predicted_cutoff_cm1'] == np.mean(actual_training)
    assert 'unseen detector' in baseline['note']
    for row in original:
        assert row['raw_group_id'] not in json.loads(row['training_groups'])
        assert len(json.loads(row['training_groups'])) == 2
    held_out.target += 100
    revised, _ = ml.evaluate(cases)
    for model in ['detector_mean', 'noise_rule', 'random_forest']:
        before = next(r for r in original if r['model'] == model and r['raw_group_id'] == held_out.group)
        after = next(r for r in revised if r['model'] == model and r['raw_group_id'] == held_out.group)
        assert before['predicted_cutoff_cm1'] == after['predicted_cutoff_cm1']
    for metric in metrics:
        rows = [r for r in original if r['model'] == metric['model']]
        assert metric['mae_cm1'] == pytest.approx(np.mean([abs(r['predicted_cutoff_cm1']-r['actual_cutoff_cm1']) for r in rows]))
        assert metric['underprediction_count'] == sum(r['predicted_cutoff_cm1'] < r['actual_cutoff_cm1'] for r in rows)


def test_missing_value_imputer_fitted_only_inside_fold(tmp_path, monkeypatch):
    data, labels, _ = dataset(tmp_path)
    cases, _ = ml.load_cases(data, labels)
    for i, case in enumerate(cases):
        case.features[1] = i*10.
    cases[0].features[2] = np.nan
    captured = []
    original = ml.make_forest
    def make():
        pipeline = original()
        fit = pipeline.fit
        def fit_capture(X, y):
            fit(X, y)
            captured.append((X.copy(), pipeline.steps[0][1].statistics_.copy()))
            return pipeline
        pipeline.fit = fit_capture
        return pipeline
    monkeypatch.setattr(ml, 'make_forest', make)
    ml.evaluate(cases)
    assert len(captured) == 3
    for X, statistics in captured:
        assert len(X) == 2
        assert statistics[1] == np.median(X[:, 1])


def test_artifacts_predict_only_raw_and_detector(tmp_path):
    data, labels, _ = dataset(tmp_path)
    output = tmp_path/'out'
    manifest, predictions, metrics = ml.run_experiment(data, labels, output)
    assert manifest['cases'] == 3 and manifest['status'] == 'completed'
    assert len(predictions) == 9 and len(metrics) == 3
    for name in ['cutoff_scatter.png', 'sample_errors.png']:
        assert (output/name).read_bytes().startswith(b'\x89PNG')
    prediction = ml.predict_raw(output/'model.joblib', data/'raw/s0.dpt', 'Bolometer')
    assert np.isfinite(prediction['predicted_cutoff_cm1'])
    assert prediction['same_as_training_original'] == 'true'
    assert ml.main(['predict', '--model', str(output/'model.joblib'), '--raw', str(data/'raw/s0.dpt'),
                    '--detector', 'Bolometer', '--output', str(output/'prediction.csv')]) == 0
    with (output/'features.csv').open(encoding='utf-8-sig') as stream:
        fields = next(csv.reader(stream))
    assert 'final_cutoff_cm1' not in fields and 'processed_file' not in fields


def test_empty_insufficient_and_stale_model_cleanup(tmp_path):
    data, labels, rows = dataset(tmp_path, count=1)
    output = tmp_path/'out'; output.mkdir()
    (output/'model.joblib').write_bytes(b'old')
    (output/'cutoff_scatter.png').write_bytes(b'old')
    manifest, predictions, _ = ml.run_experiment(data, labels, output)
    assert manifest['status'] == 'insufficient_groups'
    assert predictions == []
    assert not (output/'model.joblib').exists()
    assert not (output/'cutoff_scatter.png').exists()
    save_labels(labels, [])
    manifest, _, _ = ml.run_experiment(data, labels, output)
    assert manifest['cases'] == 0


def test_baselines_only_does_not_fit_forest(tmp_path, monkeypatch):
    data, labels, _ = dataset(tmp_path)
    monkeypatch.setattr(ml, 'make_forest', lambda: pytest.fail('baseline stage must not fit a forest'))
    _, predictions, metrics = ml.run_experiment(data, labels, tmp_path/'out', baselines_only=True)
    assert {r['model'] for r in metrics} == {'detector_mean', 'noise_rule'}
    assert not (tmp_path/'out/model.joblib').exists()


def test_real_data_baselines_and_forest_match_prior_stage(tmp_path):
    data = Path(__file__).resolve().parents[1]/'data'
    if not (data/'raw/FIR_DL1.dpt').exists():
        pytest.skip('Real measurement files not included')
    assert run(data, tmp_path/'analysis') == 0
    labels = tmp_path/'analysis/training_labels.csv'
    baseline, bp, bm = ml.run_experiment(data, labels, tmp_path/'baseline', True)
    full, fp, fm = ml.run_experiment(data, labels, tmp_path/'full')
    cases, _ = ml.load_cases(data, labels)
    assert baseline['cases'] == full['cases'] == len(cases)
    assert len(fp) == 3 * len(cases)
    assert bp == [r for r in fp if r['model'] != 'random_forest']
    assert bm == [r for r in fm if r['model'] != 'random_forest']
    assert len({r['raw_group_id'] for r in fp}) == len(cases)
