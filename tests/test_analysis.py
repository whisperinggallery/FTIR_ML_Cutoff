import csv
import numpy as np
import pytest
from ftir_cutoff.cli import axis_cutoff, diagnostics, read_spectrum, run


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


def test_regridded_input_rejected(tmp_path):
    data = fixture_data(tmp_path)
    x = np.arange(120., 201.) + .25
    np.savetxt(data / 'processed/a.dat', np.column_stack((x, np.ones_like(x))))
    assert run(data, tmp_path / 'out') == 1
    assert 'contiguous' in summary(tmp_path / 'out')[0]['message']
    assert not list((tmp_path / 'out').glob('run-*/*.png'))


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
