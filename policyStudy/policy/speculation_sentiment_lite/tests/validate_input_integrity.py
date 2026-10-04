"""Final full-content non-mutation check; read only, resumable evidence output."""
from pathlib import Path
import argparse, hashlib, json, subprocess
from datetime import datetime, timezone


def main():
    p=argparse.ArgumentParser()
    for key in ['manifest','baseline-validation','old-snapshot','output']:
        p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--theme-source',type=Path);p.add_argument('--theme-source-sha256')
    p.add_argument('--main-repo',type=Path);p.add_argument('--status-before',type=Path)
    a=p.parse_args();a.output.parent.mkdir(parents=True,exist_ok=True)
    manifest=json.loads(a.manifest.read_text(encoding='utf-8-sig'))
    assert manifest.get('finished_at'),'Cannot validate an unfinished input manifest'
    baseline=json.loads(a.baseline_validation.read_text(encoding='utf-8-sig'))
    targets=[]
    for group,label in [('frozen_databases','frozen'),('databases','live')]:
        for name,item in manifest[group].items():targets.append((label+'.'+name,Path(item['path']),item['sha256']))
    targets.extend(('old_snapshot.'+name,a.old_snapshot/name,digest) for name,digest in baseline['input_sha256'].items())
    if a.theme_source:targets.append(('original_theme_source',a.theme_source,a.theme_source_sha256))
    result={'status':'running','started_at':datetime.now(timezone.utc).isoformat(),'files':[]}
    def save():a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    save()
    for label,path,expected in targets:
        before=path.stat();h=hashlib.sha256()
        with path.open('rb') as f:
            for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
        after=path.stat();digest=h.hexdigest()
        row={'source':label,'bytes':after.st_size,'sha256':digest,'expected_sha256':expected,
             'hash_equal':digest==expected,'unchanged_during_read':(before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns)}
        result['files'].append(row);save();print(json.dumps(row),flush=True)
        assert row['hash_equal'] and row['unchanged_during_read'],label
    if a.main_repo and a.status_before:
        actual=subprocess.check_output(['git','status','--short'],cwd=a.main_repo).decode('utf-8').splitlines()
        before=a.status_before.read_text(encoding='utf-8-sig').splitlines()
        result['main_worktree_status']={'identical':actual==before,'before_lines':len(before),'after_lines':len(actual),
            'scope':'Exact status comparison plus isolated writes; not a retrospective content hash of every unrelated file'}
        save();assert actual==before,'Main worktree status changed'
    result['status']='passed';result['finished_at']=datetime.now(timezone.utc).isoformat();save()


if __name__=='__main__':main()
