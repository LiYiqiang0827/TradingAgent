/** Two full chart pages for each episode in the frozen four-board cohort.
 * Run a copy from the private build directory with the bundled node_modules.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {Presentation, PresentationFile, FileBlob} from '@oai/artifact-tool';
import sharp from 'sharp';

const root=process.env.WORKSPACE_DIR || '/Users/nickzhang/TradingAgent';
const skill=process.env.SKILL_DIR;
const build=process.env.REVIEW_BUILD;
const target=process.env.FINAL_PPTX;
if(![skill,build,target,process.env.DECK_DATA].every(p=>p && path.isAbsolute(p)))
  throw new Error('Set absolute SKILL_DIR, REVIEW_BUILD, FINAL_PPTX and DECK_DATA');
const {finalizePresentation,resolvePresentationFont}=await import(pathToFileURL(
  path.join(skill,'container_tools/artifact_tool_utils.mjs')).href);
const font=resolvePresentationFont({fontFamily:'Arial Unicode MS'});
const data=JSON.parse(await fs.readFile(process.env.DECK_DATA,'utf8'));
const {cases,summary}=data;
if(cases.length!==summary.cases || summary.charts!==2*cases.length)
  throw new Error('Source cohort and chart counts disagree');
// The source charts are about 1.6:1. A 4:3 page leaves room for the case header
// while keeping the complete plot large enough for candles and volume labels.
const p=Presentation.create({slideSize:{width:1600,height:1200}});
const manifest=[], tableOwners=[];
const ink='#142735', muted='#516779', blue='#245887', orange='#a84713';
const date=x=>`${x.slice(0,4)}-${x.slice(4,6)}-${x.slice(6,8)}`;

function text(s,value,x,y,w,h,size=25,color=ink,bold=false){
  const b=s.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},
    fill:'none',line:{fill:'none',width:0}});
  b.text=String(value);
  b.text.style={typeface:font,fontSize:size,color,bold,autoFit:'none',wrap:'square',
    verticalAlignment:'middle',insets:{top:0,right:0,bottom:0,left:0}};
  return b;
}
function slide(title,kind,notes=''){
  const s=p.slides.add();s.background.fill='#FFFFFF';
  text(s,title,48,24,1504,64,43,ink,true);
  const page=p.slides.items.length;
  text(s,page,1500,1160,52,25,18,muted);
  s.speakerNotes.textFrame.setText(notes);
  manifest.push({page,title,kind});
  return s;
}
function table(s,values,widths,y=155,rowHeight=85,fontSize=28){
  const t=s.tables.add({rows:values.length,columns:widths.length,left:48,top:y,
    width:1504,height:values.length*rowHeight,columnWidths:widths,values});
  t.borders.assign({style:'solid',fill:'#d8e2e8',width:.6});
  for(let r=0;r<values.length;r++){
    t.rows[r].height=rowHeight;
    for(let c=0;c<widths.length;c++){
      const cell=t.getCell(r,c);
      cell.fill=r===0?'#e9f1f6':(r%2?'#FFFFFF':'#f5f8fa');
      cell.text.style={typeface:font,fontSize,bold:r===0,color:ink,alignment:'left',
        verticalAlignment:'middle',autoFit:'none',wrap:'square',
        insets:{top:5,bottom:5,left:7,right:7}};
    }
  }
  tableOwners.push(p.slides.items.length);
  return t;
}
async function chart(s,filename){
  // Full resolution and aspect ratio preserved. Source JPGs remain unchanged.
  const bytes=await sharp(await fs.readFile(filename)).jpeg({quality:82,chromaSubsampling:'4:4:4'}).toBuffer();
  manifest.at(-1).embeddedImageSha256=createHash('sha256').update(bytes).digest('hex');
  s.images.add({blob:bytes,contentType:'image/jpeg',fit:'contain',alt:path.basename(filename),
    position:{left:20,top:166,width:1560,height:956}});
}

{
  const s=slide('2026年四板案例图册','cover',`Frozen source: ${summary.source}\nSHA256: ${summary.source_sha256}`);
  text(s,`${cases.length}次四板机会`,64,245,1440,85,61,blue,true);
  text(s,'每例一张日线，一张15分钟线',64,404,1440,70,41);
  text(s,`四板日期 ${date(summary.d0_first)} 至 ${date(summary.d0_last)}   共${summary.stocks}只股票`,64,577,1440,62,31);
  text(s,'日线前1年、后1个月。15分钟前1个月、后2周。',64,752,1440,76,31);
  text(s,'按四板日期排序，点击目录页码可跳转到案例。',64,984,1440,55,28,muted);
  text(s,'黄色区域标出四板当天，价格图和成交量图同步标记。',64,1082,1440,47,25,orange);
}
{
  const s=slide('日期窗口与标记','method',`Data providers: coreClient.data_provider.get_day and get_fifteenMin.\n${JSON.stringify(summary,null,2)}`);
  const rows=[['图表项目','展示方式'],
    ['日线窗口','四板当天往前12个自然月，往后1个自然月。'],
    ['15分钟窗口','四板当天往前1个自然月，往后14个自然日。'],
    ['日线标记','黄色窄带和橙色竖线标出四板当天的日K线。'],
    ['15分钟标记','黄色带覆盖四板日的全部16根K线，从09:30开盘至15:00收盘。'],
    ['均线与成交量','两张图均保留真实成交量与MA5/10/20/30/60/120/250。15分钟MA按根数计算。'],
    ['近期案例',`日线目前截至${date(summary.latest_display_data.daily)}，15分钟截至${date(summary.latest_display_data.minute)}。未满窗口的案例按实际行情绘制。`]];
  table(s,rows,[265,1239],151,110,29);
  text(s,'15分钟首根K线的时间标签为09:45，代表09:30至09:45这一段交易。',48,992,1504,45,28,muted);
  text(s,`尚未满后续窗口：日线${summary.future_window_pending.daily}例，15分钟${summary.future_window_pending.minute}例。`,48,1087,1504,32,26,muted);
}

const chunkSize=10,indexCount=Math.ceil(cases.length/chunkSize);
const chartStart=p.slides.items.length+indexCount+1;
for(let offset=0;offset<cases.length;offset+=chunkSize){
  const chunk=cases.slice(offset,offset+chunkSize);
  const s=slide(`案例目录 ${Math.floor(offset/chunkSize)+1}/${indexCount}`,'index',
    `Source: ${data.source}/四板175次_图表索引.csv\n${JSON.stringify(chunk,null,2)}`);
  text(s,`四板日期 ${date(chunk[0].d0)} 至 ${date(chunk.at(-1).d0)}，每例连续两页`,48,98,1504,38,26,muted);
  const values=[['序号','股票代码','股票名称','四板日期','四板当日题材','日线图页']];
  for(const r of chunk)values.push([r.case_number,r.ts_code,r.name,date(r.d0),r.d0_theme_raw,
    chartStart+2*(r.case_number-1)]);
  table(s,values,[90,190,210,195,674,145],155,85,28);
  text(s,'点击最后一列页码查看日线，下一页即为15分钟线。案例页右下角可返回本目录。',48,1136,1504,31,23,muted);
}

for(const r of cases){
  for(const [kind,label,windowText] of [['daily','日线','前1年、后1个月'],['minute','15分钟线','前1个月、后2周']]){
    const info=r[kind];
    const s=slide(`${String(r.case_number).padStart(3,'0')}   ${r.name} ${r.ts_code}   ${label}`,'case',
      `Source episode: ${JSON.stringify(r,null,2)}\nRetrospective price chart, no entry/exit recommendation.`);
    text(s,`四板 ${date(r.d0)}   ${windowText}   题材 ${r.d0_theme_raw}`,48,92,1504,43,26,orange);
    text(s,`实际行情 ${date(info.actual_start)} 至 ${date(info.actual_end)}   ${info.bars}根K线`+
      (info.pending_future_window?'   后续窗口尚未满':''),48,132,1504,30,23,muted);
    await chart(s,info.image);
    text(s,kind==='daily'?'黄色窄带和橙色线标记四板当天':'黄色带标记四板日完整交易区间，09:30至15:00',48,1131,1250,28,23,muted);
    text(s,'返回总表',1370,1131,112,28,19,blue);
    manifest.at(-1).caseNumber=r.case_number;
    manifest.at(-1).view=kind;
    manifest.at(-1).image=info.image;
  }
  if(r.case_number%10===0)console.log(`已编排 ${r.case_number}/${cases.length} 个案例`);
}

await fs.mkdir(build,{recursive:true});await fs.mkdir(path.dirname(target),{recursive:true});
const draft=path.join(build,'candidate.pptx');
console.log(`导出 ${manifest.length} 页`);
await (await PresentationFile.exportPptx(p)).save(draft);
const manifestPath=path.join(build,'slide_manifest.json');
await fs.writeFile(manifestPath,JSON.stringify(manifest,null,2));
const linked=path.join(build,'candidate-linked.pptx');
execFileSync(process.env.RUNTIME_PYTHON,[path.join(root,'plot/link_ma30_review_index.py'),
  draft,linked,manifestPath],{stdio:'inherit'});
const result=await finalizePresentation({workspaceDir:root,candidatePath:linked,finalPath:target,
  explicitTotalSlideCount:manifest.length,requiredNativeTableOwnerSlides:tableOwners,
  pythonExecutable:process.env.RUNTIME_PYTHON,
  integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),
  layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),
  layoutArgs:['--expected-slide-size-emu','15240000,11430000','--validate-heading-fit',
    ...tableOwners.flatMap(n=>['--require-native-table-slide',String(n)])],
  fontPolicy:{basis:'design',families:[font]},verifyArtifactToolImport:true,
  receiptPath:path.join(build,'validation.json')});
console.log(JSON.stringify(result,null,2));
const renders=path.join(build,'renders');await fs.mkdir(renders,{recursive:true});
const final=await PresentationFile.importPptx(await FileBlob.load(target));
for(let i=0;i<final.slides.items.length;i++){
  const preview=await final.export({slide:final.slides.items[i],format:'png',scale:1});
  await fs.writeFile(path.join(renders,`slide-${i+1}.png`),new Uint8Array(await preview.arrayBuffer()));
  if((i+1)%30===0)console.log(`已渲染 ${i+1}/${manifest.length} 页`);
}
console.log(`完成 ${target}`);
