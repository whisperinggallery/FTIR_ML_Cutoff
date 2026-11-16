"""Preliminary raw-spectrum-only cutoff regression; no processed-file features."""
import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import platform

import numpy as np

from .cli import diagnostics, read_spectrum, resolve_file
from .labels import spectrum_identity

BANDS = ((0, 50), (50, 100), (100, 150), (150, 200))
METRICS = ('normalized_rms', 'abs_normalized_slope', 'normalized_slope_variation')
FEATURE_NAMES = ['detector_bolometer'] + [
    f'{metric}_{stat}_{lo}_{hi}' for lo, hi in BANDS
    for metric in METRICS for stat in ('median', 'p90')
]
PRELIMINARY = 'Preliminary experiment: very small dataset; LOGO scores are not evidence of deployment accuracy.'
PREDICTION_FIELDS = ['sample_id', 'raw_group_id', 'detector', 'model', 'fold',
                     'actual_cutoff_cm1', 'predicted_cutoff_cm1', 'error_cm1',
                     'absolute_error_cm1', 'underpredicted', 'note', 'training_groups']


@dataclass
class Case:
    sample_id: str
    raw_file: str
    group: str
    detector: str
    target: float
    features: np.ndarray
    rule: float
    rule_note: str


def extract_features(x, y, detector):
    """Fixed 0–200 cm^-1 bands; never accept a target or processed spectrum."""
    if detector not in ('DTGS', 'Bolometer'):
        raise ValueError('detector must be DTGS or Bolometer')
    low = (x >= 0) & (x <= 200)
    if low.sum() < 5:
        raise ValueError('need at least 5 raw points in fixed 0–200 cm^-1 range')
    # Restrict smoothing too: high-frequency artifacts must not enter low-band fits.
    lx, ly = x[low], y[low]
    smooth, rms, slope, _ = diagnostics(lx, ly, 11, 21)
    scale = float(np.median(np.abs(ly)))
    if scale <= 1e-12:
        raise ValueError('raw low-frequency reflectance scale is zero or too small')
    local_level = np.array([np.median(np.abs(smooth[max(0, i-10):i+11])) for i in range(len(lx))])
    denominator = np.maximum(local_level, .05 * scale)
    slope_variation = np.array([np.std(slope[max(0, i-10):i+11]) for i in range(len(lx))])
    series = (rms / denominator, np.abs(slope) / denominator, slope_variation / denominator)
    features = [float(detector == 'Bolometer')]
    for lo, hi in BANDS:
        mask = (lx >= lo) & (lx < hi if hi < 200 else lx <= hi)
        for values in series:
            features.extend((float(np.median(values[mask])), float(np.percentile(values[mask], 90)))
                            if mask.any() else (np.nan, np.nan))
    # Fixed physical heuristic, chosen before evaluation; no cutoff labels used.
    stable = (series[0] <= .02) & (series[1] <= .005) & (series[2] <= .002)
    rule, note = float(lx[-1]), 'no sustained stable segment; use highest available raw point <=200'
    for start in range(len(lx)):
        stop = int(np.searchsorted(lx, lx[start] + 10, side='left'))
        if stop < len(lx) and stop - start + 1 >= 5 and stable[start:stop+1].all():
            rule, note = float(lx[start]), 'first stable segment spanning >=10 cm^-1 and >=5 raw points'
            break
    return np.asarray(features), rule, note


def load_cases(data, labels):
    """Use only selected labels; validate identity using raw numeric content."""
    excluded, grouped = [], {}
    with Path(labels).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        required = {'sample_id', 'raw_file', 'raw_group_id', 'detector', 'final_cutoff_cm1',
                    'use_for_regression', 'cutoff_confidence', 'label_status'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('training_labels.csv is missing required label columns')
        rows = list(reader)
    for row in rows:
        sample = row.get('sample_id', '')
        if row['use_for_regression'].strip().lower() != 'true':
            excluded.append(dict(sample_id=sample, reason='use_for_regression is not true'))
            continue
        try:
            if row['cutoff_confidence'] != 'axis_verified' or row['label_status'] != 'selected':
                raise ValueError('training label must be selected and axis_verified')
            path = resolve_file(Path(data) / 'raw', row['raw_file'], sample, '.dpt')
            x, y = read_spectrum(path)
            group = spectrum_identity(x, y)
            if row['raw_group_id'] != group:
                raise ValueError('raw spectrum identity differs from label; regenerate analysis labels')
            target = float(row['final_cutoff_cm1'])
            if not np.isfinite(target) or not x[0] < target <= x[-1]:
                raise ValueError('target must be finite and inside raw range above its minimum')
            values, rule, note = extract_features(x, y, row['detector'])
            case = Case(sample, path.name, group, row['detector'], target, values, rule, note)
            grouped.setdefault(group, []).append(case)
        except (ValueError, OSError, KeyError) as exc:
            excluded.append(dict(sample_id=sample, reason=str(exc)))
    cases = []
    for group, candidates in sorted(grouped.items()):
        if len({(c.target, c.detector) for c in candidates}) != 1:
            excluded.extend(dict(sample_id=c.sample_id, reason='conflicting selected targets or detectors for same raw group') for c in candidates)
            continue
        candidates.sort(key=lambda c: (c.sample_id, c.raw_file))
        cases.append(candidates[0])
        excluded.extend(dict(sample_id=c.sample_id, reason='duplicate numeric original; one case per group') for c in candidates[1:])
    return cases, excluded


def make_forest():
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import make_pipeline
    return make_pipeline(SimpleImputer(strategy='median', keep_empty_features=True),
                         RandomForestRegressor(n_estimators=300, max_depth=3, min_samples_leaf=2,
                                               random_state=0, n_jobs=1))


def evaluate(cases, include_forest=True):
    """OOF predictions only. Fit each baseline and imputer on the training fold."""
    from sklearn.model_selection import LeaveOneGroupOut
    if len(cases) < 2:
        return [], []
    X = np.array([c.features for c in cases])
    y = np.array([c.target for c in cases])
    groups = np.array([c.group for c in cases])
    if len(set(groups)) != len(cases):
        raise ValueError('expected one case per numeric original group')
    predictions = []
    for fold, (train, test) in enumerate(LeaveOneGroupOut().split(X, y, groups), 1):
        assert set(groups[train]).isdisjoint(groups[test])
        forest = make_forest() if include_forest else None
        if forest is not None:
            forest.fit(X[train], y[train])
        for index in test:
            case = cases[index]
            detector_train = [i for i in train if cases[i].detector == case.detector]
            mean = float(y[detector_train].mean()) if detector_train else float(y[train].mean())
            models = [('detector_mean', mean, 'training-fold detector mean' if detector_train else 'unseen detector; training-fold global mean'),
                      ('noise_rule', case.rule, case.rule_note)]
            if forest is not None:
                models.append(('random_forest', float(forest.predict(X[index:index+1])[0]), 'fold-trained RF; no hyperparameter search'))
            for model, predicted, note in models:
                error = predicted - case.target
                predictions.append(dict(sample_id=case.sample_id, raw_group_id=case.group, detector=case.detector,
                                        model=model, fold=fold, actual_cutoff_cm1=case.target,
                                        predicted_cutoff_cm1=predicted, error_cm1=error, absolute_error_cm1=abs(error),
                                        underpredicted=str(error < 0).lower(), note=note,
                                        training_groups=json.dumps(sorted(groups[train].tolist()))))
    metrics = []
    for model in dict.fromkeys(r['model'] for r in predictions):
        rows = [r for r in predictions if r['model'] == model]
        metrics.append(dict(model=model, cases=len(rows), mae_cm1=float(np.mean([r['absolute_error_cm1'] for r in rows])),
                            underprediction_count=sum(r['error_cm1'] < 0 for r in rows),
                            evaluation='Leave-One-Group-Out', preliminary='true'))
    return predictions, metrics


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def plots(output, predictions, cases):
    import matplotlib.pyplot as plt
    models = list(dict.fromkeys(r['model'] for r in predictions))
    fig, ax = plt.subplots(figsize=(7, 6))
    for model in models:
        rows = [r for r in predictions if r['model'] == model]
        ax.scatter([r['actual_cutoff_cm1'] for r in rows], [r['predicted_cutoff_cm1'] for r in rows], label=model)
    bounds = [r[k] for r in predictions for k in ('actual_cutoff_cm1', 'predicted_cutoff_cm1')]
    lo, hi = min(bounds), max(bounds)
    ax.plot([lo, hi], [lo, hi], 'k--', lw=1)
    ax.set(xlabel='Actual cutoff (cm$^{-1}$)', ylabel='Out-of-fold predicted cutoff (cm$^{-1}$)',
           title=f'PRELIMINARY — LOGO, n={len(cases)} original groups')
    ax.legend(); fig.tight_layout(); fig.savefig(output/'cutoff_scatter.png', dpi=160); plt.close(fig)
    fig, ax = plt.subplots(figsize=(max(10, len(cases)*.9), 6))
    positions = np.arange(len(cases))
    for i, model in enumerate(models):
        lookup = {r['raw_group_id']: r['error_cm1'] for r in predictions if r['model'] == model}
        ax.bar(positions+(i-(len(models)-1)/2)*.25, [lookup[c.group] for c in cases], width=.25, label=model)
    ax.axhline(0, color='black', lw=1)
    ax.set_xticks(positions, [c.sample_id for c in cases], rotation=45, ha='right')
    ax.set(ylabel='Predicted minus actual (cm$^{-1}$)', title='PRELIMINARY — negative error means underprediction')
    ax.legend(); fig.tight_layout(); fig.savefig(output/'sample_errors.png', dpi=160); plt.close(fig)


def run_experiment(data, labels, output, baselines_only=False):
    import sklearn
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    # Baseline-only runs should use their own directory so old fitted models cannot be mistaken for this run.
    if baselines_only and (output/'model.joblib').exists():
        raise ValueError('choose a new output directory for baseline-only evaluation')
    cases, excluded = load_cases(data, labels)
    # Remove only our generated artifacts, so empty/insufficient reruns cannot expose stale predictions.
    for name in ('model.joblib', 'cutoff_scatter.png', 'sample_errors.png'):
        (output/name).unlink(missing_ok=True)
    write_csv(output/'features.csv', ['sample_id', 'raw_group_id', 'raw_file', 'detector', *FEATURE_NAMES],
              [dict(sample_id=c.sample_id, raw_group_id=c.group, raw_file=c.raw_file, detector=c.detector,
                    **{name: ('' if np.isnan(value) else value) for name, value in zip(FEATURE_NAMES, c.features)}) for c in cases])
    write_csv(output/'excluded_labels.csv', ['sample_id', 'reason'], excluded)
    predictions, metrics = evaluate(cases, include_forest=not baselines_only)
    write_csv(output/'predictions.csv', PREDICTION_FIELDS, predictions)
    write_csv(output/'metrics.csv', ['model', 'cases', 'mae_cm1', 'underprediction_count', 'evaluation', 'preliminary'], metrics)
    feature_stats = []
    for j, name in enumerate(FEATURE_NAMES):
        values = np.array([c.features[j] for c in cases]); finite = values[np.isfinite(values)]
        feature_stats.append(dict(feature=name, available_cases=len(finite), missing_cases=len(cases)-len(finite),
                                  median=float(np.median(finite)) if len(finite) else '',
                                  min=float(finite.min()) if len(finite) else '', max=float(finite.max()) if len(finite) else ''))
    write_csv(output/'feature_summary.csv', ['feature', 'available_cases', 'missing_cases', 'median', 'min', 'max'], feature_stats)
    status = 'completed' if predictions else 'insufficient_groups'
    if predictions:
        plots(output, predictions, cases)
    if predictions and not baselines_only:
        import joblib
        forest = make_forest()
        forest.fit(np.array([c.features for c in cases]), np.array([c.target for c in cases]))
        joblib.dump(dict(estimator=forest, feature_names=FEATURE_NAMES, feature_version=1,
                         training_groups=[c.group for c in cases], sklearn_version=sklearn.__version__), output/'model.joblib')
    manifest = dict(status=status, warning=PRELIMINARY, cases=len(cases), excluded_records=len(excluded),
                    mode='baselines_only' if baselines_only else 'baselines_and_random_forest',
                    sklearn_version=sklearn.__version__, numpy_version=np.__version__, python_version=platform.python_version(),
                    feature_version=1, feature_names=FEATURE_NAMES,
                    feature_parameters=dict(bands_cm1=BANDS, smoothing_points=11, local_points=21,
                                            normalization_floor_fraction=.05),
                    training_groups=[c.group for c in cases],
                    evaluation='Leave-One-Group-Out across all eligible groups; not an independent held-out test',
                    rf_parameters=dict(n_estimators=300, max_depth=3, min_samples_leaf=2, random_state=0),
                    noise_rule=dict(normalized_rms_max=.02, abs_normalized_slope_max=.005,
                                    normalized_slope_variation_max=.002, stable_span_cm1=10, min_points=5))
    (output/'experiment.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(PRELIMINARY)
    print(f'{len(cases)} original groups; {status}')
    for row in metrics:
        print(f"{row['model']}: MAE={row['mae_cm1']:.4f} cm^-1; underpredictions={row['underprediction_count']}/{row['cases']}")
    return manifest, predictions, metrics


def predict_raw(model_path, raw, detector):
    import joblib
    bundle = joblib.load(model_path)
    if bundle.get('feature_version') != 1 or bundle.get('feature_names') != FEATURE_NAMES:
        raise ValueError('model feature schema is incompatible')
    x, y = read_spectrum(Path(raw))
    features, rule, note = extract_features(x, y, detector)
    prediction = float(bundle['estimator'].predict(features.reshape(1, -1))[0])
    return dict(raw_file=Path(raw).name, detector=detector, predicted_cutoff_cm1=prediction,
                noise_rule_cutoff_cm1=rule, noise_rule_note=note, preliminary='true',
                same_as_training_original=str(spectrum_identity(x, y) in bundle['training_groups']).lower())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    train = sub.add_parser('evaluate', help='LOGO evaluation and final preliminary model')
    train.add_argument('--data-dir', type=Path, default=Path('data'))
    train.add_argument('--labels', type=Path, default=Path('results/training_labels.csv'))
    train.add_argument('--output-dir', type=Path, default=Path('results/ml'))
    train.add_argument('--baselines-only', action='store_true')
    predict = sub.add_parser('predict', help='predict using only a raw .dpt and detector')
    predict.add_argument('--model', type=Path, default=Path('results/ml/model.joblib'))
    predict.add_argument('--raw', type=Path, required=True)
    predict.add_argument('--detector', choices=['DTGS', 'Bolometer'], required=True)
    predict.add_argument('--output', type=Path, default=Path('results/ml/new_prediction.csv'))
    args = parser.parse_args(argv)
    try:
        if args.command == 'evaluate':
            run_experiment(args.data_dir, args.labels, args.output_dir, args.baselines_only)
        else:
            row = predict_raw(args.model, args.raw, args.detector)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            write_csv(args.output, list(row), [row])
            print(PRELIMINARY)
            print(f"Predicted cutoff: {row['predicted_cutoff_cm1']:.4f} cm^-1")
        return 0
    except (OSError, ValueError, KeyError, ImportError) as exc:
        print(f'ML experiment error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
