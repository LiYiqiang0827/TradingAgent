import pandas as pd, numpy as np
f=pd.read_pickle('/tmp/sent/daily_raw.pkl')
f['yr']=f.date.str[:4]
cal=f[f.yr=='2025']
A=5.0
# 2025 priors (pooled k/n)
pri={}
for g in ['h1','h2','h3','h4']: pri[g]=cal['k_'+g].sum()/cal['n_'+g].sum()
for g in ['b1','b2','b3']: pri[g]=cal['kz_'+g].sum()/cal['nz_'+g].sum()
pri['mid']=cal.k_mid.sum()/cal.n_mid.sum()
sm=lambda k,n,p:(k+A*p)/(n+A)
W1={'h1':.25,'h2':.30,'h3':.25,'h4':.20}
def m1raw(r):
    num=den=0
    for g,w in W1.items():
        if r['n_'+g]>0: num+=w*sm(r['k_'+g],r['n_'+g],pri[g]); den+=w
    return num/den if den else np.nan
f['M1raw']=f.apply(m1raw,axis=1)
WB={'b1':.2,'b2':.3,'b3':.5}
def sq(r):
    num=den=0
    for g,w in WB.items():
        if r['nz_'+g]>0: num+=w*sm(r['kz_'+g],r['nz_'+g],pri[g]); den+=w
    return 1-num/den if den else np.nan
f['SQ']=f.apply(sq,axis=1)
f['MIDfail']=[sm(k,n,pri['mid']) if n>0 else np.nan for k,n in zip(f.k_mid,f.n_mid)]
cal=f[f.yr=='2025']
anch={}
for col in ['M1raw','P_open','P_close','C_open','C_close','BL','SQ','Ddens','MIDfail','lr20']:
    anch[col]=(cal[col].quantile(.1),cal[col].quantile(.9))
def nz(col,neg=False):
    lo,hi=anch[col]; s=((f[col]-lo)/(hi-lo)*100).clip(0,100)
    return 100-s if neg else s
f['M1']=nz('M1raw')
P=0.4*nz('P_open')+0.6*nz('P_close'); C=0.4*nz('C_open')+0.6*nz('C_close')
def comb(parts):
    vals=np.vstack([p.values for p,_ in parts]); ws=np.array([w for _,w in parts])[:,None]
    ok=~np.isnan(vals); return pd.Series((np.nansum(vals*ws,axis=0))/np.where(ok,ws,0).sum(axis=0),index=f.index)
f['M2']=comb([(P,.4),(C,.35),(f.H_score,.25)])
f['M3']=comb([(nz('BL'),.4),(nz('SQ'),.35),(nz('Ddens',True),.25)])
f['M4']=nz('MIDfail',True)
q90=cal.Ddens.quantile(.9)
f['panic']=(f.ratio20>1)&(f.median_ret<0)&(((f.Ddens>0)&(f.Ddens>=q90))|(f.H_h2l>=1))
f['M5']=np.where(f.panic,np.minimum(nz('lr20'),50),nz('lr20'))
f['score']=(25*f.M1+20*f.M2+20*f.M3+15*f.M4+10*f.M5)/90
f['grade']=pd.cut(f.score,[-1,35,50,65,80,101],right=False,labels=['D','C','B','A','S'])
f['dir']=np.select([f.score-f.score.shift(1)>=2,f.score-f.score.shift(1)<=-2],['↑','↓'],'→')
f=f[f.date>='20250102'].copy()
f.to_pickle('/tmp/sent/scored.pkl')
print('priors',{k:round(v,3) for k,v in pri.items()})
print('anchors',{k:(round(a,4),round(b,4)) for k,(a,b) in anch.items()})
print(f.groupby('yr').grade.value_counts().unstack().reindex(columns=['S','A','B','C','D']))
print(f.score.describe().round(1).to_dict())
print('missing', f[['M1','M2','M3','M4','M5','score']].isna().sum().to_dict())
