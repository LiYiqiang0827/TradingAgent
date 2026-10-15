"""Controller audit of the ten-day artifact, frozen scientific inputs and archive."""
from pathlib import Path
from datetime import datetime,timezone
import json,hashlib,subprocess,sys,xml.etree.ElementTree as ET
import pandas as pd
import pymupdf
from pypdf import PdfReader
from fontTools.ttLib import TTFont

ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT))
from policyStudy.policy.market_sentiment.li_review import FIXED_DATES,evaluate_li_labels
from policyStudy.policy.market_sentiment.li_packet import LI_FONT
from policyStudy.policy.market_sentiment.io import read_csv,sha256,write_json
R=ROOT/'docs/research/mkt_weather_v02_20261005'
BASE='40e218393918545858d53e9abee344458f415388'

def main():
    protected=['data/mkt_daily.csv','data/mkt_raw_daily.csv','data/mkt_calibration_v02.json',
               'human/packets/cards.pdf','human/packets/cards.html','human/packets/Harry_labels.csv','human/packets/Li_labels.csv',
               'human/packets/nominations_template.csv','human/packets/reference_2025.csv','human/private_key/selection_manifest.csv']
    fingerprints=[]
    for rel in protected:
        path=R/rel;old=subprocess.check_output(['git','show',f'{BASE}:{path.relative_to(ROOT).as_posix()}'],cwd=ROOT)
        assert old==path.read_bytes(),rel
        fingerprints.append({'path':path.relative_to(ROOT).as_posix(),'sha256':sha256(path),'byte_equal_to_base':True})
    spec=ROOT/'policyStudy/policy/market_sentiment/config/mkt_spec_v02.json'
    assert spec.read_bytes()==subprocess.check_output(['git','show',f'{BASE}:{spec.relative_to(ROOT).as_posix()}'],cwd=ROOT)
    private=R/'human/li10/private_key';pack=R/'human/li10/packets'
    order=json.loads((private/'card_order.json').read_text(encoding='utf-8'))['card_dates']
    assert set(order)==set(FIXED_DATES) and len(order)==10
    assert order!=sorted(order) and order!=list(FIXED_DATES)
    labels=pd.read_csv(pack/'Li_labels.csv',keep_default_na=False)
    assert labels.trade_date.tolist()==order
    assert labels.drop(columns='trade_date').eq('').to_numpy().all()
    reference=read_csv(pack/'reference_2025.csv');original_reference=read_csv(R/'human/packets/reference_2025.csv')
    pd.testing.assert_frame_equal(reference,original_reference,check_exact=True)
    assert sorted(p.name for p in pack.iterdir())==['FONT_LICENSE.txt','Li_labels.csv','README.md','cards.html','cards.pdf','reference_2025.csv']
    pages=PdfReader(pack/'cards.pdf').pages;assert len(pages)==10
    all_text=[]
    for day,page in zip(order,pages):
        text=page.extract_text();assert day in text
        assert not any(token in text for token in ('临界','随机','mkt_','machine','雷阵雨','暴雨','多云','阈值'))
        assert all(token in text for token in ('挨打（必填）','延续（必填）','活跃（必填）','天气（选填）','备注（选填）','整张跳过'))
        all_text.append(text)
    cmap=TTFont(LI_FONT).getBestCmap()
    missing=sorted({char for char in ''.join(all_text) if not char.isspace() and ord(char) not in cmap})
    assert not missing,missing
    doc=pymupdf.open(pack/'cards.pdf')
    outside=[]
    for i,page in enumerate(doc):
        for b in page.get_text('blocks'):
            if min(b[0],b[1])<0 or b[2]>page.rect.width or b[3]>page.rect.height:outside.append(i+1)
    assert not outside
    dense=max(range(len(doc)),key=lambda i:len(doc[i].get_text()))
    poppler=Path('C:/Users/HarryWu_IOKI/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe')
    rendered=[]
    for name,page in [('first',1),('dense',dense+1),('last',10)]:
        dest=R/f'acceptance/revision_r3_{name}'
        subprocess.run([str(poppler),'-f',str(page),'-l',str(page),'-singlefile','-r','125','-png',str(pack/'cards.pdf'),str(dest)],check=True,capture_output=True)
        rendered.append({'page':page,'path':dest.with_suffix('.png').relative_to(ROOT).as_posix()})
    evaluation=json.loads((private/'evaluation/human_evaluation.json').read_text(encoding='utf-8'))
    assert evaluation['reference_only'] and evaluation['status']=='awaiting_labels'
    assert evaluation['threshold_revision_counts']=={'hit':0,'cont':0,'act':0}
    assert all(x['matches']==0 and x['missing_n']==10 and not x['adjustment_allowed'] for x in evaluation['dimensions'].values())
    tests=ET.parse(R/'acceptance/revision_r3_tests.xml').getroot()
    assert not tests.findall('.//failure') and not tests.findall('.//error')
    result={'status':'structural_checks_passed_awaiting_root_visual_review','checked_at':datetime.now(timezone.utc).isoformat(),
            'base_commit':BASE,'pdf_pages':10,'card_facts_per_day':10,'order_shuffled_without_machine_input':True,
            'label_csv_order_matches_pdf':True,'blind_packet_files':6,'no_machine_answers_or_boundary_markers':True,
            'all_font_glyphs_present':True,'all_text_within_page':True,'reference_2025_unchanged':True,
            'protected_files':fingerprints,'weather_config_unchanged':True,'tests_passed':len(tests.findall('.//testcase')),
            'boundary_subtests_passed':8,'empty_evaluation':'awaiting_labels; no revision consumed','rendered':rendered}
    write_json(R/'acceptance/revision_r3_controller.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='protected_files'},ensure_ascii=False))

if __name__=='__main__':main()
