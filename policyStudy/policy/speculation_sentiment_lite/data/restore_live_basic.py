"""Bounded recovery of a live basic SQLite copy; never modifies the frozen source."""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, sqlite3, struct, time
from datetime import datetime, timezone
from pathlib import Path

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()

def ro(path):
    for suffix in ('-wal', '-journal'):
        s = Path(str(path) + suffix)
        if s.exists() and s.stat().st_size:
            raise RuntimeError(f'Nonempty SQLite sidecar: {s}')
    c = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    c.execute('PRAGMA query_only=ON')
    return c

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source-root', type=Path, required=True)
    p.add_argument('--live-root', type=Path, required=True)
    p.add_argument('--audit-root', type=Path, required=True)
    p.add_argument('--finalize-verified', action='store_true', help='Resume only a hash- and quick_check-verified temporary copy after a failed rename')
    a = p.parse_args()
    src = (a.source_root / 'db_cn_basic.db').resolve()
    live = a.live_root.resolve()
    dest = (live / 'db_cn_basic.db').resolve()
    assert dest.parent == live and src.parent != live and src != dest
    a.audit_root.mkdir(parents=True, exist_ok=True)
    out = a.audit_root / 'basic_restore.json'
    if out.exists() and dest.exists():
        previous = json.loads(out.read_text(encoding='utf-8'))
        if previous.get('status') == 'done' and Path(previous['destination']).resolve() == dest:
            print(json.dumps({'status': 'already_restored', 'destination': str(dest),
                              'current_size': dest.stat().st_size,
                              'preserve_live_updates': True}), flush=True)
            return
    if a.finalize_verified:
        audit = json.loads(out.read_text(encoding='utf-8'))
        temp = Path(audit['temporary']).resolve()
        assert temp.parent == live and Path(audit['source']).resolve() == src
        assert audit['source_sha256'] == audit['copy_sha256']
        assert audit['quick_check'] == ['ok']
        assert temp.stat().st_size == audit['source_before']['size'] == src.stat().st_size
        assert src.stat().st_mtime_ns == audit['source_before']['mtime_ns']
        assert not dest.exists()
        os.replace(temp, dest)
        audit['status'] = 'done'; audit['recovered_rename'] = True
        audit['source_after'] = {'size': src.stat().st_size, 'mtime_ns': src.stat().st_mtime_ns}
        audit['finished_at'] = datetime.now(timezone.utc).isoformat()
        out.write_text(json.dumps(audit, indent=2), encoding='utf-8')
        print(json.dumps(audit), flush=True)
        return
    # A smaller live database can be valid. Only a header-declared truncated
    # SQLite file may be preserved as incomplete and replaced automatically.
    if dest.exists():
        with dest.open('rb') as f: header = f.read(100)
        if len(header) < 100 or header[:16] != b'SQLite format 3\x00':
            raise RuntimeError('Refusing to overwrite existing LIVE file with unrecognized SQLite header')
        page_size = struct.unpack('>H', header[16:18])[0]
        page_size = 65536 if page_size == 1 else page_size
        pages = struct.unpack('>I', header[28:32])[0]
        if not pages or pages * page_size <= dest.stat().st_size:
            raise RuntimeError('Refusing to overwrite an existing LIVE database that is not demonstrably truncated')
        with src.open('rb') as f: source_header = f.read(100)
        if header != source_header:
            raise RuntimeError('Refusing to replace truncated LIVE file whose SQLite header differs from the specified frozen source')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    temp = (live / f'db_cn_basic.db.copying_{stamp}').resolve()
    assert temp.parent == live
    src_stat = src.stat()
    audit = {'source': str(src), 'destination': str(dest), 'temporary': str(temp),
             'source_before': {'size': src_stat.st_size, 'mtime_ns': src_stat.st_mtime_ns},
             'status': 'running', 'started_at': stamp}
    def save():
        out.write_text(json.dumps(audit, indent=2), encoding='utf-8')
    save()
    print(json.dumps(audit), flush=True)
    try:
        with ro(src) as c:
            audit['source_header'] = {'page_count': c.execute('PRAGMA page_count').fetchone()[0],
                                      'page_size': c.execute('PRAGMA page_size').fetchone()[0]}
        c.close()
        if shutil.disk_usage(live).free < src_stat.st_size + 1024 ** 3:
            raise RuntimeError('Insufficient space for complete temporary copy')
        if dest.exists() and dest.stat().st_size != src_stat.st_size:
            incomplete = Path(str(dest) + f'.incomplete_{stamp}').resolve()
            assert incomplete.parent == live
            dest.rename(incomplete)
            audit['preserved_incomplete'] = str(incomplete)
            save()
        h = hashlib.sha256()
        copied = 0
        start = time.monotonic()
        last = start
        with src.open('rb') as fi, temp.open('xb') as fo:
            for b in iter(lambda: fi.read(8 * 1024 * 1024), b''):
                fo.write(b); h.update(b); copied += len(b)
                now = time.monotonic()
                if now - last > 20:
                    print(json.dumps({'copied_bytes': copied, 'total_bytes': src_stat.st_size,
                                      'elapsed_s': round(now-start,1)}), flush=True)
                    last = now
            fo.flush(); os.fsync(fo.fileno())
        audit['source_sha256'] = h.hexdigest()
        audit['copy_sha256'] = digest(temp)
        assert temp.stat().st_size == src_stat.st_size
        assert audit['copy_sha256'] == audit['source_sha256']
        assert src.stat().st_size == src_stat.st_size and src.stat().st_mtime_ns == src_stat.st_mtime_ns
        save()
        print('Copy hashes matched; running read-only SQLite quick_check', flush=True)
        with ro(temp) as c:
            audit['quick_check'] = [r[0] for r in c.execute('PRAGMA quick_check')]
            audit['table_count'] = c.execute("select count(*) from sqlite_master where type='table'").fetchone()[0]
        c.close()
        assert audit['quick_check'] == ['ok']
        os.replace(temp, dest)
        audit['status'] = 'done'
        audit['elapsed_s'] = round(time.monotonic()-start, 2)
        audit['source_after'] = {'size': src.stat().st_size, 'mtime_ns': src.stat().st_mtime_ns}
        save()
        print(json.dumps(audit), flush=True)
    except BaseException as e:
        audit['status'] = 'failed'; audit['error'] = str(e); save()
        raise

if __name__ == '__main__':
    main()
