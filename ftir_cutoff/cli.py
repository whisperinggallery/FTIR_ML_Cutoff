"""Conservative axis matching and local diagnostics for two-column spectra."""
import argparse
import csv
from pathlib import Path
import re
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

FIELDS = ['sample_id', 'detector', 'status', 'message', 'raw_file', 'processed_file',
          'axis_cutoff_cm1', 'human_cutoff_cm1', 'raw_min_cm1', 'raw_max_cm1',
          'processed_min_cm1', 'processed_max_cm1', 'axis_match_max_error_cm1',
          'raw_points', 'processed_points', 'smoothing_window', 'local_window']


def read_spectrum(path):
    """Read exactly two numeric columns; headers must start with # or ;."""
    rows = []
    for line_no, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith(('#', ';')):
            continue
        parts = re.split(r'[\s,]+', line)
        if len(parts) != 2:
            raise ValueError(f'{path.name}:{line_no}: expected two columns')
        try:
            rows.append([float(p.replace('D', 'E').replace('d', 'e')) for p in parts])
        except ValueError as exc:
            raise ValueError(f'{path.name}:{line_no}: nonnumeric data; prefix headers with #') from exc
    a = np.asarray(rows, dtype=float)
    if len(a) < 5 or not np.isfinite(a).all():
        raise ValueError(f'{path.name}: need at least 5 finite points')
    a = a[np.argsort(a[:, 0])]
    if np.any(np.diff(a[:, 0]) <= 0):
        raise ValueError(f'{path.name}: duplicate wavenumbers')
    return a[:, 0], a[:, 1]


def axis_cutoff(raw_x, processed_x, tolerance):
    """Require processed coordinates to be a contiguous subset of raw coordinates."""
    idx = np.searchsorted(raw_x, processed_x)
    right = np.clip(idx, 0, len(raw_x) - 1)
    left = np.clip(idx - 1, 0, len(raw_x) - 1)
    nearest = np.where(abs(raw_x[left] - processed_x) <= abs(raw_x[right] - processed_x), left, right)
    errors = abs(raw_x[nearest] - processed_x)
    if np.any(errors > tolerance) or np.any(np.diff(nearest) != 1):
        raise ValueError('processed axis is not a contiguous raw-axis subset within tolerance; no cutoff inferred')
    if nearest[0] == 0:
        return None, float(errors.max())
    return float(processed_x[0]), float(errors.max())


def diagnostics(x, y, smoothing_window=11, local_window=21):
    for name, window in [('smoothing', smoothing_window), ('local', local_window)]:
        if window < 5 or window % 2 == 0:
            raise ValueError(f'{name} window must be odd and >= 5')
    effective = min(smoothing_window, len(x) if len(x) % 2 else len(x) - 1)
    # Local polynomial fits use physical x coordinates, including nonuniform grids.
    smooth = np.empty_like(y)
    for i in range(len(x)):
        start = min(max(0, i - effective // 2), len(x) - effective)
        sl = slice(start, start + effective)
        coef = np.polynomial.polynomial.polyfit(x[sl] - x[i], y[sl], 2)
        smooth[i] = coef[0]
    residual = y - smooth
    rms, slope = np.empty_like(y), np.empty_like(y)
    for i in range(len(x)):
        sl = slice(max(0, i - local_window // 2), min(len(x), i + local_window // 2 + 1))
        rms[i] = np.sqrt(np.mean(residual[sl] ** 2))
        slope[i] = np.polynomial.polynomial.polyfit(x[sl] - x[i], smooth[sl], 1)[1]
    return smooth, rms, slope, effective


def resolve_file(root, name, sample, suffix):
    # Blank fields use ONLY exact sample_id + suffix; no fuzzy matching.
    name = name or sample + suffix
    path = (root / name).resolve()
    if path.parent != root.resolve() or path.suffix.lower() != suffix:
        raise ValueError(f'file must be directly inside {root} and have suffix {suffix}')
    if not path.is_file():
        raise ValueError(f'missing file: {path.name}')
    return path


def plot_result(path, sample, detector, x, y, px, py, smooth, rms, slope, cutoff, human):
    fig, axes = plt.subplots(3, 1, figsize=(10, 9), sharex=True)
    axes[0].plot(x, y, lw=.7, label='Raw relative reflectance')
    axes[0].plot(x, smooth, lw=1, label='Smoothed raw')
    axes[0].set_ylabel('Raw reflectance (input units)')
    corrected = axes[0].twinx()
    corrected.plot(px, py, color='gray', alpha=.5, label='Processed (separate scale)')
    corrected.set_ylabel('Processed reflectance (input units)')
    corrected.legend(loc='lower right', fontsize=8)
    axes[1].plot(x, rms)
    axes[1].set_ylabel('Local residual RMS')
    axes[2].plot(x, slope)
    axes[2].set_ylabel('Local slope / cm$^{-1}$')
    axes[2].set_xlabel('Wavenumber (cm$^{-1}$)')
    for ax in axes:
        if cutoff is not None:
            ax.axvline(cutoff, color='red', ls='--', label='Axis cutoff')
        if human is not None:
            ax.axvline(human, color='green', ls=':', label='Human cutoff')
        ax.grid(alpha=.2)
    axes[0].legend(loc='upper right', fontsize=8)
    fig.suptitle(f'{sample} | {detector}')
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def run(data, output, tolerance=.01, smoothing_window=11, local_window=21):
    if not np.isfinite(tolerance) or tolerance < 0:
        raise ValueError('axis tolerance must be finite and nonnegative')
    if any(w < 5 or w % 2 == 0 for w in (smoothing_window, local_window)):
        raise ValueError('windows must be odd and >= 5')
    output.mkdir(parents=True, exist_ok=True)
    # New run directory prevents stale PNG files from looking like current results.
    import tempfile
    artifacts = Path(tempfile.mkdtemp(prefix='run-', dir=output))
    metadata = data / 'metadata.csv'
    rows = []
    if metadata.exists():
        with metadata.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            required = {'sample_id', 'detector'}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError('metadata.csv requires sample_id and detector columns')
            rows = [{k: (v or '').strip() for k, v in r.items() if k is not None} for r in reader]
    counts = {}
    for row in rows:
        sample = row.get('sample_id', '')
        counts[sample] = counts.get(sample, 0) + 1
    # Resolve all pairs first so reused input files are rejected for every affected row.
    pairs, uses = {}, {}
    for i, row in enumerate(rows):
        try:
            pair = (resolve_file(data / 'raw', row.get('raw_file', ''), row['sample_id'], '.dpt'),
                    resolve_file(data / 'processed', row.get('processed_file', ''), row['sample_id'], '.dat'))
            pairs[i] = pair
            for p in pair:
                uses[p] = uses.get(p, 0) + 1
        except (ValueError, OSError):
            pass
    results, used = [], set()
    for i, row in enumerate(rows):
        result = {k: row.get(k, '') for k in FIELDS}
        try:
            sample = row['sample_id']
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', sample) or counts[sample] != 1:
                raise ValueError('sample_id must be unique and use ASCII letters, numbers, _, . or -')
            if row['detector'] not in ('DTGS', 'Bolometer'):
                raise ValueError('detector must be DTGS or Bolometer')
            raw = resolve_file(data / 'raw', row.get('raw_file', ''), sample, '.dpt')
            processed = resolve_file(data / 'processed', row.get('processed_file', ''), sample, '.dat')
            if any(uses[p] > 1 for p in (raw, processed)):
                raise ValueError('input file is assigned to multiple samples; no analysis performed')
            used.update((raw, processed))
            x, y = read_spectrum(raw)
            px, py = read_spectrum(processed)
            cutoff, error = axis_cutoff(x, px, tolerance)
            human = float(row['human_cutoff_cm1']) if row.get('human_cutoff_cm1') else None
            if human is not None and (not np.isfinite(human) or not x[0] <= human <= x[-1]):
                raise ValueError('human cutoff must be finite and inside raw axis range')
            smooth, rms, slope, effective = diagnostics(x, y, smoothing_window, local_window)
            np.savetxt(artifacts / f'{sample}_diagnostics.csv', np.column_stack((x, y, smooth, y-smooth, rms, slope)),
                       delimiter=',', header='wavenumber_cm1,raw_reflectance,smoothed_raw,residual,local_rms,local_slope', comments='')
            plot_result(artifacts / f'{sample}.png', sample, row['detector'], x, y, px, py, smooth, rms, slope, cutoff, human)
            result.update(status='ok' if cutoff is not None else 'no_low_frequency_trim',
                          message='' if cutoff is not None else 'processed includes raw low-frequency endpoint; cutoff not identifiable',
                          raw_file=raw.name, processed_file=processed.name, axis_cutoff_cm1='' if cutoff is None else cutoff,
                          raw_min_cm1=x[0], raw_max_cm1=x[-1], processed_min_cm1=px[0], processed_max_cm1=px[-1],
                          axis_match_max_error_cm1=error, raw_points=len(x), processed_points=len(px),
                          smoothing_window=effective, local_window=local_window)
        except (ValueError, OSError, KeyError) as exc:
            result.update(status='error', message=str(exc))
        results.append(result)
    for folder, suffix in [('raw', '.dpt'), ('processed', '.dat')]:
        for path in sorted((data / folder).glob('*')):
            if path.suffix.lower() == suffix and path.resolve() not in used:
                results.append(dict(status='unassigned', message='file not analyzed; add or fix an unambiguous metadata row',
                                    **{'raw_file' if folder == 'raw' else 'processed_file': path.name}))
    with (output / 'summary.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(results)
    print(f'{len(results)} result rows; diagnostics: {artifacts}; summary: {output / "summary.csv"}')
    return 1 if any(r['status'] in ('error', 'unassigned') for r in results) else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path('data'))
    parser.add_argument('--output-dir', type=Path, default=Path('results'))
    parser.add_argument('--axis-tolerance', type=float, default=.01, help='absolute wavenumber tolerance in cm^-1')
    parser.add_argument('--smoothing-window', type=int, default=11)
    parser.add_argument('--local-window', type=int, default=21)
    args = parser.parse_args(argv)
    try:
        return run(args.data_dir, args.output_dir, args.axis_tolerance, args.smoothing_window, args.local_window)
    except (ValueError, OSError) as exc:
        print(f'Analysis configuration error: {exc}', file=sys.stderr)
        return 2
