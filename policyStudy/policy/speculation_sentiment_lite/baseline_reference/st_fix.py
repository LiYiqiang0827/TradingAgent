# 2026-07-06 起主板 ST 涨跌幅由 5% 改为 10%，限价比例不再能识别 ST。
# 7/6 之后：当天开盘啦名单里有该股则以其当日名称判断，否则用证券主表当前名称。
import pandas as pd, numpy as np
m=pd.read_pickle('/tmp/sent/m.pkl')
b=pd.read_csv('/tmp/data/tbl_cn_basic.tsv',sep='\t',dtype=str)
cur=set(b[b.name.str.contains('ST',na=False)].ts_code)
k=pd.read_csv('/tmp/data/tbl_cn_kpl_list.tsv',sep='\t',dtype=str)[['ts_code','trade_date','name']].drop_duplicates(['ts_code','trade_date'])
k['kst']=k.name.str.contains('ST',na=False)
m=m.drop(columns=[c for c in ['kst'] if c in m.columns]).merge(k[['ts_code','trade_date','kst']],on=['ts_code','trade_date'],how='left')
post=m.trade_date>='20260706'
m['st_flag']=np.where(post, np.where(m.kst.notna(), m.kst.fillna(False).astype(bool), m.ts_code.isin(cur)), m.regime.eq('st5'))
m['st_flag']=m.st_flag.astype(bool)|m.regime.eq('st5')
m['elig']=m.regime.isin(['n10','n20'])&~m.st_flag&(m.age>20)
m=m.sort_values(['ts_code','trade_date']).reset_index(drop=True)
m['prev_elig']=m.groupby('ts_code').elig.shift(1).fillna(False).astype(bool)
m['regime_cls']=np.where(m.st_flag,'st',m.regime)
m.to_pickle('/tmp/sent/m.pkl')
