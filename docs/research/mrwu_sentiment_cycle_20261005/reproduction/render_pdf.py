"""Render every page and make contact sheets for human verification."""
from pathlib import Path
import argparse,json
import pymupdf
from PIL import Image,ImageOps,ImageDraw

def main():
    p=argparse.ArgumentParser();p.add_argument('pdf',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    records=[];images=[]
    with pymupdf.open(a.pdf) as d:
        for i,page in enumerate(d):
            path=a.output/f'page_{i+1:02}.png';page.get_pixmap(matrix=pymupdf.Matrix(1,1)).save(path);im=Image.open(path).convert('RGB');im.thumbnail((640,360));images.append(im.copy())
            blocks=page.get_text('blocks');outside=[list(b[:4])+[str(b[4])[:60]] for b in blocks if b[0]<-1 or b[1]<-1 or b[2]>page.rect.width+1 or b[3]>page.rect.height+1]
            records.append({'page':i+1,'text_characters':len(page.get_text()),'out_of_page_text':outside,'rendered':str(path.name)})
    for start in range(0,len(images),6):
        sheet=Image.new('RGB',(1280,1140),'#D0D5DD');dr=ImageDraw.Draw(sheet)
        for j,im in enumerate(images[start:start+6]):x=(j%2)*640;y=(j//2)*380;sheet.paste(im,(x,y));dr.text((x+8,y+361),f'Page {start+j+1}',fill='black')
        sheet.save(a.output/f'contact_{start+1:02}_{min(start+6,len(images)):02}.png')
    result={'pdf_pages':len(records),'pages':records,'outside_page_text_count':sum(len(r['out_of_page_text']) for r in records),'note':'Automated bounds are supplemental; root must inspect all pages visually.'}
    (a.output/'render_checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'pages':len(records),'outside_page_text_count':result['outside_page_text_count']}))
if __name__=='__main__':main()
