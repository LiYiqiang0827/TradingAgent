import pandas as pd, numpy as np
f=pd.read_pickle('/tmp/sent/scored.pkl').copy()
f['d']=pd.to_datetime(f.date).dt.strftime('%Y-%m-%d')
f['r12']=f.k_h1/f.n_h1
kk=f.k_h2+f.k_h3+f.k_h4; nn=f.n_h2+f.n_h3+f.n_h4; f['rlb']=kk/nn
f['midfail_raw']=f.k_mid/f.n_mid
f['quality']=np.where(f.date=='20260911','降级：9/10行情缺失，昨日对比不可靠','正常')
def line(r):
    p=lambda x: '—' if pd.isna(x) else f'{x*100:.0f}%'
    s=f"情绪 {r.score:.0f} {r.grade} {r.dir}｜涨停 {r.U}、炸板 {r.Z}、跌停 {r.D}｜1进2 {p(r.r12)}、连板晋级 {p(r.rlb)}｜最高 {r.max_h} 板｜昨日涨停今日均 {r.P_close*100:+.1f}%｜中位大面率 {p(r.midfail_raw)}｜成交 {r.amount/1e12:.2f} 万亿（较20日均 {r.ratio20:.2f} 倍）"
    if r.panic: s+='｜恐慌放量'
    return s
f['summary']=f.apply(line,axis=1)
out=pd.DataFrame({'日期':f.d,'接力情绪分':f.score.round(1),'档位':f.grade.astype(str),'方向':f.dir,
 'M1连板接力':f.M1.round(0),'M2强势股反馈':f.M2.round(0),'M3涨跌停结构':f.M3.round(0),'M4中位股生态':f.M4.round(0),'M5流动性':f.M5.round(0),
 '涨停数':f.U,'炸板数':f.Z,'跌停数':f.D,'连板数':f.lianban,'最高板':f.max_h,'1进2':f.r12.round(3),'连板晋级率':f.rlb.round(3),
 '昨日涨停今日开盘均值':f.P_open.round(4),'昨日涨停今日收盘均值':f.P_close.round(4),'中位大面率':f.midfail_raw.round(3),
 '两市成交额_万亿':(f.amount/1e12).round(3),'成交额较20日均':f.ratio20.round(3),'恐慌放量':f.panic,'合格股票数':f.N,'数据质量':f.quality,'一行摘要':f.summary})
out.to_csv('/tmp/sent/out/接力情绪日序列_2025-01-02_2026-09-17.csv',index=False,encoding='utf-8-sig')
print(len(out))
