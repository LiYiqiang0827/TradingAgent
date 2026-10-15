"""Verify cohort coverage, D0 markers and the actual images in the final PPTX."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

import pandas as pd
import numpy as np
from PIL import Image

NS={"p":"http://schemas.openxmlformats.org/presentationml/2006/main",
    "a":"http://schemas.openxmlformats.org/drawingml/2006/main",
    "r":"http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
REL="http://schemas.openxmlformats.org/package/2006/relationships"


def verify(output: Path, pptx: Path) -> dict:
    payload=json.loads((output/"deck_data.json").read_text())
    summary,records=payload["summary"],payload["cases"]
    source=Path(summary["source"])
    assert hashlib.sha256(source.read_bytes()).hexdigest()==summary["source_sha256"]
    cohort=pd.read_csv(source,dtype=str)
    assert set(zip(cohort.ts_code,cohort.d0))=={(r["ts_code"],r["d0"]) for r in records}
    assert len(records)==len(cohort)==175
    source_images=[]
    for r in records:
        day=pd.Timestamp(r["d0"])
        for kind,left,right,n in (("daily",day-pd.DateOffset(years=1),day+pd.DateOffset(months=1),1),
                                  ("minute",day-pd.DateOffset(months=1),day+pd.Timedelta(days=14),16)):
            info=r[kind]
            assert info["requested_start"]==left.strftime("%Y%m%d")
            assert info["requested_end"]==right.strftime("%Y%m%d")
            assert info["anchor_bars"]==n
            assert info["requested_start"]<=info["actual_start"]<=r["d0"]
            assert r["d0"]<=info["actual_end"]<=info["requested_end"]
            image=Path(info["image"])
            assert hashlib.sha256(image.read_bytes()).hexdigest()==info["image_sha256"]
            with Image.open(image) as im:
                assert im.format=="JPEG" and im.width>=1600 and im.height>=900
            source_images.append(str(image))
        assert r["minute"]["anchor_first"].endswith("T09:45:00")
        assert r["minute"]["anchor_last"].endswith("T15:00:00")
        assert not r["minute"]["incomplete_days"]
        assert not r["minute"]["missing_daily_trading_dates"]
    assert len(set(source_images))==350
    build=output/".ppt_build"
    slides=json.loads((build/"slide_manifest.json").read_text())
    chart_slides=[s for s in slides if s["kind"]=="case"]
    assert len(slides)==370 and len(chart_slides)==350
    assert [s["image"] for s in chart_slides]==source_images
    for i,r in enumerate(records):
        a,b=chart_slides[2*i:2*i+2]
        assert a["caseNumber"]==b["caseNumber"]==r["case_number"]
        assert (a["view"],b["view"])==("daily","minute")
    links=0
    table_rows=0
    rendered=0
    with zipfile.ZipFile(pptx) as z:
        package_slides=[n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml",n)]
        assert len(package_slides)==370
        for s in slides:
            page=s["page"]
            part=f"ppt/slides/slide{page}.xml"
            xml=ET.fromstring(z.read(part))
            rels={r.attrib["Id"]:r.attrib for r in ET.fromstring(z.read(f"ppt/slides/_rels/slide{page}.xml.rels"))}
            for link in xml.findall('.//a:hlinkClick',NS):
                rel=rels[link.attrib[f"{{{NS['r']}}}id"]]
                assert rel['Type']==NS['r']+'/slide'
                assert link.attrib['action']=='ppaction://hlinksldjump'
                assert 1<=int(re.search(r'slide(\d+)\.xml',rel['Target']).group(1))<=370
                links+=1
            if s['kind']=='index':
                table_rows+=len(xml.findall('.//a:tbl/a:tr',NS))-1
            if s['kind']=='case':
                blips=xml.findall('.//p:pic/p:blipFill/a:blip',NS)
                assert len(blips)==1
                rel=rels[blips[0].attrib[f"{{{NS['r']}}}embed"]]
                media=(rel['Target'].lstrip('/') if rel['Target'].startswith('/') else
                       posixpath.normpath(posixpath.join('ppt/slides',rel['Target'])))
                assert hashlib.sha256(z.read(media)).hexdigest()==s['embeddedImageSha256']
            render=build/'renders'/f'slide-{page}.png'
            with Image.open(render) as im:
                assert im.size==(1600,1200)
                # Full-size raster check, including the price and volume panels.
                if s['kind']=='case':
                    rgb=im.convert('RGB')
                    for box in ((100,240,1500,810),(100,920,1500,1050)):
                        region=np.asarray(rgb.crop(box),dtype=np.uint16)
                        rr,gg,bb=region[:,:,0],region[:,:,1],region[:,:,2]
                        colored=np.count_nonzero(((5*rr>6*gg)&(5*rr>6*bb)&(rr>80)) |
                                                 ((5*gg>6*rr)&(10*gg>11*bb)&(gg>80)))
                        assert colored>10,f'走势图或成交量空白: page {page}'
                rendered+=1
        size=sum(i.file_size for i in z.infolist())
    assert links==525 and table_rows==175 and rendered==370
    validation=json.loads((build/'validation.json').read_text())
    assert validation['packageIntegrity']['findingCount']==0
    assert validation['presentationLayout']['findingCount']==0
    assert validation['presentationLayout']['warning_count']==0
    report={'cases':175,'stocks':summary['stocks'],'source_images':350,'chart_slides':350,
            'total_slides':370,'rendered_slides':rendered,'index_rows':table_rows,'internal_links':links,
            'all_embedded_images_match':True,'all_d0_sessions_complete':True,
            'package_uncompressed_bytes':size,'pptx_bytes':pptx.stat().st_size,
            'future_window_pending':summary['future_window_pending'],
            'latest_display_data':summary['latest_display_data'],
            'source_sha256':summary['source_sha256'],
            'pptx_sha256':hashlib.sha256(pptx.read_bytes()).hexdigest()}
    (build/'content_quality.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=Path('outputs/four_board_chart_review_2026'))
    p.add_argument('--pptx',type=Path,required=True)
    args=p.parse_args()
    print(json.dumps(verify(args.output,args.pptx),ensure_ascii=False,indent=2))
