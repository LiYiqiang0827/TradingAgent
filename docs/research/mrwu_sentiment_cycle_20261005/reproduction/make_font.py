"""Build a small OFL font subset; this is not required for ordinary PDF rebuilds."""
from pathlib import Path
import argparse, json
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools import subset

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--text-roots',type=Path,nargs='+',required=True);a=p.parse_args()
    chars=set(chr(i) for i in range(32,127))
    for root in a.text_roots:
        paths=root.rglob('*') if root.is_dir() else [root]
        for path in paths:
            if path.suffix in {'.py','.md','.csv','.json'}:
                chars.update(path.read_text(encoding='utf-8-sig'))
    font=TTFont(a.input)
    if 'fvar' in font:font=instantiateVariableFont(font,{'wght':400},inplace=True)
    opts=subset.Options();opts.name_IDs=['*'];opts.name_legacy=True;opts.name_languages=['*'];s=subset.Subsetter(options=opts);s.populate(text=''.join(chars));s.subset(font)
    for record in font['name'].names:
        if record.nameID in [1,2,3,4,6]:
            value='Regular' if record.nameID==2 else 'RocketSansSC'
            record.string=value.encode(record.getEncoding(),errors='replace')
    a.output.parent.mkdir(parents=True,exist_ok=True);font.save(a.output)
    print(json.dumps({'font':str(a.output),'bytes':a.output.stat().st_size,'requested_characters':len(chars),'license':'SIL OFL 1.1; see fonts/OFL.txt'}))
if __name__=='__main__':main()
