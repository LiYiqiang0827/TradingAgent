"""Validate a byte-equivalent NVMe cache while preserving the original LIVE copy.

The caller closes its original slow validator only after this command succeeds,
then atomically finalizes the D: copy. No source database is written or rehashed.
"""
import argparse,hashlib,json,os,shutil,sqlite3,time
from pathlib import Path
from datetime import datetime,timezone

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while chunk:=f.read(16*1024*1024):h.update(chunk)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser();p.add_argument('--live-root',type=Path,required=True)
    p.add_argument('--audit-root',type=Path,required=True);p.add_argument('--validation-root',type=Path,required=True)
    a=p.parse_args();live=a.live_root.resolve();audit=a.audit_root.resolve();validation=a.validation_root.resolve()
    state=json.loads((audit/'basic_restore.json').read_text(encoding='utf-8'))
    original=Path(state['temporary']).resolve();assert original.parent==live
    if state['source_sha256']!=state['copy_sha256']:raise RuntimeError('Unverified original temporary copy')
    expected=state['copy_sha256'];before=original.stat()
    if before.st_size!=state['source_before']['size']:raise RuntimeError('Unexpected original size')
    validation.mkdir(parents=True,exist_ok=True);cache=(validation/'db_cn_basic.validation.db').resolve()
    assert cache.parent==validation
    if cache.exists():raise FileExistsError('Validation cache already exists; refusing overwrite')
    if shutil.disk_usage(validation).free<before.st_size+1024**3:raise RuntimeError('Insufficient NVMe space')
    output=audit/'basic_nvme_validation.json'
    result={'status':'running','started_at':datetime.now(timezone.utc).isoformat(),'original':str(original),'cache':str(cache),
            'original_before':{'size':before.st_size,'mtime_ns':before.st_mtime_ns},'expected_sha256':expected,
            'authorization':'parent explicitly approved bounded NVMe cache under supplied validation-root'}
    def save():output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    save();print(json.dumps(result),flush=True);start=time.monotonic();last=start;copied=0;h=hashlib.sha256()
    try:
        with original.open('rb') as fi,cache.open('xb') as fo:
            while chunk:=fi.read(16*1024*1024):
                fo.write(chunk);h.update(chunk);copied+=len(chunk)
                if time.monotonic()-last>20:
                    print(json.dumps({'nvme_copied_bytes':copied,'total_bytes':before.st_size}),flush=True);last=time.monotonic()
            fo.flush();os.fsync(fo.fileno())
        result['copy_stream_sha256']=h.hexdigest();result['independent_cache_sha256']=digest(cache)
        after=original.stat();result['original_after']={'size':after.st_size,'mtime_ns':after.st_mtime_ns}
        assert result['original_after']==result['original_before']
        assert cache.stat().st_size==before.st_size
        assert result['copy_stream_sha256']==result['independent_cache_sha256']==expected
        save();print('NVMe stream and independent hashes matched; full readonly quick_check starting',flush=True)
        c=sqlite3.connect(cache.as_uri()+'?mode=ro&immutable=1',uri=True)
        try:
            c.execute('pragma query_only=on');c.execute('pragma cache_size=-131072')
            result['quick_check']=[r[0] for r in c.execute('pragma quick_check')]
            result['table_count']=c.execute("select count(*) from sqlite_master where type='table'").fetchone()[0]
        finally:c.close()
        assert result['quick_check']==['ok']
        result['status']='validated_await_finalization';result['elapsed_s']=round(time.monotonic()-start,3)
        result['finished_at']=datetime.now(timezone.utc).isoformat();save();print(json.dumps(result),flush=True)
    except BaseException as e:
        result['status']='failed';result['error']=str(e);save();raise
if __name__=='__main__':main()
