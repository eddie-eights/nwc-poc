#!/usr/bin/env python3
"""Markdown 風のデッキ記法から .pptx を組み立てる。

    render_pptx.py deck.md [-o out.pptx]

依存は python-pptx だけ（pyproject の docs のグループ）。
デザイントークンは My Repo の design-system/document.css から写した。
"""

from __future__ import annotations

import argparse
import functools
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.parts.image import Image as PptxImage
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

# ---- デザイントークン (document.css と同じ値) ------------------------------

BG = RGBColor(0xFA, 0xF9, 0xF6)
SURFACE = RGBColor(0xFF, 0xFF, 0xFF)
INK = RGBColor(0x11, 0x11, 0x10)
BODY = RGBColor(0x1A, 0x1A, 0x1A)
MUTED = RGBColor(0x59, 0x59, 0x59)
FAINT = RGBColor(0xA5, 0xA5, 0xA5)
RULE = RGBColor(0xDF, 0xDF, 0xDF)
LINK = RGBColor(0x29, 0x90, 0xDA)
ACCENT = RGBColor(0xD6, 0x3A, 0x2F)

DEFAULT_FONT_JA = "Noto Sans JP"
DEFAULT_FONT_LATIN = "Lato"
DEFAULT_FONT_MONO = "Menlo"

# ---- 版面 (16:9 / 13.333in x 7.5in) ---------------------------------------

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)
MARGIN = Inches(0.92)
CONTENT_W = SLIDE_W - MARGIN * 2

TITLE_TOP = Inches(0.60)
TITLE_H = Inches(0.86)
RULE_Y = Inches(1.56)
BODY_TOP = Inches(1.92)
BODY_BOTTOM = Inches(6.72)
BODY_H = BODY_BOTTOM - BODY_TOP
FOOTER_Y = Inches(6.86)

# ブロック同士の空き。draw_* の戻り値と measure_* で同じ値を使う。
BLOCK_GAP = Inches(0.26)
COL_GAP = Inches(0.55)

# 行送りと段落後の空き。line_spacing / space_after の設定値そのもの。
LINE_SPACING = 1.55
PARA_GAP = 0.44  # フォントサイズに対する比
QUOTE_SPACING = 1.5
CODE_SPACING = 1.35
TABLE_SPACING = 1.35


# ---- パーサ ----------------------------------------------------------------


@dataclass
class Slide:
    kind: str  # "section" | "content"
    title: str = ""
    blocks: list = field(default_factory=list)
    notes: str = ""


FENCE_RE = re.compile(r"^```(\S*)\s*$")
IMAGE_RE = re.compile(r"^!\[(.*?)\]\((.+?)\)\s*$")
BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.*)$")
TABLE_RE = re.compile(r"^\s*\|(.+)\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
DIRECTIVE_RE = re.compile(r"^:::\s*(\w*)\s*$")
# 桁を持つ数値セル。連番のような文字列としての数字は右寄せにしない。
NUMERIC_CELL_RE = re.compile(r"^[\u00a5$€]?\s*-?[\d,]+(\.\d+)?\s*[%\u5186]?$")


def parse_front_matter(lines: list[str]) -> tuple[dict, int]:
    """先頭の --- 〜 --- を key: value として読む。YAML 依存は持たない。"""
    if not lines or lines[0].strip() != "---":
        return {}, 0
    meta: dict[str, str] = {}
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return meta, i + 1
        if ":" in lines[i]:
            k, _, v = lines[i].partition(":")
            meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    # 閉じ --- が無いまま EOF。本文を meta に取り込むと事故るので front matter 無し扱い。
    return {}, 0


CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def split_table_row(line: str) -> list[str]:
    """行をセルに割る。\\| は縦棒そのものとして残す。"""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip().replace("\\|", "|") for c in CELL_SPLIT_RE.split(body)]


def parse_blocks(lines: list[str]) -> list:
    """スライド本文をブロック列に畳む。"""
    blocks: list = []
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        line = raw.rstrip()
        if not line.strip():
            i += 1
            continue

        m = DIRECTIVE_RE.match(line.strip())
        if m and m.group(1) in ("columns", "cols"):
            depth, body = 1, []
            i += 1
            while i < n:
                inner = lines[i].strip()
                if DIRECTIVE_RE.match(inner) and DIRECTIVE_RE.match(inner).group(1):
                    depth += 1
                elif inner == ":::":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                body.append(lines[i])
                i += 1
            left, cur, fenced = None, [], False
            for b in body:
                if FENCE_RE.match(b.rstrip()):
                    fenced = not fenced
                elif not fenced and left is None and b.strip() == "---":
                    left, cur = cur, []  # 区切りは最初の 1 本だけ
                    continue
                cur.append(b)
            if left is None:  # 区切りが無ければ全部を左に置く
                left, right = cur, []
            else:
                right = cur
            blocks.append(("columns", parse_blocks(left), parse_blocks(right)))
            continue

        m = FENCE_RE.match(line)
        if m:
            lang, code = m.group(1), []
            i += 1
            while i < n and not FENCE_RE.match(lines[i].rstrip()):
                code.append(lines[i].rstrip("\n"))
                i += 1
            i += 1
            blocks.append(("code", lang, code))
            continue

        m = IMAGE_RE.match(line.strip())
        if m:
            blocks.append(("image", m.group(2).strip(), m.group(1).strip()))
            i += 1
            continue

        if BULLET_RE.match(line):
            items = []
            while i < n and BULLET_RE.match(lines[i].rstrip()):
                mb = BULLET_RE.match(lines[i].rstrip())
                level = len(mb.group(1).replace("\t", "  ")) // 2
                items.append((min(level, 2), mb.group(2).strip()))
                i += 1
            blocks.append(("bullets", items))
            continue

        if TABLE_RE.match(line):
            rows = []
            while i < n and TABLE_RE.match(lines[i].rstrip()):
                if not TABLE_SEP_RE.match(lines[i].rstrip()):
                    rows.append(split_table_row(lines[i]))
                i += 1
            if rows:
                blocks.append(("table", rows))
            continue

        if line.lstrip().startswith(">"):
            quote = []
            while i < n and lines[i].lstrip().startswith(">"):
                quote.append(lines[i].lstrip()[1:].strip())
                i += 1
            blocks.append(("quote", quote))
            continue

        para = []
        while i < n and lines[i].strip() and not _starts_block(lines[i]):
            para.append(lines[i].strip())
            i += 1
        if para:
            blocks.append(("para", " ".join(para)))
        else:
            # 未知のディレクティブ (::: warning) や孤立した ::: 。
            # ここで進めないと i が動かず無限ループになる。
            i += 1
    return blocks


def _starts_block(line: str) -> bool:
    s = line.rstrip()
    return bool(
        BULLET_RE.match(s)
        or TABLE_RE.match(s)
        or FENCE_RE.match(s)
        or IMAGE_RE.match(s.strip())
        or DIRECTIVE_RE.match(s.strip())
        or s.lstrip().startswith(">")
    )


def parse_deck(text: str) -> tuple[dict, list[Slide]]:
    lines = text.replace("\r\n", "\n").split("\n")
    meta, start = parse_front_matter(lines)
    slides: list[Slide] = []
    cur: Slide | None = None
    buf: list[str] = []
    notes: list[str] = []
    in_notes = False
    in_fence = False

    def flush():
        nonlocal buf, notes
        if cur is not None:
            cur.blocks = parse_blocks(buf)
            cur.notes = "\n".join(notes).strip()
        buf, notes = [], []

    for line in lines[start:]:
        s = line.rstrip()
        if not in_notes and FENCE_RE.match(s):
            in_fence = not in_fence
            buf.append(line)
            continue
        if in_fence:  # フェンス内の # / ## は見出しではなくコード
            buf.append(line)
            continue
        m = DIRECTIVE_RE.match(s.strip())
        if m and m.group(1) == "notes":
            in_notes = True
            continue
        if in_notes:
            if s.strip() == ":::":
                in_notes = False
            else:
                notes.append(s)
            continue
        if s.startswith("## "):
            flush()
            cur = Slide(kind="content", title=s[3:].strip())
            slides.append(cur)
            continue
        if s.startswith("# "):
            flush()
            cur = Slide(kind="section", title=s[2:].strip())
            slides.append(cur)
            continue
        buf.append(line)
    flush()
    return meta, slides


# ---- 描画ヘルパ ------------------------------------------------------------

INLINE_RE = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")


class Theme:
    def __init__(self, meta: dict):
        self.ja = meta.get("font-ja", DEFAULT_FONT_JA)
        self.latin = meta.get("font-latin", DEFAULT_FONT_LATIN)
        self.mono = meta.get("font-mono", DEFAULT_FONT_MONO)


def style_run(run, theme: Theme, size, *, bold=False, color=BODY, mono=False, italic=False):
    font = run.font
    font.name = theme.mono if mono else theme.latin
    font.size = Pt(size)
    font.bold = bold
    font.italic = italic
    font.color.rgb = color
    rPr = run._r.get_or_add_rPr()
    latin = rPr.find(qn("a:latin"))
    if latin is None:  # font.name が立てるはずだが念のため
        latin = rPr.makeelement(qn("a:latin"), {"typeface": font.name})
        rPr.append(latin)
    face = theme.mono if mono else theme.ja
    prev = latin
    for tag in ("a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = rPr.makeelement(qn(tag), {"typeface": face})
            prev.addnext(el)
        else:
            el.set("typeface", face)
        prev = el


def add_inline(paragraph, text: str, theme: Theme, size, *, color=BODY, bold=False):
    """**強調** と `コード` だけ解釈して run に割る。"""
    for part in INLINE_RE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            r = paragraph.add_run()
            r.text = part[2:-2]
            style_run(r, theme, size, bold=True, color=INK)
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            r = paragraph.add_run()
            r.text = part[1:-1]
            style_run(r, theme, size * 0.94, color=color, mono=True)
        else:
            r = paragraph.add_run()
            r.text = part
            style_run(r, theme, size, bold=bold, color=color)


def add_textbox(slide, left, top, width, height, *, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(_emu(left), _emu(top), _emu(width), _emu(height))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    return box, tf


def add_rect(slide, left, top, width, height, fill=None, line=None, line_w=Pt(1.2)):
    shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, _emu(left), _emu(top), _emu(width), _emu(height))
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = line_w
    shp.shadow.inherit = False
    return shp


def _emu(value) -> Emu:
    """寸法計算で float になったものを EMU に丸める。"""
    return Emu(int(round(value)))


def paint_background(slide):
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = BG


def set_cell_borders(cell, *, bottom=None, width=Pt(0.75)):
    tcPr = cell._tc.get_or_add_tcPr()
    for tag in ("a:lnL", "a:lnR", "a:lnT", "a:lnB"):
        for old in tcPr.findall(qn(tag)):
            tcPr.remove(old)
    if bottom is not None:
        ln = tcPr.makeelement(qn("a:lnB"), {"w": str(int(width)), "cap": "flat"})
        fill = ln.makeelement(qn("a:solidFill"), {})
        clr = fill.makeelement(qn("a:srgbClr"), {"val": str(bottom)})
        fill.append(clr)
        ln.append(fill)
        # CT_TableCellProperties は lnL/lnR/lnT/lnB → 塗り の順序が必須。
        # append だと noFill の後ろに付いて PowerPoint が壊れたファイル扱いにする。
        tcPr.insert(0, ln)


# ---- スライド組み ----------------------------------------------------------


def render_title_slide(prs, meta: dict, theme: Theme):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    paint_background(slide)
    add_rect(slide, MARGIN, Inches(2.30), Inches(0.11), Inches(1.28), fill=INK)

    _, tf = add_textbox(slide, MARGIN + Inches(0.34), Inches(2.22),
                        CONTENT_W - Inches(0.34), Inches(1.5))
    p = tf.paragraphs[0]
    p.line_spacing = 1.25
    add_inline(p, meta.get("title", "Untitled"), theme, 40, color=INK, bold=True)

    sub = meta.get("subtitle", "")
    if sub:
        _, tf2 = add_textbox(slide, MARGIN + Inches(0.34), Inches(3.86),
                             CONTENT_W - Inches(0.34), Inches(0.7))
        p2 = tf2.paragraphs[0]
        p2.line_spacing = 1.5
        add_inline(p2, sub, theme, 18, color=MUTED)

    meta_bits = [b for b in (meta.get("date", ""), meta.get("author", ""),
                             meta.get("source", "")) if b]
    if meta_bits:
        _, tf3 = add_textbox(slide, MARGIN + Inches(0.34), Inches(4.68),
                             CONTENT_W - Inches(0.34), Inches(0.5))
        p3 = tf3.paragraphs[0]
        r = p3.add_run()
        r.text = "  /  ".join(meta_bits)
        style_run(r, theme, 12, color=FAINT, mono=True)


def render_section_slide(prs, slide_no: int, s: Slide, theme: Theme, total: int,
                         section_no: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    paint_background(slide)
    add_rect(slide, MARGIN, Inches(3.02), Inches(0.11), Inches(1.02), fill=ACCENT)

    _, tfn = add_textbox(slide, MARGIN + Inches(0.34), Inches(2.56),
                         CONTENT_W, Inches(0.42))
    rn = tfn.paragraphs[0].add_run()
    rn.text = f"{section_no:02d}"
    style_run(rn, theme, 13, color=FAINT, mono=True)

    _, tf = add_textbox(slide, MARGIN + Inches(0.34), Inches(2.98),
                        CONTENT_W - Inches(0.34), Inches(1.2))
    p = tf.paragraphs[0]
    p.line_spacing = 1.25
    add_inline(p, s.title, theme, 32, color=INK, bold=True)

    lead = next((b[1] for b in s.blocks if b[0] == "para"), "")
    if lead:
        _, tf2 = add_textbox(slide, MARGIN + Inches(0.34), Inches(4.32),
                             Inches(8.4), Inches(1.0))
        p2 = tf2.paragraphs[0]
        p2.line_spacing = 1.6
        add_inline(p2, lead, theme, 14, color=MUTED)

    add_footer(slide, theme, slide_no, total)
    attach_notes(slide, s.notes)


def add_footer(slide, theme: Theme, no: int, total: int):
    _, tf = add_textbox(slide, MARGIN, FOOTER_Y, CONTENT_W, Inches(0.3))
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.RIGHT
    r = p.add_run()
    r.text = f"{no} / {total}"
    style_run(r, theme, 10, color=FAINT, mono=True)


def attach_notes(slide, notes: str):
    if notes:
        slide.notes_slide.notes_text_frame.text = notes


# ---- 寸法計算 --------------------------------------------------------------
# draw_* と measure_* は同じ式を使うこと。片方だけ直すと版面からはみ出す。


def _lines_for(text: str, width, size: float, level: int = 0) -> int:
    return max(1, -(-len(text) // wrap_chars(width, size, level)))


def para_height(text: str, width, size: float) -> int:
    return _emu(_lines_for(text, width, size) * Pt(LINE_SPACING * size))


def bullets_height(items, width, size: float) -> int:
    h = 0.0
    for level, text in items:
        isize = size if not level else size * 0.92
        h += _lines_for(text, width, isize, level) * Pt(LINE_SPACING * isize)
        h += Pt(PARA_GAP * size)
    return _emu(h)


def quote_split(lines):
    body = [l for l in lines if l and not l.startswith("\u2014") and not l.startswith("--")]
    src = next((l for l in lines if l.startswith("\u2014") or l.startswith("--")), "")
    return body or [""], src


def quote_height(lines, width, size: float) -> int:
    body, src = quote_split(lines)
    inner = width - Inches(0.30)
    h = sum(_lines_for(l, inner, size * 1.02) * Pt(QUOTE_SPACING * size * 1.02)
            for l in body)
    if src:
        h += Pt(6) + Pt(QUOTE_SPACING * size * 0.8)
    return _emu(h + Inches(0.10))


def code_size(size: float) -> float:
    return min(size * 0.78, 15.0)


def code_height(lines, size: float) -> int:
    csize = code_size(size)
    return _emu(max(1, len(lines)) * Pt(CODE_SPACING * csize) + Inches(0.40))


def table_row_heights(rows, width, size: float) -> list:
    """行ごとの高さ。セルが折り返す分だけ背を伸ばす。"""
    ncols = max((len(r) for r in rows), default=1)
    cell_w = _emu(width / ncols) - Inches(0.20)
    csize = size * 0.82
    floor = _emu(Inches(0.44) * (size / 18.0) + Inches(0.10))
    out = []
    for row in rows:
        n = max([_lines_for(c, cell_w, csize) for c in row] or [1])
        out.append(max(floor, _emu(n * Pt(TABLE_SPACING * csize) + Inches(0.10))))
    return out


def table_height(rows, width, size: float) -> int:
    return sum(table_row_heights(rows, width, size))


IMAGE_EST_H = Inches(2.60)  # 画像を開けなかったときの保険


@functools.lru_cache(maxsize=64)
def image_native_size(path: str):
    """画像の原寸を EMU で返す。開けなければ None。"""
    try:
        img = PptxImage.from_file(path)
        px_w, px_h = img.size
        dpi_x, dpi_y = img.dpi
        return int(px_w * 914400 / dpi_x), int(px_h * 914400 / dpi_y)
    except Exception:
        return None


def resolve_image(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (Path.cwd() / p).resolve()


def image_height(path: str, caption: str, width, avail) -> int:
    """draw_image が実際に取る高さ。measure と draw で同じ計算を使う。"""
    cap_h = Inches(0.42) if caption else 0
    native = image_native_size(str(resolve_image(path)))
    if native is None:
        return _emu(min(IMAGE_EST_H, avail))
    nw, nh = native
    max_h = max(Inches(1.0), avail - cap_h - Inches(0.10))
    scale = min(width / nw, max_h / nh, 1.0)
    return _emu(nh * scale) + cap_h


def measure_blocks(blocks, width, size: float) -> int:
    """draw_blocks が使う高さの合計。版面に収まるかの判定に使う。"""
    y = 0
    for b in blocks:
        kind = b[0]
        if kind == "para":
            y += para_height(b[1], width, size) + BLOCK_GAP
        elif kind == "bullets":
            y += bullets_height(b[1], width, size) + BLOCK_GAP
        elif kind == "table":
            y += table_height(b[1], width, size) + BLOCK_GAP
        elif kind == "quote":
            y += quote_height(b[1], width, size) + BLOCK_GAP
        elif kind == "code":
            y += code_height(b[2], size) + BLOCK_GAP
        elif kind == "image":
            y += image_height(b[1], b[2], width, BODY_H - y) + BLOCK_GAP
        elif kind == "columns":
            cw = _emu((width - COL_GAP) / 2)
            y += max(measure_blocks(b[1], cw, size),
                     measure_blocks(b[2], cw, size))
    return y


BODY_SIZES = (20.0, 18.0, 16.0, 14.0, 12.0)


def body_font_size(blocks, width=CONTENT_W, avail=BODY_H) -> float:
    """本文が版面に収まる一番大きいサイズ。収まらなければ最小サイズを返す。"""
    for size in BODY_SIZES:
        if measure_blocks(blocks, width, size) <= avail:
            return size
    return BODY_SIZES[-1]


def render_content_slide(prs, slide_no: int, s: Slide, theme: Theme, total: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    paint_background(slide)

    add_rect(slide, MARGIN, TITLE_TOP + Inches(0.10), Inches(0.09), Inches(0.44), fill=INK)
    _, tf = add_textbox(slide, MARGIN + Inches(0.28), TITLE_TOP,
                        CONTENT_W - Inches(0.28), TITLE_H)
    p = tf.paragraphs[0]
    p.line_spacing = 1.2
    add_inline(p, s.title, theme, 24, color=INK, bold=True)

    line = slide.shapes.add_connector(1, MARGIN, RULE_Y, MARGIN + CONTENT_W, RULE_Y)
    line.line.color.rgb = RULE
    line.line.width = Pt(1.0)

    size = body_font_size(s.blocks)
    if measure_blocks(s.blocks, CONTENT_W, size) > BODY_H:
        print(f"警告: スライド {slide_no}「{s.title}」は最小サイズでも本文が"
              f"版面に収まらない。情報を削るかスライドを分けること。", file=sys.stderr)
    draw_blocks(slide, s.blocks, MARGIN, BODY_TOP, CONTENT_W, BODY_H, theme, size)

    add_footer(slide, theme, slide_no, total)
    attach_notes(slide, s.notes)


def draw_blocks(slide, blocks, left, top, width, height, theme: Theme, size: float):
    y = top
    for b in blocks:
        kind = b[0]
        if kind == "para":
            h = para_height(b[1], width, size)
            _, tf = add_textbox(slide, left, y, width, h)
            p = tf.paragraphs[0]
            p.line_spacing = LINE_SPACING
            add_inline(p, b[1], theme, size, color=BODY)
            y += h + BLOCK_GAP
        elif kind == "bullets":
            y = draw_bullets(slide, b[1], left, y, width, theme, size)
        elif kind == "table":
            y = draw_table(slide, b[1], left, y, width, theme, size)
        elif kind == "quote":
            y = draw_quote(slide, b[1], left, y, width, theme, size)
        elif kind == "code":
            y = draw_code(slide, b[2], left, y, width, theme, size)
        elif kind == "image":
            y = draw_image(slide, b[1], b[2], left, y, width, top + height - y, theme)
        elif kind == "columns":
            cw = _emu((width - COL_GAP) / 2)
            remaining = top + height - y
            y_left = draw_column(slide, b[1], left, y, cw, remaining, theme, size)
            y_right = draw_column(slide, b[2], left + cw + COL_GAP, y, cw,
                                  remaining, theme, size)
            y = max(y_left, y_right)
    return y


def draw_column(slide, blocks, left, top, width, height, theme, size):
    if not blocks:
        return top
    return draw_blocks(slide, blocks, left, top, width, height, theme, size)


def wrap_chars(width_emu, size: float, level: int = 0) -> int:
    """全角 1 文字 ≒ フォントサイズ幅として、1 行に入る概算文字数を返す。"""
    width_pt = (width_emu - Inches(0.30) * level) / 914400 * 72
    return max(12, int(width_pt / max(size, 1.0)))


def draw_bullets(slide, items, left, top, width, theme: Theme, size: float):
    h = bullets_height(items, width, size)
    _, tf = add_textbox(slide, left, top, width, h)
    tf.word_wrap = True
    for idx, (level, text) in enumerate(items):
        isize = size if not level else size * 0.92
        p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
        p.line_spacing = LINE_SPACING
        p.space_after = Pt(size * PARA_GAP)
        p.level = level
        marker = "\u2014  " if level else "\u30fb"
        r = p.add_run()
        r.text = marker
        style_run(r, theme, isize, color=ACCENT if not level else FAINT)
        add_inline(p, text, theme, isize, color=BODY if not level else MUTED)
        pPr = p._pPr if p._pPr is not None else p._p.get_or_add_pPr()
        pPr.set("marL", str(int(Inches(0.30) * level)))
        pPr.set("indent", "0")
    return top + h + BLOCK_GAP


def draw_quote(slide, lines, left, top, width, theme: Theme, size: float):
    body, src = quote_split(lines)
    h = quote_height(lines, width, size)
    add_rect(slide, left, top, Inches(0.055), h, fill=ACCENT)
    _, tf = add_textbox(slide, left + Inches(0.30), top, width - Inches(0.30), h)
    for idx, l in enumerate(body):
        p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
        p.line_spacing = QUOTE_SPACING
        add_inline(p, l, theme, size * 1.02, color=INK)
    if src:
        p = tf.add_paragraph()
        p.line_spacing = QUOTE_SPACING
        p.space_before = Pt(6)
        r = p.add_run()
        r.text = src
        style_run(r, theme, size * 0.8, color=MUTED)
    return top + h + BLOCK_GAP


def draw_code(slide, lines, left, top, width, theme: Theme, size: float):
    csize = code_size(size)
    h = code_height(lines, size)
    add_rect(slide, left, top, width, h, fill=SURFACE, line=RULE, line_w=Pt(1.0))
    _, tf = add_textbox(slide, left + Inches(0.24), top + Inches(0.20),
                        width - Inches(0.48), h - Inches(0.40))
    for idx, l in enumerate(lines):
        p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
        p.line_spacing = CODE_SPACING
        r = p.add_run()
        r.text = l if l else " "
        style_run(r, theme, csize, color=BODY, mono=True)
    return top + h + BLOCK_GAP


def draw_table(slide, rows, left, top, width, theme: Theme, size: float):
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    heights = table_row_heights(rows, width, size)
    total_h = sum(heights)
    shape = slide.shapes.add_table(len(rows), ncols, _emu(left), _emu(top),
                                   _emu(width), total_h)
    table = shape.table
    tblPr = table._tbl.find(qn("a:tblPr"))
    if tblPr is not None:
        tblPr.set("firstRow", "1")
        tblPr.set("bandRow", "0")
        for style in tblPr.findall(qn("a:tableStyleId")):
            tblPr.remove(style)
    for r_i, row in enumerate(rows):
        table.rows[r_i].height = heights[r_i]
        for c_i, text in enumerate(row):
            cell = table.cell(r_i, c_i)
            cell.fill.background()
            cell.margin_left = cell.margin_right = Inches(0.10)
            cell.margin_top = cell.margin_bottom = Inches(0.05)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.line_spacing = TABLE_SPACING
            # 数字だけのセルは右寄せ。桁を見分けやすくする。
            if r_i and c_i and NUMERIC_CELL_RE.match(text):
                p.alignment = PP_ALIGN.RIGHT
            add_inline(p, text, theme, size * 0.82,
                       color=INK if r_i == 0 else BODY, bold=(r_i == 0))
            set_cell_borders(cell,
                             bottom="111110" if r_i == 0 else "DFDFDF",
                             width=Pt(1.2) if r_i == 0 else Pt(0.75))
    return top + total_h + BLOCK_GAP


def draw_image(slide, path, caption, left, top, width, avail_h, theme: Theme):
    p = resolve_image(path)
    pic, reason = None, None
    if not p.is_file():
        reason = "見つからない"
    else:
        try:
            pic = slide.shapes.add_picture(str(p), _emu(left), _emu(top))
        except Exception as exc:  # テキスト・破損・未対応形式など
            reason = "読めない"
            print(f"警告: 画像として読めない {path}: {exc}", file=sys.stderr)
    if pic is None:
        _, tf = add_textbox(slide, left, top, width, Inches(0.4))
        r = tf.paragraphs[0].add_run()
        r.text = f"[画像が{reason}: {path}]"
        style_run(r, theme, 13, color=ACCENT)
        return top + Inches(0.5) + BLOCK_GAP
    cap_h = Inches(0.42) if caption else 0
    max_h = max(Inches(1.0), avail_h - cap_h - Inches(0.10))
    scale = min(width / pic.width, max_h / pic.height, 1.0)
    pic.width = _emu(pic.width * scale)
    pic.height = _emu(pic.height * scale)
    pic.left = _emu(left + (width - pic.width) / 2)
    if caption:
        _, tf = add_textbox(slide, left, top + pic.height + Inches(0.14),
                            width, Inches(0.36))
        cp = tf.paragraphs[0]
        cp.alignment = PP_ALIGN.CENTER
        r = cp.add_run()
        r.text = caption
        style_run(r, theme, 11, color=MUTED)
    return top + pic.height + cap_h + BLOCK_GAP


# ---- エントリポイント ------------------------------------------------------


def build(src: Path, out: Path) -> Path:
    meta, slides = parse_deck(src.read_text(encoding="utf-8"))
    theme = Theme(meta)
    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H

    has_title = bool(meta.get("title"))
    total = len(slides) + (1 if has_title else 0)
    if total == 0:
        print(f"警告: {src} に見出し (# / ##) も title もない。"
              f"空の pptx になる。", file=sys.stderr)
    if has_title:
        render_title_slide(prs, meta, theme)
    section_no = 0
    for i, s in enumerate(slides, start=2 if has_title else 1):
        if s.kind == "section":
            section_no += 1
            render_section_slide(prs, i, s, theme, total, section_no)
        else:
            render_content_slide(prs, i, s, theme, total)
    prs.save(str(out))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="デッキ記法 (.md) から .pptx を作る")
    ap.add_argument("source", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=None)
    args = ap.parse_args(argv)

    src: Path = args.source
    if not src.exists():
        print(f"見つからない: {src}", file=sys.stderr)
        return 1
    out = args.out or src.with_suffix(".pptx")
    build(src, out)
    print(f"書き出した: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
