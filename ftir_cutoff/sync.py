"""Conservative one-way synchronization from local folders to a dedicated Git clone."""
import argparse
from contextlib import contextmanager
import csv
import io
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time

LOG = logging.getLogger('ftir-sync')
DATA_PATHS = ('data/raw', 'data/processed', 'data/metadata.csv')
COLUMNS = ['sample_id', 'detector', 'raw_file', 'processed_file', 'human_cutoff_cm1']


def git(repo, *args):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never')
    try:
        result = subprocess.run(['git', '-C', str(repo), *args], env=env, timeout=60,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f'Git {args[0]} timed out; check network or credentials manually') from exc
    if result.returncode:
        # Do not log stderr: credential helpers and remote URLs may contain secrets.
        raise RuntimeError(f'Git {args[0]} failed (exit {result.returncode}); check credentials, network, branch protection or conflicts in Git manually')
    return result.stdout.decode('utf-8', errors='strict').strip()


@contextmanager
def exclusive_lock(path):
    with path.open('a+b') as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('another sync is running') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def stamp(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def discover(folder, extension):
    found = {}
    for path in folder.iterdir():
        if path.is_file() and path.suffix.lower() == extension:
            found.setdefault(path.stem.casefold(), []).append(path)
    return found


def stable_pairs(raw, processed, seconds):
    """Observe both files across a delay; skip recent, ambiguous or changing files."""
    raw_files, processed_files = discover(raw, '.dpt'), discover(processed, '.dat')
    candidates = []
    for key in sorted(raw_files.keys() | processed_files.keys()):
        a, b = raw_files.get(key, []), processed_files.get(key, [])
        if len(a) != 1 or len(b) != 1 or a[0].stem != b[0].stem:
            LOG.warning('Incomplete or ambiguous pair skipped: %s', key)
            continue
        try:
            before = tuple(stamp(p) for p in (a[0], b[0]))
            if any(time.time_ns() - s[1] < seconds * 1e9 for s in before):
                LOG.info('Recent pair deferred: %s', key)
                continue
            candidates.append((a[0], b[0], before))
        except OSError:
            LOG.warning('Unreadable pair deferred: %s', key)
    if candidates:
        time.sleep(seconds)
    for a, b, before in candidates:
        try:
            if tuple(stamp(p) for p in (a, b)) != before:
                LOG.info('Changing pair deferred: %s', a.stem)
                continue
            contents = (a.read_bytes(), b.read_bytes())
            # Re-read detects same-size changes and checks both snapshots together.
            if tuple(stamp(p) for p in (a, b)) != before or contents != (a.read_bytes(), b.read_bytes()):
                LOG.info('Changing pair deferred: %s', a.stem)
                continue
            if not all(contents):
                LOG.warning('Empty pair deferred: %s', a.stem)
                continue
            yield a.stem, contents
        except OSError:
            LOG.warning('Locked or unreadable pair deferred: %s', a.stem)


def prepare(repo, pairs):
    """Build all changes in memory; preserve metadata cells and unknown columns."""
    metadata = repo / 'data/metadata.csv'
    existing = metadata.read_bytes() if metadata.exists() else b''
    if existing:
        reader = csv.DictReader(io.StringIO(existing.decode('utf-8-sig'), newline=''))
        fields = reader.fieldnames
        if not fields or len(fields) != len(set(fields)) or not {'sample_id', 'detector'}.issubset(fields):
            raise ValueError('metadata has missing or duplicate columns')
        rows = list(reader)
        if any(None in r or any(v is None for v in r.values()) for r in rows):
            raise ValueError('metadata has malformed rows')
    else:
        fields, rows = COLUMNS.copy(), []
    for column in COLUMNS:
        if column not in fields:
            fields.append(column)
    changes, added = {}, False
    for sample, contents in pairs:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', sample):
            LOG.warning('Unsupported sample ID skipped: %s', sample)
            continue
        matching = [r for r in rows if r['sample_id'].strip().casefold() == sample.casefold()]
        names = (sample + '.dpt', sample + '.dat')
        if matching:
            if len(matching) != 1 or matching[0]['sample_id'] != sample:
                LOG.warning('Ambiguous metadata sample skipped: %s', sample)
                continue
            row = matching[0]
            if any((row.get(col) or sample + ext) != name for col, ext, name in
                   zip(('raw_file', 'processed_file'), ('.dpt', '.dat'), names)):
                LOG.warning('Existing metadata mapping preserved; pair skipped: %s', sample)
                continue
        # Refuse a filename already assigned to a different metadata sample.
        if any(r not in matching and any((r.get(col) or r['sample_id'] + ext).casefold() == name.casefold()
               for col, ext, name in zip(('raw_file', 'processed_file'), ('.dpt', '.dat'), names)) for r in rows):
            LOG.warning('File already assigned; pair skipped: %s', sample)
            continue
        targets = (repo / DATA_PATHS[0] / names[0], repo / DATA_PATHS[1] / names[1])
        if any(t.parent.exists() and any(p.name.casefold() == t.name.casefold() and p.name != t.name
                                       for p in t.parent.iterdir()) for t in targets):
            LOG.warning('Case collision skipped: %s', sample)
            continue
        for target, content in zip(targets, contents):
            if not target.exists() or target.read_bytes() != content:
                changes[target] = content
        if not matching:
            rows.append(dict(sample_id=sample, detector='Bolometer' if 'bolo' in sample.casefold() else 'DTGS',
                             raw_file=names[0], processed_file=names[1], human_cutoff_cm1=''))
            added = True
    if added:
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        changes[metadata] = stream.getvalue().encode('utf-8-sig')
    return changes


def atomic_write(path, content):
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.ftir-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def synchronize(repo, raw, processed, seconds=5):
    repo, raw, processed = repo.resolve(), raw.resolve(), processed.resolve()
    if not raw.is_dir() or not processed.is_dir() or raw == processed:
        raise ValueError('select two different existing source folders')
    if any(p == repo or repo in p.parents for p in (raw, processed)):
        raise ValueError('source folders must be outside the dedicated sync clone')
    if Path(git(repo, 'rev-parse', '--show-toplevel')).resolve() != repo:
        raise ValueError('repo must be the Git root')
    state_dir = Path(git(repo, 'rev-parse', '--absolute-git-dir')) / 'ftir-sync'
    state_dir.mkdir(exist_ok=True)
    with exclusive_lock(state_dir / 'lock'):
        if git(repo, 'status', '--porcelain', '--untracked-files=all'):
            raise RuntimeError('clone has local changes; sync refuses to modify or commit them')
        branch = git(repo, 'symbolic-ref', '--short', 'HEAD')
        if git(repo, 'config', f'branch.{branch}.remote') != 'origin':
            raise RuntimeError('branch must track origin')
        merge_ref = git(repo, 'config', f'branch.{branch}.merge')
        if not merge_ref.startswith('refs/heads/'):
            raise RuntimeError('branch must track an origin branch')
        git(repo, 'fetch', 'origin')
        upstream = git(repo, 'rev-parse', '--abbrev-ref', '@{upstream}')
        head, remote = git(repo, 'rev-parse', 'HEAD'), git(repo, 'rev-parse', '@{upstream}')
        pending = state_dir / 'pending'
        if head != remote:
            ahead = git(repo, 'rev-list', f'{upstream}..HEAD')
            if ahead:
                if not pending.exists() or pending.read_text().strip() != head or ahead.splitlines() != [head]:
                    raise RuntimeError('unexpected local commits or divergent history; resolve Git manually')
                if git(repo, 'rev-parse', 'HEAD^') != remote:
                    raise RuntimeError('remote advanced after failed push; resolve Git manually')
                git(repo, 'push', 'origin', f'HEAD:{merge_ref}')
                pending.unlink()
                LOG.info('Previously committed data pushed successfully')
            else:
                git(repo, 'merge', '--ff-only', upstream)
        if pending.exists():
            pending.unlink()
        # Reject symlinks in data paths before writing outside the intended tree.
        for path in (repo / 'data', *(repo / p for p in DATA_PATHS)):
            if path.is_symlink():
                raise ValueError('symlinks in destination data paths are unsupported')
        changes = prepare(repo, stable_pairs(raw, processed, seconds))
        if any(path.is_symlink() for path in changes):
            raise ValueError('symlink destination is unsupported')
        if not changes:
            LOG.info('No new or changed complete stable pairs')
            return
        backups = {path: path.read_bytes() if path.exists() else None for path in changes}
        committed = False
        try:
            for path, content in changes.items():
                atomic_write(path, content)
            git(repo, 'add', '--', *[str(p.relative_to(repo)) for p in changes])
            git(repo, 'commit', '-m', 'Sync FTIR measurement data', '--only', '--',
                *[str(p.relative_to(repo)) for p in changes])
            committed = True
        finally:
            if not committed:
                git(repo, 'reset', '--', *[str(p.relative_to(repo)) for p in changes])
                for path, content in backups.items():
                    if content is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_write(path, content)
        atomic_write(pending, (git(repo, 'rev-parse', 'HEAD') + '\n').encode())
        git(repo, 'push', 'origin', f'HEAD:{merge_ref}')
        pending.unlink()
        LOG.info('Committed and pushed %d changed files', len(changes))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args(argv)
    log_dir = args.config.resolve().parent
    log_dir.mkdir(parents=True, exist_ok=True)
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[RotatingFileHandler(log_dir / 'sync.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8'), logging.StreamHandler()])
    try:
        config = json.loads(args.config.read_text(encoding='utf-8-sig'))
        if not isinstance(config, dict) or any(not isinstance(config.get(k), str) for k in ('repo', 'raw_folder', 'processed_folder')):
            raise ValueError('config requires repo, raw_folder and processed_folder string paths')
        seconds = float(config.get('stability_seconds', 5))
        if not 1 <= seconds <= 60:
            raise ValueError('stability_seconds must be between 1 and 60')
        synchronize(Path(config['repo']), Path(config['raw_folder']), Path(config['processed_folder']), seconds)
        return 0
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        LOG.error('%s', exc)
        return 1


if __name__ == '__main__':
    sys.exit(main())
