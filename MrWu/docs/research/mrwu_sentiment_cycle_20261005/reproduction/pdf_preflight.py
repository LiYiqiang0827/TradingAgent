from pathlib import Path
import argparse
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor
import fitz

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--font',type=Path,required=True);p.add_argument('--style-pdf',type=Path);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    pdfmetrics.registerFont(TTFont('Chinese',str(a.font),subfontIndex=0))
    c=Canvas(str(a.output/'preflight.pdf'),pagesize=(1280,720));c.setFillColor(HexColor('#F7F3EA'));c.rect(0,0,1280,720,fill=1,stroke=0)
    c.setFillColor(HexColor('#14213D'));c.setFont('Chinese',38);c.drawString(64,630,'接力情绪与周期：中文与图表导出试验')
    c.setFont('Chinese',24);c.drawString(64,574,'分数、相位和题材结构分别呈现；下图仅为排版测试。')
    for i,h in enumerate([.3,.7,.45,.9,.55]):
        c.setFillColor(HexColor('#2A9D8F' if i%2 else '#D85C41'));c.rect(90+i*200,160,130,h*350,fill=1,stroke=0)
    c.setFillColor(HexColor('#14213D'));c.setFont('Chinese',15);c.drawString(64,54,'描述性研究 | 合成排版数据 N=5 | 正式报告截至 2026-09-30 | 来源：导出试验')
    c.showPage();c.save()
    with fitz.open(a.output/'preflight.pdf') as d:d[0].get_pixmap(matrix=fitz.Matrix(1,1)).save(a.output/'preflight.png')
    if a.style_pdf:
        with fitz.open(a.style_pdf) as d:
            for i in [0,3,7]:d[i].get_pixmap(matrix=fitz.Matrix(1,1)).save(a.output/f'style_{i+1:02}.png')
    print(a.output/'preflight.pdf')
if __name__=='__main__':main()
