/** Review all MA30 candidates in an editable-table, full-chart PowerPoint.
 * Run a copy in a build directory linked to the supplied artifact-tool runtime.
 * Supply DECK_DATA, REVIEW_BUILD, FINAL_PPTX, SKILL_DIR and RUNTIME_PYTHON.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
import { Presentation, PresentationFile } from '@oai/artifact-tool';
import sharp from 'sharp';

const root = process.env.WORKSPACE_DIR || '/Users/nickzhang/TradingAgent';
const skill = process.env.SKILL_DIR;
const build = process.env.REVIEW_BUILD;
const target = process.env.FINAL_PPTX;
if (![skill, build, target, process.env.DECK_DATA].every(p => p && path.isAbsolute(p))) {
  throw new Error('Set absolute SKILL_DIR, REVIEW_BUILD, FINAL_PPTX and DECK_DATA paths');
}
const {finalizePresentation, resolvePresentationFont} = await import(pathToFileURL(
  path.join(skill, 'container_tools/artifact_tool_utils.mjs')).href);
const font = resolvePresentationFont({fontFamily:'Arial Unicode MS'});
const data = JSON.parse(await fs.readFile(process.env.DECK_DATA, 'utf8'));
const cases = data.cases;
const summary = data.summary;
const presentation = Presentation.create({slideSize:{width:1600,height:900}});
const tableOwners=[];
const manifest=[];
const colors={ink:'#142735', muted:'#516779', blue:'#245887', good:'#166534', bad:'#a33127'};
const pct=x => Number.isFinite(x) ? `${x>=0?'+':''}${x.toFixed(2)}%` : '缺数据';
const spread=x => `${Number(x).toFixed(2)}%`;
const date=x => `${x.slice(0,4)}-${x.slice(4,6)}-${x.slice(6,8)}`;

function text(s, value, x, y, w, h, size=25, color=colors.ink, bold=false) {
  const box=s.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},
    fill:'none',line:{fill:'none',width:0}});
  box.text=value;
  box.text.style={typeface:font,fontSize:size,color,bold,autoFit:'none',wrap:'square',
    verticalAlignment:'middle',insets:{top:0,right:0,bottom:0,left:0}};
  return box;
}
function slide(title, notes='', kind='summary') {
  const s=presentation.slides.add(); s.background.fill='#FFFFFF';
  text(s,title,48,24,1504,64,38,colors.ink,true);
  const n=presentation.slides.items.length;
  text(s,`${n}`,1500,860,52,25,18,colors.muted);
  s.speakerNotes.textFrame.setText(notes);
  manifest.push({page:n,title,kind});
  return s;
}
function table(s, values, widths, y=154, rowHeight=52, fontSize=23) {
  const t=s.tables.add({rows:values.length,columns:widths.length,left:48,top:y,
    width:1504,height:values.length*rowHeight,columnWidths:widths,values});
  t.borders.assign({style:'solid',fill:'#d8e2e8',width:.6});
  for(let r=0;r<values.length;r++) {
    t.rows[r].height=rowHeight;
    for(let c=0;c<widths.length;c++) {
      const cell=t.getCell(r,c);
      cell.fill=r===0?'#e9f1f6':(r%2?'#FFFFFF':'#f5f8fa');
      cell.text.style={typeface:font,fontSize:r===0?fontSize:fontSize,bold:r===0,
        color:colors.ink,alignment:'left',verticalAlignment:'middle',autoFit:'none',wrap:'square',
        insets:{top:5,bottom:5,left:7,right:7}};
    }
  }
  tableOwners.push(presentation.slides.items.length);
  return t;
}

// Re-encode JPEG bytes for a bounded deck size without resizing or cropping.
// Keep the original full-resolution files in the source chart directory.
async function image(s, filename, box={left:30,top:151,width:1540,height:694}) {
  const bytes=await sharp(await fs.readFile(filename))
    .jpeg({quality:85,chromaSubsampling:'4:4:4'}).toBuffer();
  s.images.add({blob:bytes,contentType:'image/jpeg',fit:'contain',alt:path.basename(filename),position:box});
}

{
  const s=slide('MA30 回调再启动候选图册',`Source: ${data.source}\n${JSON.stringify(summary,null,2)}`,'cover');
  text(s,'2026 年 5月至8月',64,165,1440,62,48,colors.blue,true);
  text(s,`${cases.length} 个案例，每例含日线筛选图、日线买卖回放图和 15分钟回放图`,64,265,1440,100,35);
  text(s,`新增条件：MA5、MA10、MA20 的最大间距不超过 7%`,64,415,1440,66,32,colors.blue);
  text(s,`原有 90 次候选保留作对照。新条件先筛日期再去重，得到 81 次候选。`,64,510,1440,86,29);
  text(s,'总表为可编辑的 PPT 表格。每行附案例起始页码，所有走势图完整嵌入。',64,642,1440,80,28);
  text(s,'本册供形态复核。收益沿用此前的开盘价模拟，尚未验证15分钟买卖规则。',64,766,1440,54,25,colors.muted);
}
{
  const s=slide('筛选时点与回放口径', 'All screening features stop at signal-day close. Future charts serve review only.');
  const lines=[
    ['均线粘合', '间距 = 100 ×（MA5/10/20 中最大值 ÷ 最小值 − 1），阈值 7%。'],
    ['可见信息', '信号日收盘后计算。买入日收盘的均线不参与次日开盘买入筛选。'],
    ['样本去重', '先从原107个匹配日期筛选，再按同股同类型间隔10交易日去重。'],
    ['买卖模拟', '信号日次一交易日开盘买入，10交易日后开盘卖出。费用合计0.4个百分点。'],
    ['图示范围', '日线回放向前1年、向后1个月。15分钟从买入前1个月到卖出后7自然日。'],
    ['首根K线', '15分钟首根标记时间09:45，其开盘价对应09:30。标记不使用09:45收盘价。'],
    ['题材来源', '沿用首波最后涨停日的开盘啦原始题材标签，含附加标签。'],
    ['图表区别', '筛选图截至信号日。后续回放图展示未来行情，用于复核结果。'],
  ];
  table(s,[['项目','做法'],...lines],[220,1284],134,77,26);
}
{
  const s=slide('加入7%条件后的收益对照',`${JSON.stringify(summary,null,2)}\nNo independent holdout. No newly tested intraday execution.`);
  const a=summary.original_episodes,b=summary.original_episodes_passing_gate,c=summary.compact_reselected_episodes;
  const v=[['分组','次数','平均净收益','中位净收益','胜率','最差净收益'],
    ['原有候选',a.candidates,pct(a.mean_net_pct),pct(a.median_net_pct),spread(a.win_rate_pct),pct(a.worst_net_pct)],
    ['原有候选中通过7%',b.candidates,pct(b.mean_net_pct),pct(b.median_net_pct),spread(b.win_rate_pct),pct(b.worst_net_pct)],
    ['先筛7%后重新去重',c.candidates,pct(c.mean_net_pct),pct(c.median_net_pct),spread(c.win_rate_pct),pct(c.worst_net_pct)]];
  table(s,v,[430,130,240,240,214,250],170,99,28);
  text(s,'间距条件仅减少一部分候选，收益仍有明显的负尾部。',60,645,1480,65,32,colors.bad,true);
  text(s,'5月至8月属于探索样本。选择阈值和规则时已看过图形，不能视作独立验证。',60,752,1480,62,26,colors.muted);
}
{
  const s=slide('重新去重后的81次候选分月结果',JSON.stringify(summary.monthly_compact,null,2));
  const v=[['信号月份','次数','平均净收益','中位净收益','胜率','最差净收益']];
  for(const [month,r] of Object.entries(summary.monthly_compact))v.push([
    `${month.slice(0,4)}-${month.slice(4)}`,r.candidates,pct(r.mean_net_pct),pct(r.median_net_pct),spread(r.win_rate_pct),pct(r.worst_net_pct)]);
  table(s,v,[280,150,274,274,252,274],168,100,29);
  text(s,'6月贡献主要正收益。5月、7月和8月均值为负，仍需逐例核对形态与题材。',60,744,1480,78,28,colors.muted);
}

// The previous aggregate return chart is included as an explicit baseline.
const baseline=path.join(root,'outputs/rising_ma_reactivation/may_aug_2026_final/return_distribution.jpg');
try {
  await fs.access(baseline);
  const s=slide('原始全样本收益分布',`Source: ${baseline}\nOriginal broad MA20/MA30 audit, prior to 7% gate.`,'baseline');
  text(s,'沿用上一轮分布图，供与新条件比较。图中样本口径保持原样。',48,98,1504,38,25,colors.muted);
  await image(s,baseline);
} catch(e) {if(e.code!=='ENOENT')throw e;}

const chunkSize=10;
const indexPages=Math.ceil(cases.length/chunkSize);
const chartStart=presentation.slides.items.length+indexPages+1;
for(let offset=0;offset<cases.length;offset+=chunkSize) {
  const chunk=cases.slice(offset,offset+chunkSize);
  const s=slide(`完整交易总表 ${Math.floor(offset/chunkSize)+1}/${indexPages}`,
    `Source CSV: ${data.source}/全部候选_均线粘合_图表索引.csv\n${JSON.stringify(chunk,null,2)}`,'index');
  text(s,'通过 = 7%条件通过且进入新去重候选。对照 = 保留旧案例，未进入新候选。',48,98,1504,36,24,colors.muted);
  const v=[['序号','代码','名字','模拟买入','模拟卖出','涨跌幅','净收益','间距','类别','题材','图页']];
  for(const r of chunk)v.push([r.case_number,r.ts_code,r.name,date(r.entry_date),date(r.exit_date),
    pct(r.price_change_pct),pct(r.estimated_net_pct),spread(r.ma5_20_spread_pct),
    r.compact_episode?'通过':'对照',r.theme,chartStart+3*(r.case_number-1)]);
  const t=table(s,v,[62,142,110,146,146,118,118,100,86,292,84],155,60,22);
  for(let j=0;j<chunk.length;j++) {
    const r=chunk[j];
    t.getCell(j+1,8).text.style={typeface:font,fontSize:22,
      color:r.compact_episode?colors.good:colors.bad,autoFit:'none',wrap:'square',
      insets:{top:5,bottom:5,left:7,right:7}};
  }
  text(s,'买卖价格按日线复权等价开盘价计算。费用为合计0.4个百分点。筛选日见各案例页。',48,836,1504,31,22,colors.muted);
}

for(const r of cases) {
  const status=r.compact_episode?'新候选通过':(r.compact_pass?'间距通过，去重未选':'间距超过7%');
  const note=`Source row: ${JSON.stringify(r,null,2)}\nThe trade and intraday charts are future replay only.`;
  const views=[['日线筛选',r.chart_path,'截至筛选日，仅使用当时可见信息'],
               ['日线买卖回放',r.trade_chart_path,'后续日线回放，含模拟买卖日期与持有区间'],
               ['15分钟买卖回放',r.minute_chart_path,'前1个月至后1周，MA按15分钟K线根数计算']];
  for(const [label,filename,description] of views) {
    const s=slide(`${String(r.case_number).padStart(2,'0')}  ${r.name} ${r.ts_code}   ${label}`,note,'case');
    text(s,`筛选 ${date(r.signal_date)}   MA5/10/20间距 ${spread(r.ma5_20_spread_pct)}   ${status}`,48,91,1504,33,24,
      r.compact_episode?colors.good:colors.bad);
    const detail=label==='日线筛选'
      ? `MA5 ${r.signal_ma5.toFixed(2)}   MA10 ${r.signal_ma10.toFixed(2)}   MA20 ${r.signal_ma20.toFixed(2)}   题材 ${r.theme}`
      : `买 ${date(r.entry_date)}   卖 ${date(r.exit_date)}   区间 ${pct(r.price_change_pct)}   净 ${pct(r.estimated_net_pct)}   题材 ${r.theme}`;
    text(s,detail,48,123,1504,33,23,colors.muted);
    await image(s,filename,{left:20,top:170,width:1560,height:651});
    text(s,description,48,830,1504,28,21,colors.muted);
    text(s,'返回总表',1370,830,112,28,19,colors.blue);
    s.speakerNotes.textFrame.setText(`${note}\n${description}\nSignal MA5=${r.signal_ma5}, MA10=${r.signal_ma10}, MA20=${r.signal_ma20}`);
    manifest.at(-1).caseNumber=r.case_number;
    manifest.at(-1).image=filename;
  }
  if(r.case_number%10===0)console.log(`已编排 ${r.case_number}/${cases.length} 个案例`);
}

await fs.mkdir(build,{recursive:true});
await fs.mkdir(path.dirname(target),{recursive:true});
const draft=path.join(build,'candidate.pptx');
console.log(`导出 ${presentation.slides.items.length} 页`);
await (await PresentationFile.exportPptx(presentation)).save(draft);
await fs.writeFile(path.join(build,'slide_manifest.json'),JSON.stringify(manifest,null,2));
const candidate=path.join(build,'candidate-linked.pptx');
execFileSync(process.env.RUNTIME_PYTHON,[path.join(root,'plot/link_ma30_review_index.py'),
  draft,candidate,path.join(build,'slide_manifest.json')],{stdio:'inherit'});
const requirements={explicitTotalSlideCount:manifest.length,requiredNativeTableOwnerSlides:tableOwners};
const result=await finalizePresentation({
  ...requirements,workspaceDir:root,candidatePath:candidate,finalPath:target,
  pythonExecutable:process.env.RUNTIME_PYTHON,
  integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),
  layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),
  layoutArgs:['--expected-slide-size-emu','15240000,8572500','--validate-heading-fit',
    ...tableOwners.flatMap(x=>['--require-native-table-slide',String(x)])],
  requiredNativeTableOwnerSlides:tableOwners,
  fontPolicy:{basis:'design',families:[font]},verifyArtifactToolImport:true,
  receiptPath:path.join(build,'validation.json'),
});
console.log(JSON.stringify(result,null,2));
// Render every finalized slide, including native table and backlink additions.
// Package/geometry checks alone do not establish visual quality.
const renderDir=path.join(build,'renders');await fs.mkdir(renderDir,{recursive:true});
const {FileBlob}=await import('@oai/artifact-tool');
const finalized=await PresentationFile.importPptx(await FileBlob.load(target));
for(let i=0;i<finalized.slides.items.length;i++) {
  const preview=await finalized.export({slide:finalized.slides.items[i],format:'png',scale:1});
  await fs.writeFile(path.join(renderDir,`slide-${i+1}.png`),new Uint8Array(await preview.arrayBuffer()));
  if((i+1)%30===0)console.log(`已渲染 ${i+1}/${manifest.length} 页`);
}
console.log(`完成 ${target}`);
