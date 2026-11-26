"""Insert the source-rendered English figures and CSV tables into the numbered Word manuscript.

First run render_npj_heritage_outline_figures.py --numbered-english. Then run this
script with the Chinese numbered DOCX and the desired output DOCX as arguments.
"""

import argparse
import csv
import os
import posixpath
import re
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZipFile

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.shared import Pt
from docx.text.paragraph import Paragraph
from heritage_numbered_tables import BLOCKS, read_block
from heritage_numbered_text import FIGURE_CAPTIONS, NOTES, TABLE_TITLES
from lxml import etree as ET
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "output/heritage-english-illustrations"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
PIC = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
SPACE = "{http://www.w3.org/XML/1998/namespace}space"
OLD_FIGURES = ("1", "S8", "S1", "S2", "S3", "S7", "2", "3", "4", "5", "S4", "S5", "6", "7", "S6")


def text_of(node):
    return "".join(t.text or "" for t in node.iter(W + "t"))


def set_leaf(leaf, value):
    leaf.text = value
    if value != value.strip():
        leaf.set(SPACE, "preserve")


def set_parts(paragraph, parts):
    # Keep run formatting, bookmarks, field instructions and field results.
    leaves = list(paragraph.iter(W + "t"))
    if len(leaves) != len(parts):
        raise ValueError(f"Unexpected run structure in {text_of(paragraph)[:65]}")
    for leaf, value in zip(leaves, parts, strict=True):
        set_leaf(leaf, value)


def put_cell(cell, value):
    (paragraph,) = cell.findall(W + "p")
    runs = paragraph.findall(W + "r")
    # Leave number formatting and empty continuation cells alone when they
    # already match the value freshly read from the CSV.
    if value == text_of(cell) and next(cell.iter(W + "br"), None) is None:
        return
    style = runs[0].find(W + "rPr") if runs else None
    for run in runs:
        paragraph.remove(run)
    run = ET.SubElement(paragraph, W + "r")
    if style is not None:
        run.append(deepcopy(style))
    for i, line in enumerate(value.split("\n")):
        if i:
            ET.SubElement(run, W + "br")
        set_leaf(ET.SubElement(run, W + "t"), line)


def fill_table(table, key):
    original, english = read_block(key)
    rows = table.findall(W + "tr")
    columns = table.find(W + "tblGrid").findall(W + "gridCol")
    weights = BLOCKS[key][1]
    total = sum(int(col.get(W + "w")) for col in columns)
    widths = [round(total * w / sum(weights)) for w in weights]
    widths[-1] += total - sum(widths)
    for col, width in zip(columns, widths, strict=True):
        col.set(W + "w", str(width))
    for side in ("top", "bottom"):
        margin = table.find(f"{W}tblPr/{W}tblCellMar/{W}{side}")
        if margin is not None:
            margin.set(W + "w", "24")
    text_columns = [
        any(re.search(r"[\u3400-\u9fff]", value) for value in column)
        for column in zip(*original[1:], strict=True)
    ]
    for index, (row, before, after) in enumerate(zip(rows, original, english, strict=True)):
        # Allow a multi-page table to break between strata. Keep each continuation
        # row with its group label and the last row with its existing footnote.
        next_is_continuation = index + 1 < len(rows) and not original[index + 1][0]
        for cell, source, translated, width, textual in zip(
            row.findall(W + "tc"), before, after, widths, text_columns, strict=True
        ):
            if "".join(text_of(cell).split()) != "".join(source.split()):
                raise ValueError(
                    f"Word and source CSV differ in table {key}: {source!r}, {text_of(cell)!r}"
                )
            cell.find(W + "tcPr").find(W + "tcW").set(W + "w", str(width))
            put_cell(cell, translated)
            fmt = Paragraph(cell.find(W + "p"), None).paragraph_format
            fmt.line_spacing = Pt(11.25)
            if index < len(rows) - 1:
                fmt.keep_with_next = index == 0 or len(rows) <= 7 or next_is_continuation
            if textual:
                fmt.alignment = WD_ALIGN_PARAGRAPH.LEFT
    return english


def localize_figure(number, drawing, caption, archive, targets, figures):
    title, *detail = FIGURE_CAPTIONS[number]
    parts = [f"Figure\u00a0{number}. {title} ", *detail]
    set_parts(caption, parts)
    fmt = Paragraph(caption, None).paragraph_format
    fmt.keep_with_next = False
    fmt.keep_together = True
    fmt.alignment = WD_ALIGN_PARAGRAPH.LEFT
    fmt.line_spacing = Pt(12.25)
    fmt.space_before = Pt(2.5)
    fmt.space_after = Pt(4)
    (inline,) = drawing.iter(WP + "inline")
    (blip,) = inline.iter(A + "blip")
    target = posixpath.normpath(posixpath.join("word", targets[blip.get(R + "embed")]))
    ext = Path(target).suffix
    published = ROOT / "paper/figures" / f"Fig{OLD_FIGURES[number - 1]}{ext}"
    if archive.read(target) != published.read_bytes():
        raise ValueError(f"Figure {number} differs from its archived source edition")
    file = figures / f"Figure-{number:02d}{ext}"
    with Image.open(file) as img:
        w, h = img.size
    extent = inline.find(WP + "extent")
    scale = min(int(extent.get("cx")) / w, int(extent.get("cy")) / h)
    # Reserve room for the longer English captions of the two full-page plates.
    scale *= {1: 0.96, 2: 0.94}.get(number, 1.0)
    for node in (extent, *inline.iter(A + "ext")):
        node.set("cx", str(round(w * scale)))
        node.set("cy", str(round(h * scale)))
    for node in (*inline.iter(WP + "docPr"), *inline.iter(PIC + "cNvPr")):
        node.set("descr", "".join(parts).strip())
        if "title" in node.attrib:
            node.set("title", title)
    return target, file.read_bytes()


def build(source, output, assets_dir):
    if source.resolve() == output.resolve():
        raise ValueError("Choose a separate output file for the English illustrations")
    styles_file, document_file = "word/styles.xml", "word/document.xml"
    tables = {}
    figures_done, titles_done, notes_done = set(), set(), set()
    changes = {}
    with ZipFile(source) as archive:
        document = parse_xml(archive.read(document_file))
        body = document.find(W + "body")
        styles = {
            s.get(W + "styleId"): s.find(W + "name").get(W + "val")
            for s in ET.fromstring(archive.read(styles_file)).findall(W + "style")
            if s.find(W + "name") is not None
        }
        targets = {
            r.get("Id"): r.get("Target")
            for r in ET.fromstring(archive.read("word/_rels/document.xml.rels"))
        }
        current = None
        for block in body:
            if block.tag == W + "tbl":
                key = next(k for k in BLOCKS if k.startswith(f"{current:02d}") and k not in tables)
                tables[key] = fill_table(block, key)
                continue
            if block.tag != W + "p":
                continue
            bookmark_names = [n.get(W + "name", "") for n in block.iter(W + "bookmarkStart")]
            tags = [m for name in bookmark_names if (m := re.fullmatch(r"(Fig|Tab)(\d+)", name))]
            if tags:
                (tag,) = tags
                number = int(tag[2])
                if tag[1] == "Fig":
                    target, image = localize_figure(
                        number, block.getprevious(), block, archive, targets, assets_dir / "figures"
                    )
                    changes[target] = image
                    figures_done.add(number)
                else:
                    current = number
                    set_parts(block, [f"Table\u00a0{number}. {TABLE_TITLES[number]}"])
                    titles_done.add(number)
                continue
            st = block.find(f"{W}pPr/{W}pStyle")
            style = styles.get(st.get(W + "val")) if st is not None else None
            text = text_of(block)
            if style not in ("PanelLabel", "TableNote") and not text.startswith("构造判读覆盖岜团"):
                continue
            (key,) = [k for k in NOTES if text.startswith(k)]
            parts = NOTES[key]
            if "SHA-256" in key:
                digest = re.search(r"[0-9a-f]{64}$", text)[0]
                parts = (f"{parts[0]}  {digest}",)
            set_parts(block, parts)
            fmt = Paragraph(block, None).paragraph_format
            if key == "构造判读覆盖岜团":
                note_style = next(i for i, name in styles.items() if name == "TableNote")
                block.get_or_add_pPr().style = note_style
                fmt.first_line_indent = Pt(0)
            fmt.alignment = WD_ALIGN_PARAGRAPH.LEFT
            fmt.line_spacing = Pt(13 if style == "PanelLabel" else 12)
            notes_done.add(key)
        if (
            figures_done != FIGURE_CAPTIONS.keys()
            or titles_done != TABLE_TITLES.keys()
            or tables.keys() != BLOCKS.keys()
            or notes_done != NOTES.keys()
        ):
            raise ValueError(
                "The manuscript does not contain the complete numbered figure and table inventory"
            )
        changes[document_file] = ET.tostring(
            document, encoding="UTF-8", standalone=True, xml_declaration=True
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        table_dir = assets_dir / "tables"
        table_dir.mkdir(parents=True, exist_ok=True)
        for key, rows in tables.items():
            with (table_dir / f"Table-{key}.csv").open(
                "w", encoding="utf-8-sig", newline=""
            ) as stream:
                csv.writer(stream).writerows(rows)
        with NamedTemporaryFile(dir=output.parent, suffix=".docx", delete=False) as tmp:
            temp = Path(tmp.name)
        try:
            with ZipFile(temp, "w") as revised:
                revised.comment = archive.comment
                for info in archive.infolist():
                    name = info.filename
                    revised.writestr(info, changes[name] if name in changes else archive.read(name))
            os.replace(temp, output)
        finally:
            temp.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--assets-dir", type=Path, default=ASSETS)
    args = parser.parse_args()
    build(args.source, args.output, args.assets_dir)
