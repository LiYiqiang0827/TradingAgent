import pandas as pd, numpy as np
m=pd.read_pickle('/tmp/sent/m.pkl')
dates=sorted(m.trade_date.unique())
A=5.0
rows=[]
for t in dates[1:]:
    x=m[m.trade_date==t]
    e=x[x.elig]
    N=len(e)
    U=e[e.U]; touch=e[e['Touch']]
    r={'date':t,'N':N,'U':len(U),'Z':int(e.Z.sum()),'D':int(e.Dn.sum()),'Touch':len(touch),
       'lianban':int((U.height>=2).sum()),'max_h':int(U.height.max()) if len(U) else 0}
    # ladder coverage
    bins=set(min(h,6) for h in U.height); r['ladder']=len(bins)/6
    # yesterday cohorts (eligible yesterday, trading today)
    y=x[x.prev_elig&(x.prev_height>=1)]
    for name,mask in [('h1',y.prev_height==1),('h2',y.prev_height==2),('h3',y.prev_height==3),('h4',y.prev_height>=4)]:
        g=y[mask]; r['k_'+name]=int((g.U&(g.height==g.prev_height+1)).sum()); r['n_'+name]=len(g)
    yl=y  # all yesterday limit-ups
    r['P_open']=yl.oret.mean(); r['P_close']=yl.ret.mean(); r['P_n']=len(yl)
    yc=y[y.prev_height>=2]; r['C_open']=yc.oret.mean() if len(yc) else np.nan; r['C_close']=yc.ret.mean() if len(yc) else np.nan; r['C_n']=len(yc)
    # high cohort: top3 distinct yesterday heights among >=2
    hs=sorted(set(yc.prev_height),reverse=True)[:3]; hc=yc[yc.prev_height.isin(hs)]
    def oc(s):
        if s.H2L: return 0
        if s.Dn: return 0
        if s.U and s.height==s.prev_height+1: return 100
        if s.ret<=-0.05: return 15
        if s.ret>0: return 75
        if s.ret==0: return 50
        return 35
    r['H_score']=np.mean([oc(s) for s in hc.itertuples()]) if len(hc) else np.nan; r['H_n']=len(hc)
    r['H_h2l']=int(hc.H2L.sum()) if len(hc) else 0
    # break by attempt height among touched: attempt = prev_height+1 if prev eligible limit-up else 1
    att=np.where(touch.prev_elig&(touch.prev_height>=1),touch.prev_height+1,1)
    for name,mask in [('b1',att==1),('b2',att==2),('b3',att>=3)]:
        g=touch[mask]; r['kz_'+name]=int(g.Z.sum()); r['nz_'+name]=len(g)
    mid=x[x.prev_elig&x.prev_height.between(2,4)]
    r['k_mid']=int(((mid.ret<=-0.05)|mid.Dn).sum()); r['n_mid']=len(mid)
    r['median_ret']=e.ret.median()
    r['amount']=x.amount.sum()*1000  # all SH/SZ A incl ST/new, CNY
    # 财联社口径 (non-ST, any age) for reconciliation
    ns=x[x.regime_cls.isin(['n10','n20'])]
    r['cls_U']=int(ns.U.sum()); r['cls_Z']=int(ns.Z.sum()); r['cls_lianban']=int((ns.U&(ns.height>=2)).sum())
    yns=ns[(ns.prev_height>=1)]
    g1=yns[yns.prev_height==1]; g2=yns[yns.prev_height>=2]
    r['cls_1to2']=(g1.U&(g1.height==2)).mean() if len(g1) else np.nan
    r['cls_lb_promo']=(g2.U&(g2.height==g2.prev_height+1)).mean() if len(g2) else np.nan
    r['cls_D']=int(ns.Dn.sum())
    rows.append(r)
f=pd.DataFrame(rows)
f['amt_ma20']=f.amount.shift(1).rolling(20).mean(); f['ratio20']=f.amount/f.amt_ma20; f['lr20']=np.log(f.ratio20)
f['Udens']=f.U/f.N*1000; f['Ddens']=f.D/f.N*1000; f['BL']=f.Udens*f.ladder
f.to_pickle('/tmp/sent/daily_raw.pkl'); print(len(f), f.date.min(), f.date.max())
