import csv
import numpy as np
import pytest
from ftir_cutoff.cli import assess_axes, axis_cutoff, diagnostics, read_spectrum, run


def fixture_data(tmp_path, **overrides):
    data = tmp_path / 'data'
    (data / 'raw').mkdir(parents=True)
    (data / 'processed').mkdir()
    x = np.arange(100., 201.)
    y = .3 + .001*x + .002*np.sin(x)
    np.savetxt(data / 'raw/a.dpt', np.column_stack((x[::-1], y[::-1])))
    np.savetxt(data / 'processed/a.dat', np.column_stack((x[20:], 2*y[20:]+.1)))
    row = dict(sample_id='a', detector='DTGS', raw_file='', processed_file='', human_cutoff_cm1='125')
    row.update(overrides)
    write_metadata(data, [row])
    return data


def write_metadata(data, rows):
    with (data / 'metadata.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['sample_id', 'detector', 'raw_file', 'processed_file', 'human_cutoff_cm1'])
        writer.writeheader()
        writer.writerows(rows)


def summary(output):
    with (output / 'summary.csv').open(encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def test_full_analysis(tmp_path):
    data = fixture_data(tmp_path)
    output = tmp_path / 'results'
    assert run(data, output) == 0
    row = summary(output)[0]
    assert row['status'] == 'ok'
    assert float(row['axis_cutoff_cm1']) == 120
    assert float(row['human_cutoff_cm1']) == 125
    png = next(output.glob('run-*/*.png'))
    assert png.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    values = np.loadtxt(next(output.glob('run-*/*_diagnostics.csv')), delimiter=',', skiprows=1)
    assert values.shape == (101, 6)
    assert np.isfinite(values).all()
    assert np.all(values[:, 4] >= 0)
    assert run(data, output) == 0
    assert len(list(output.glob('run-*'))) == 2


def test_empty_data(tmp_path):
    assert run(tmp_path / 'absent', tmp_path / 'out') == 0
    assert summary(tmp_path / 'out') == []


def test_axis_rules():
    x = np.arange(10.)
    assert axis_cutoff(x, x[3:], .01)[0] == 3
    assert axis_cutoff(x, x, .01)[0] is None
    for bad in (x[::2], x[3:]+.2, np.arange(-1., 5.)):
        with pytest.raises(ValueError):
            axis_cutoff(x, bad, .01)


def test_nonuniform_linear_diagnostics():
    x = np.arange(1., 32.)**1.2
    y = 2*x + 5
    smooth, rms, slope, window = diagnostics(x, y)
    np.testing.assert_allclose(smooth, y, atol=1e-10)
    np.testing.assert_allclose(rms, 0, atol=1e-10)
    np.testing.assert_allclose(slope, 2, atol=1e-10)
    assert window == 11


@pytest.mark.parametrize('change', [dict(raw_file='missing.dpt'), dict(raw_file='../a.dpt'),
    dict(detector='unknown'), dict(human_cutoff_cm1='nan'), dict(human_cutoff_cm1='999')])
def test_invalid_metadata(tmp_path, change):
    data = fixture_data(tmp_path, **change)
    assert run(data, tmp_path / 'out') == 1
    assert summary(tmp_path / 'out')[0]['status'] == 'error'
    assert not list((tmp_path / 'out').glob('run-*/*.png'))


def test_duplicate_assignments(tmp_path):
    data = fixture_data(tmp_path)
    write_metadata(data, [dict(sample_id=s, detector='DTGS', raw_file='a.dpt', processed_file='a.dat') for s in ['a', 'b']])
    assert run(data, tmp_path / 'out') == 1
    assert all(r['status'] == 'error' for r in summary(tmp_path / 'out')[:2])


def test_unassigned_data(tmp_path):
    data = fixture_data(tmp_path)
    (data / 'metadata.csv').unlink()
    assert run(data, tmp_path / 'out') == 1
    assert all(r['status'] == 'unassigned' for r in summary(tmp_path / 'out'))


@pytest.mark.parametrize('content', ['1 2\n'*5, 'header\n', '1 nan\n'*5])
def test_bad_spectrum(tmp_path, content):
    path = tmp_path / 'bad.dpt'
    path.write_text(content)
    with pytest.raises(ValueError):
        read_spectrum(path)


def test_rms_and_slope_definitions():
    x = np.arange(41., dtype=float)
    y = .1*x + .03*np.cos(np.pi*x)
    smooth, rms, slope, _ = diagnostics(x, y, 7, 9)
    np.testing.assert_allclose(rms[20], np.sqrt(np.mean((y[16:25]-smooth[16:25])**2)))
    np.testing.assert_allclose(slope[20], np.polyfit(x[16:25], smooth[16:25], 1)[0])
    assert rms[20] > 0


def test_regridded_input_is_unverified_candidate(tmp_path):
    data = fixture_data(tmp_path)
    x = np.arange(120., 201.) + .25
    np.savetxt(data / 'processed/a.dat', np.column_stack((x, np.ones_like(x))))
    assert run(data, tmp_path / 'out') == 0
    row = summary(tmp_path / 'out')[0]
    assert row['status'] == 'warning'
    assert row['axis_cutoff_cm1'] == ''
    assert float(row['cutoff_candidate_cm1']) == 120.25
    assert row['axis_alignment'] == 'resampled_or_unmatched'
    assert row['cutoff_confidence'] == 'candidate_only'
    assert list((tmp_path / 'out').glob('run-*/*.png'))


def test_duplicate_sample_ids(tmp_path):
    data = fixture_data(tmp_path)
    write_metadata(data, [dict(sample_id='a', detector='DTGS')]*2)
    assert run(data, tmp_path / 'out') == 1
    assert all('unique' in r['message'] for r in summary(tmp_path / 'out')[:2])


def test_short_spectrum_window_and_headers(tmp_path):
    path = tmp_path / 'short.dpt'
    path.write_text('# header\n; comment\n5,1D0\n4,2\n3,3\n2,4\n1,5\n')
    x, y = read_spectrum(path)
    smooth, rms, slope, effective = diagnostics(x, y)
    assert effective == 5
    np.testing.assert_allclose(smooth, y, atol=1e-12)


def test_extended_exact_axis_retains_old_cutoff():
    x = np.arange(100., 201.)
    processed = np.r_[x[20:], np.arange(201., 1000.)]
    assessment = assess_axes(x, processed, .01)
    assert assessment['axis_cutoff_cm1'] == axis_cutoff(x, x[20:], .01)[0]
    assert assessment['axis_alignment'] == 'exact_overlap'
    assert assessment['cutoff_confidence'] == 'axis_verified'
    assert assessment['overlap_points'] == 81


def test_joined_region_after_exact_low_frequency_prefix():
    x = np.arange(100., 201.)
    assessment = assess_axes(x, np.r_[x[20:40], np.arange(140.2, 1000., .2)], .01)
    assert assessment['axis_cutoff_cm1'] == 120
    assert assessment['axis_alignment'] == 'exact_low_frequency_segment'
    assert assessment['low_frequency_match_points'] == 20
    assert 'Only the low-frequency prefix' in assessment['message']


@pytest.mark.parametrize('processed,alignment', [
    (np.arange(300., 400.), 'no_overlap'),
    (np.arange(90., 150.), 'extends_below_raw'),
    (np.arange(198., 205.), 'insufficient_overlap'),
    (np.arange(120.25, 500., .2), 'resampled_or_unmatched'),
    (np.arange(120., 201., 2), 'resampled_or_unmatched'),
])
def test_uncertain_overlap_never_claims_verified_cutoff(processed, alignment):
    a = assess_axes(np.arange(100., 201.), processed, .01)
    assert a['status'] == 'warning'
    assert a['axis_cutoff_cm1'] == ''
    assert a['axis_alignment'] == alignment
    assert a['cutoff_candidate_cm1'] == processed[0]
    assert a['cutoff_confidence'] != 'axis_verified'


def test_extended_axis_without_low_frequency_trim():
    a = assess_axes(np.arange(100., 201.), np.arange(100., 500.), .01)
    assert a['status'] == 'no_low_frequency_trim'
    assert a['axis_cutoff_cm1'] == ''


def test_unmatched_axes_still_save_diagnostics(tmp_path):
    data = fixture_data(tmp_path)
    p = np.arange(300., 400.)
    np.savetxt(data / 'processed/a.dat', np.column_stack((p, np.ones_like(p))))
    assert run(data, tmp_path / 'out') == 0
    assert summary(tmp_path / 'out')[0]['axis_alignment'] == 'no_overlap'
    assert list((tmp_path / 'out').glob('run-*/*.png'))
    assert list((tmp_path / 'out').glob('run-*/*_diagnostics.csv'))


def test_one_bad_sample_does_not_discard_valid_sample(tmp_path):
    data = fixture_data(tmp_path)
    write_metadata(data, [dict(sample_id='broken', detector='Bolometer'),
                          dict(sample_id='a', detector='DTGS')])
    assert run(data, tmp_path / 'out') == 1
    assert [r['status'] for r in summary(tmp_path / 'out')] == ['error', 'ok']
    assert list((tmp_path / 'out').glob('run-*/a.png'))


def test_real_repository_measurements(tmp_path):
    from pathlib import Path
    data = Path(__file__).resolve().parents[1] / 'data'
    expected = {'DL1_FIR_Bolo': 98.36, 'DL2_FIR_Bolo': 94.89,
                'DL3_FIR_Bolo': 92.57, 'DL4_FIR_Bolo': 91.37}
    if not all((data / 'raw' / f'{s}.dpt').exists() and
               (data / 'processed' / f'{s}.dat').exists() for s in expected):
        pytest.skip('Real Bolometer measurements are not included in this checkout')
    assert run(data, tmp_path / 'out') == 0
    rows = {r['sample_id']: r for r in summary(tmp_path / 'out')}
    for sample, reference in expected.items():
        row = rows[sample]
        px, _ = read_spectrum(data / 'processed' / row['processed_file'])
        assert float(row['cutoff_candidate_cm1']) == px[0]
        assert float(row['cutoff_candidate_cm1']) == pytest.approx(reference, abs=.01)
        if sample in ('DL1_FIR_Bolo', 'DL3_FIR_Bolo'):
            assert row['axis_alignment'] == 'exact_low_frequency_segment'
            assert float(row['axis_cutoff_cm1']) == px[0]
        else:
            assert row['axis_alignment'] == 'resampled_or_unmatched'
            assert row['axis_cutoff_cm1'] == ''
            assert row['status'] == 'warning'
        assert list((tmp_path / 'out').glob(f'run-*/{sample}.png'))
        assert list((tmp_path / 'out').glob(f'run-*/{sample}_diagnostics.csv'))
    for row in rows.values():
        if row['detector'] == 'DTGS':
            x, _ = read_spectrum(data / 'raw' / row['raw_file'])
            px, _ = read_spectrum(data / 'processed' / row['processed_file'])
            old, _ = axis_cutoff(x, px, .01)
            assert float(row['axis_cutoff_cm1']) == old
            assert row['axis_alignment'] == 'exact_overlap'
