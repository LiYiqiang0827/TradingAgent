"""Add internal slide jumps to native chart-index cells and return links.

This only adds OOXML relationships to the artifact-tool-authored draft. It does
not modify chart pixels, table values, geometry or fonts. Finalize after linking.
"""
from __future__ import annotations
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"a": A}
ET.register_namespace("a", A)
ET.register_namespace("r", R)


def link(source: Path, target: Path, manifest_path: Path) -> int:
    if target.exists():
        raise ValueError("链接后的新文件不能已存在")
    manifest = json.loads(manifest_path.read_text())
    indexes = [m for m in manifest if m["kind"] == "index"]
    index_pages = [m["page"] for m in indexes]
    changes: dict[str, bytes] = {}
    links = 0
    with zipfile.ZipFile(source) as src:
        for m in manifest:
            page = m["page"]
            if m["kind"] not in ("index", "case"):
                continue
            part = f"ppt/slides/slide{page}.xml"
            relpart = f"ppt/slides/_rels/slide{page}.xml.rels"
            xml = ET.fromstring(src.read(part))
            rels = ET.fromstring(src.read(relpart))
            counter = 0

            def add(run: ET.Element, destination: int) -> None:
                nonlocal counter, links
                counter += 1
                rid = f"rIdMa30Jump{counter}"
                ET.SubElement(rels, f"{{{REL}}}Relationship", {"Id": rid,
                    "Type": f"{R}/slide", "Target": f"slide{destination}.xml"})
                prop = run.find("a:rPr", NS)
                if prop is None:
                    prop = ET.Element(f"{{{A}}}rPr")
                    run.insert(0, prop)
                ET.SubElement(prop, f"{{{A}}}hlinkClick",
                              {f"{{{R}}}id": rid, "action": "ppaction://hlinksldjump"})
                links += 1

            if m["kind"] == "index":
                rows = xml.findall(".//a:tbl/a:tr", NS)
                for row in rows[1:]:
                    cells = row.findall("a:tc", NS)
                    cell = cells[-1]
                    runs = cell.findall(".//a:r", NS)
                    label = "".join(t.text or "" for t in cell.findall(".//a:t", NS))
                    destination = int(label)
                    for run in runs:
                        add(run, destination)
            else:
                destination = index_pages[(m["caseNumber"] - 1) // 10]
                for run in xml.findall(".//a:r", NS):
                    if run.findtext("a:t", default="", namespaces=NS) == "返回总表":
                        add(run, destination)
            if counter:
                changes[part] = ET.tostring(xml, encoding="utf-8", xml_declaration=True)
                changes[relpart] = ET.tostring(rels, encoding="utf-8", xml_declaration=True)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                dst.writestr(item, changes.get(item.filename, src.read(item.filename)))
    print(f"已加入 {links} 个内部跳转链接")
    return links


if __name__ == "__main__":
    link(*(Path(arg) for arg in sys.argv[1:4]))
