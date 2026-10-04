"""Atomically finalize the original D: bytes after equivalent full NVMe validation.

The caller must first stop and join its original slow read-only validator.
"""
import argparse,json,os
from pathlib import Path
from datetime import datetime,timezone

def main():
    p=argparse.ArgumentParser();p.add_argument('--live-root',type=Path,required=True)
    p.add_argument('--audit-root',type=Path,required=True);p.add_argument('--validation-root',type=Path,required=True)
    a=p.parse_args();live=a.live_root.resolve();audit=a.audit_root.resolve();validation=a.validation_root.resolve()
    out=audit/'basic_restore.json';original=json.loads(out.read_text(encoding='utf-8'))
    check=json.loads((audit/'basic_nvme_validation.json').read_text(encoding='utf-8'))
    stopped=json.loads((audit/'original_slow_validator_end.json').read_text(encoding='utf-8-sig'))
    assert stopped['status']=='stopped_after_byte_equivalent_nvme_full_quick_check'
    assert check['status']=='validated_await_finalization' and check['quick_check']==['ok']
    assert original['source_sha256']==original['copy_sha256']==check['expected_sha256']==check['copy_stream_sha256']==check['independent_cache_sha256']
    temp=Path(original['temporary']).resolve();dest=(live/'db_cn_basic.db').resolve();cache=Path(check['cache']).resolve()
    assert temp==Path(check['original']).resolve() and temp.parent==dest.parent==live
    assert cache.parent==validation and cache.name=='db_cn_basic.validation.db'
    stat=temp.stat();assert {'size':stat.st_size,'mtime_ns':stat.st_mtime_ns}==check['original_before']==check['original_after']
    source=Path(original['source']).resolve();sstat=source.stat()
    assert {'size':sstat.st_size,'mtime_ns':sstat.st_mtime_ns}==original['source_before']
    if dest.exists():raise FileExistsError('LIVE standard destination already exists; refusing overwrite')
    if cache.stat().st_size!=stat.st_size:raise RuntimeError('Cache size changed')
    os.replace(temp,dest)
    original.update(status='done',quick_check=['ok'],table_count=check['table_count'],source_after=original['source_before'],
                    quick_check_basis='Full readonly PRAGMA quick_check on byte-equivalent NVMe copy; D original unchanged throughout; old D validator explicitly stopped and joined',
                    nvme_evidence=str(audit/'basic_nvme_validation.json'),original_slow_validator_end=stopped,
                    finished_at=datetime.now(timezone.utc).isoformat())
    out.write_text(json.dumps(original,indent=2),encoding='utf-8')
    # Delete only the exact newly created validation file, never a directory.
    cache.unlink()
    check.update(status='finalized',live_destination=str(dest),cache_deleted=True,finished_at=original['finished_at'])
    (audit/'basic_nvme_validation.json').write_text(json.dumps(check,indent=2),encoding='utf-8')
    print(json.dumps({'status':'done','live_destination':str(dest),'size':dest.stat().st_size,'quick_check':['ok'],'cache_deleted':True}),flush=True)
if __name__=='__main__':main()
