import csv
import io
from pathlib import Path
import subprocess

import pytest
from ftir_cutoff import sync


def command(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.STDOUT).decode().strip()


def sources(tmp_path):
    raw, processed = tmp_path / 'raw source', tmp_path / 'processed source'
    raw.mkdir(); processed.mkdir()
    return raw, processed


def test_pairs_stability_and_incomplete(tmp_path, monkeypatch):
    raw, processed = sources(tmp_path)
    (raw / 'a.dpt').write_bytes(b'raw')
    (processed / 'a.dat').write_bytes(b'processed')
    (raw / 'alone.dpt').write_bytes(b'wait')
    assert list(sync.stable_pairs(raw, processed, 0)) == [('a', (b'raw', b'processed'))]
    monkeypatch.setattr(sync.time, 'sleep', lambda seconds: (raw / 'a.dpt').write_bytes(b'changed'))
    assert list(sync.stable_pairs(raw, processed, 0)) == []


def test_recent_and_empty_pairs(tmp_path, monkeypatch):
    raw, processed = sources(tmp_path)
    (raw / 'a.dpt').write_bytes(b'x')
    (processed / 'a.dat').write_bytes(b'y')
    assert list(sync.stable_pairs(raw, processed, 60)) == []
    (processed / 'a.dat').write_bytes(b'')
    assert list(sync.stable_pairs(raw, processed, 0)) == []


def test_metadata_preserved_and_detector(tmp_path):
    (tmp_path / 'data').mkdir()
    meta = tmp_path / 'data/metadata.csv'
    meta.write_text('sample_id,detector,raw_file,processed_file,human_cutoff_cm1,note\nDL1,Bolometer,DL1.dpt,DL1.dat,123,manual\n')
    changes = sync.prepare(tmp_path, [('DL1', (b'a', b'b')), ('DL2_bOlO', (b'c', b'd')), ('DL3', (b'e', b'f'))])
    rows = list(csv.DictReader(io.StringIO(changes[meta].decode('utf-8-sig'))))
    assert rows[0]['human_cutoff_cm1'] == '123'
    assert rows[0]['detector'] == 'Bolometer'
    assert rows[0]['note'] == 'manual'
    assert [r['detector'] for r in rows] == ['Bolometer', 'Bolometer', 'DTGS']
    for path, data in changes.items():
        sync.atomic_write(path, data)
    assert sync.prepare(tmp_path, [('DL1', (b'a', b'b')), ('DL2_bOlO', (b'c', b'd'))]) == {}
    changed = sync.prepare(tmp_path, [('DL1', (b'new', b'b'))])
    assert list(changed) == [tmp_path / 'data/raw/DL1.dpt']


@pytest.mark.parametrize('text', [
    'sample_id,detector,raw_file\na,DTGS,other.dpt\n',
    'sample_id,detector\na,DTGS\na,DTGS\n',
    'sample_id,detector,raw_file\nb,DTGS,a.dpt\n',
])
def test_ambiguous_metadata_not_overwritten(tmp_path, text):
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/metadata.csv').write_text(text)
    assert sync.prepare(tmp_path, [('a', (b'x', b'y'))]) == {}
    assert (tmp_path / 'data/metadata.csv').read_text() == text


@pytest.fixture
def git_setup(tmp_path):
    remote = tmp_path / 'remote.git'
    subprocess.check_call(['git', 'init', '--bare', str(remote)], stdout=subprocess.DEVNULL)
    repo = tmp_path / 'clone'
    subprocess.check_call(['git', 'clone', str(remote), str(repo)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    command(repo, 'config', 'user.name', 'Sync Test')
    command(repo, 'config', 'user.email', 'test@example.com')
    (repo / 'README.md').write_text('unchanged analysis')
    command(repo, 'add', '.')
    command(repo, 'commit', '-m', 'initial')
    command(repo, 'push', '-u', 'origin', 'HEAD')
    raw, processed = sources(tmp_path)
    (raw / 'a.dpt').write_bytes(b'raw')
    (processed / 'a.dat').write_bytes(b'processed')
    return repo, raw, processed, remote


def test_real_git_sync_idempotence_no_deletions(git_setup):
    repo, raw, processed, remote = git_setup
    sync.synchronize(repo, raw, processed, 0)
    head = command(repo, 'rev-parse', 'HEAD')
    assert command(remote, 'rev-parse', 'HEAD') == head
    assert set(command(repo, 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').splitlines()) == {
        'data/raw/a.dpt', 'data/processed/a.dat', 'data/metadata.csv'}
    sync.synchronize(repo, raw, processed, 0)
    assert command(repo, 'rev-parse', 'HEAD') == head
    (raw / 'a.dpt').unlink()
    sync.synchronize(repo, raw, processed, 0)
    assert (repo / 'data/raw/a.dpt').read_bytes() == b'raw'
    assert command(repo, 'rev-parse', 'HEAD') == head
    assert (repo / 'README.md').read_text() == 'unchanged analysis'


def test_dirty_clone_untouched(git_setup):
    repo, raw, processed, remote = git_setup
    (repo / 'README.md').write_text('human edit')
    with pytest.raises(RuntimeError, match='local changes'):
        sync.synchronize(repo, raw, processed, 0)
    assert (repo / 'README.md').read_text() == 'human edit'
    assert not (repo / 'data').exists()


def test_failed_push_retried_without_duplicate(git_setup, monkeypatch):
    repo, raw, processed, remote = git_setup
    original = sync.git
    def fail(repo, *args):
        if args[0] == 'push':
            raise RuntimeError('simulated authentication failure')
        return original(repo, *args)
    monkeypatch.setattr(sync, 'git', fail)
    with pytest.raises(RuntimeError, match='authentication'):
        sync.synchronize(repo, raw, processed, 0)
    head = command(repo, 'rev-parse', 'HEAD')
    assert command(remote, 'rev-parse', 'HEAD') != head
    monkeypatch.setattr(sync, 'git', original)
    sync.synchronize(repo, raw, processed, 0)
    assert command(repo, 'rev-parse', 'HEAD') == head == command(remote, 'rev-parse', 'HEAD')


def test_commit_failure_rolls_back(git_setup, monkeypatch):
    repo, raw, processed, remote = git_setup
    original = sync.git
    def fail(repo, *args):
        if args[0] == 'commit':
            raise RuntimeError('simulated commit error')
        return original(repo, *args)
    monkeypatch.setattr(sync, 'git', fail)
    with pytest.raises(RuntimeError, match='commit error'):
        sync.synchronize(repo, raw, processed, 0)
    assert command(repo, 'status', '--porcelain') == ''
    assert not (repo / 'data/raw/a.dpt').exists()


def test_unexpected_local_commit_blocked(git_setup):
    repo, raw, processed, remote = git_setup
    (repo / 'README.md').write_text('local code')
    command(repo, 'add', 'README.md')
    command(repo, 'commit', '-m', 'unrelated work')
    with pytest.raises(RuntimeError, match='unexpected local commits'):
        sync.synchronize(repo, raw, processed, 0)
    assert not (repo / 'data').exists()


def advance_remote(repo, remote, tmp_path):
    other = tmp_path / 'other'
    subprocess.check_call(['git', 'clone', str(remote), str(other)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    command(other, 'config', 'user.name', 'Other')
    command(other, 'config', 'user.email', 'other@example.com')
    (other / 'README.md').write_text('remote edit')
    command(other, 'add', '.')
    command(other, 'commit', '-m', 'remote update')
    command(other, 'push')


def test_remote_fast_forward_preserves_code(git_setup, tmp_path):
    repo, raw, processed, remote = git_setup
    advance_remote(repo, remote, tmp_path)
    sync.synchronize(repo, raw, processed, 0)
    assert (repo / 'README.md').read_text() == 'remote edit'
    assert command(repo, 'rev-parse', 'HEAD') == command(remote, 'rev-parse', 'HEAD')


def test_remote_divergence_preserves_pending_files(git_setup, tmp_path, monkeypatch):
    repo, raw, processed, remote = git_setup
    original = sync.git
    def fail(repo, *args):
        if args[0] == 'push':
            raise RuntimeError('push failure')
        return original(repo, *args)
    monkeypatch.setattr(sync, 'git', fail)
    with pytest.raises(RuntimeError):
        sync.synchronize(repo, raw, processed, 0)
    head = command(repo, 'rev-parse', 'HEAD')
    monkeypatch.setattr(sync, 'git', original)
    advance_remote(repo, remote, tmp_path)
    with pytest.raises(RuntimeError, match='remote advanced'):
        sync.synchronize(repo, raw, processed, 0)
    assert command(repo, 'rev-parse', 'HEAD') == head
    assert (repo / 'data/raw/a.dpt').read_bytes() == b'raw'
    assert command(repo, 'status', '--porcelain') == ''


def test_cli_logs_errors_without_traceback(tmp_path):
    config = tmp_path / 'config.json'
    config.write_text('{bad json')
    assert sync.main(['--config', str(config)]) == 1
    assert (tmp_path / 'sync.log').exists()


def test_read_locked_pair_deferred(tmp_path, monkeypatch):
    raw, processed = sources(tmp_path)
    (raw / 'a.dpt').write_bytes(b'raw')
    (processed / 'a.dat').write_bytes(b'processed')
    original = Path.read_bytes
    def locked(path):
        if path == raw / 'a.dpt':
            raise PermissionError('locked by writer')
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', locked)
    assert list(sync.stable_pairs(raw, processed, 0)) == []


def test_lock_prevents_overlapping_runs(tmp_path):
    with sync.exclusive_lock(tmp_path / 'lock'):
        with pytest.raises(RuntimeError, match='another sync'):
            with sync.exclusive_lock(tmp_path / 'lock'):
                pytest.fail('second lock should not be acquired')


def test_case_mismatch_not_guessed(tmp_path):
    raw, processed = sources(tmp_path)
    (raw / 'DL1.dpt').write_bytes(b'raw')
    (processed / 'dl1.dat').write_bytes(b'processed')
    assert list(sync.stable_pairs(raw, processed, 0)) == []
