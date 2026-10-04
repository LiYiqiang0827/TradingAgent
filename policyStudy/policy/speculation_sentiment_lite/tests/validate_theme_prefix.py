"""Integration check: recompute historical theme queries at fixed earlier cutoffs."""
from pathlib import Path
import argparse, json, subprocess, sys

def main():
    p=argparse.ArgumentParser()
    for k in ['repo-root','theme-db','full-dir','output']:p.add_argument('--'+k,type=Path,required=True)
    p.add_argument('--cutoffs',default='20260331,20260630,20260909')
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    full=json.loads((a.full_dir/'theme_review_raw.json').read_text(encoding='utf-8'))
    rows=[]
    for end in a.cutoffs.split(','):
        out=a.output/end
        cmd=[sys.executable,str(Path(__file__).resolve().parents[1]/'theme_export.py'),'--repo-root',str(a.repo_root),'--theme-db',str(a.theme_db),'--output',str(out),'--end',end]
        run=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8')
        (a.output/f'{end}.log').write_text(run.stdout+'\n'+run.stderr,encoding='utf-8')
        if run.returncode:raise RuntimeError(f'Theme query failed: {end}; see log')
        prefix=json.loads((out/'theme_review_raw.json').read_text(encoding='utf-8'))
        expected=[r for r in full if r['trade_date'].replace('-','')<=end]
        row={'cutoff':end,'days':len(prefix),'expected_days':len(expected),'whole_return_equal':prefix==expected};rows.append(row)
        print(json.dumps(row),flush=True)
        (a.output/'validation.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
        assert row['whole_return_equal'],f'Theme prefix changed: {end}'
if __name__=='__main__':main()
