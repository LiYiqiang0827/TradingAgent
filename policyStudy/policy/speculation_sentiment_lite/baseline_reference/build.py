import pandas as pd, numpy as np
D='/tmp/data/'
d=pd.read_csv(D+'tbl_cn_day.tsv',sep='\t',dtype={'ts_code':str,'trade_date':str})
l=pd.read_csv(D+'tbl_cn_stk_limit.tsv',sep='\t',dtype={'ts_code':str,'trade_date':str})
b=pd.read_csv(D+'tbl_cn_basic.tsv',sep='\t',dtype=str)
cal=pd.read_csv(D+'tbl_cn_tradecal.tsv',sep='\t',dtype=str)
m=d.merge(l[['ts_code','trade_date','up_limit','down_limit']],on=['ts_code','trade_date'],how='left')
m=m[m.ts_code.str[-2:].isin(['SH','SZ'])&m.ts_code.str[:2].isin(['60','00','30','68'])].copy()
m['board']=np.where(m.ts_code.str[:2].isin(['30','68']),'20pct','main')
c=lambda x:(np.floor(x*100+0.5+1e-9)).astype('int64')
m['cU']=c(m.up_limit); m['cD']=c(m.down_limit); m['cC']=c(m.close); m['cH']=c(m.high); m['cO']=c(m.open); m['cP']=c(m.pre_close)
r=(m.up_limit/m.pre_close-1)
m['regime']=np.select([(m.board=='main')&(r.between(0.04,0.065)),(m.board=='main')&(r.between(0.085,0.125)),(m.board=='20pct')&(r.between(0.18,0.22))],['st5','n10','n20'],'other')
nom=m.regime.map({'st5':0.05,'n10':0.10,'n20':0.20})
nn=nom.fillna(0)
m['ref_ok']=nom.notna()&(c(m.pre_close*(1+nn))==m.cU)&(c(m.pre_close*(1-nn))==m.cD)
# new listings: first 20 trading days since list_date
dates=sorted(m.trade_date.unique()); di={x:i for i,x in enumerate(dates)}
ld=b.set_index('ts_code').list_date.dropna().to_dict()
opencal=sorted(cal[(cal.exchange=='SSE')&(cal.is_open=='1')].cal_date)
oi={x:i for i,x in enumerate(opencal)}
import bisect
def nth(ts,td):
    L=ld.get(ts)
    if L is None or L<opencal[0]: return 10**6
    a=bisect.bisect_left(opencal,L); return oi[td]-a+1 if td in oi else 10**6
m['age']=[nth(a,t) for a,t in zip(m.ts_code,m.trade_date)]
m['elig']=(m.regime.isin(['n10','n20']))&(m.age>20)
m['U']=m.cC==m.cU; m['Touch']=m.cH==m.cU; m['Z']=m['Touch']&~m['U']; m['Dn']=m.cC==m.cD; m['H2L']=m['Touch']&m['Dn']
m['ret']=m.close/m.pre_close-1; m['oret']=m.open/m.pre_close-1
m=m.sort_values(['ts_code','trade_date']).reset_index(drop=True)
# consecutive board height over stock's own trading rows
h=np.zeros(len(m),dtype=int); prevcode=None; run=0
for i,(code,u) in enumerate(zip(m.ts_code.values,m.U.values)):
    if code!=prevcode: run=0; prevcode=code
    run=run+1 if u else 0; h[i]=run
m['height']=h
m['prev_height']=m.groupby('ts_code').height.shift(1).fillna(0).astype(int)
m['prev_elig']=m.groupby('ts_code').elig.shift(1).fillna(False).astype(bool)
m.to_pickle('/tmp/sent/m.pkl')
print('rows',len(m)); print(m.regime.value_counts().to_dict())
x=m[m.regime!='other']; print('ref consistency', round(x.ref_ok.mean()*100,3),'%', 'mismatch',(~x.ref_ok).sum())
print(x[~x.ref_ok].groupby(x.trade_date.str[:4]).size().to_dict())
