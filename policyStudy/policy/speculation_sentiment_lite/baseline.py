"""Faithful B0 execution of the archived five scripts on the old parquet snapshot."""
from pathlib import Path
import argparse, hashlib, json
import pandas as pd

def run(snapshot, reference_dir, output):
    snapshot, reference_dir, output = map(Path, (snapshot, reference_dir, output))
    output.mkdir(parents=True, exist_ok=True)
    (output / 'out').mkdir(exist_ok=True)
    def read_source(path, **kwargs):
        df = pd.read_parquet(snapshot / (Path(path).stem + '.parquet'))
        dtype = kwargs.get('dtype')
        if dtype is str:
            for c in df:
                df[c] = df[c].map(lambda x: str(x) if pd.notna(x) else None)
        elif isinstance(dtype, dict):
            for c in dtype:
                df[c] = df[c].map(lambda x: str(x) if pd.notna(x) else None)
        return df
    ns = {'read_source': read_source}
    for name in ['build.py', 'st_fix.py', 'compute.py', 'score.py', 'export.py']:
        code = (reference_dir / name).read_text(encoding='utf-8')
        code = code.replace('/tmp/sent', output.as_posix())
        code = code.replace('pd.read_csv(', 'read_source(')
        print('Running', name, flush=True)
        exec(compile(code, name, 'exec'), ns)
    raw = pd.read_pickle(output / 'daily_raw.pkl')
    raw.to_csv(output / 'daily_raw.csv', index=False)
    result = pd.read_pickle(output / 'scored.pkl')
    result['trade_date'] = pd.to_datetime(result.date).dt.strftime('%Y-%m-%d')
    result['score5'] = result.score
    result['grade5'] = result.grade
    result.to_csv(output / 'baseline_daily.csv', index=False)
    ref = pd.read_csv(next(reference_dir.glob('*.csv')))
    actual = pd.read_csv(next((output / 'out').glob('*.csv')))
    merged = ref.merge(actual, on='日期', how='outer', suffixes=('_reference','_computed'), indicator=True)
    numeric = ['接力情绪分','M1连板接力','M2强势股反馈','M3涨跌停结构','M4中位股生态','M5流动性','涨停数','炸板数','跌停数','合格股票数']
    for c in numeric:
        merged[c + '_diff'] = merged[c + '_computed'] - merged[c + '_reference']
    merged['grade_match'] = merged['档位_reference'] == merged['档位_computed']
    merged.to_csv(output / 'baseline_diff.csv', index=False, encoding='utf-8-sig')
    calibration = {'version':'B0-reference-parity-v1', 'year':2025, 'pseudocount':5, 'priors':ns['pri'], 'anchors':ns['anch']}
    (output / 'calibration_reference.json').write_text(json.dumps(calibration, ensure_ascii=False, indent=2), encoding='utf-8')
    summary = {'dates':len(merged), 'joined':merged._merge.value_counts().to_dict(), 'score_max_absolute_difference':float(merged['接力情绪分_diff'].abs().max()), 'grade_mismatches':int((~merged.grade_match).sum()), 'max_module_rounded_difference':{c:float(merged[c+'_diff'].abs().max()) for c in numeric}, 'input_sha256':{p.name:hashlib.file_digest(p.open('rb'),'sha256').hexdigest() for p in snapshot.glob('*.parquet')}}
    (output / 'baseline_validation.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('--snapshot',required=True); p.add_argument('--reference-dir',default=str(Path(__file__).parent/'baseline_reference')); p.add_argument('--output',required=True)
    a=p.parse_args(); run(a.snapshot,a.reference_dir,a.output)
