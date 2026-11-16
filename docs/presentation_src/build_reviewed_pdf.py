"""Render the reviewed content with existing ReportLab/fonts, never installing anything.

Run with an already prepared Python containing reportlab. Keeps presentation.pdf.
"""
from pathlib import Path
import html
import json
import os

from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from reportlab.lib.utils import ImageReader

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
W, H = 960, 540


def font_path(env, candidates):
    custom = os.environ.get(env)
    for path in ([custom] if custom else []) + candidates:
        if path and Path(path).is_file():
            return path
    raise FileNotFoundError('No existing Cyrillic font; supply ' + env + '. No font is downloaded.')


def main():
    regular = font_path('PDF_FONT_REGULAR', ['/System/Library/Fonts/Supplemental/Arial.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'])
    bold = font_path('PDF_FONT_BOLD', ['/System/Library/Fonts/Supplemental/Arial Bold.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'])
    pdfmetrics.registerFont(TTFont('ReviewSans', regular))
    pdfmetrics.registerFont(TTFont('ReviewSansBold', bold))
    slides = json.loads((HERE / 'reviewed_slides.json').read_text(encoding='utf-8'))
    output = ROOT / 'presentation_reviewed.pdf'
    c = canvas.Canvas(str(output), pagesize=(W, H))
    c.setTitle('ПСБ — юридический ассистент: проверенная редакция содержания')
    c.setAuthor('Никита — аналитика и приёмка')
    bg, card, ink, secondary, accent = map(HexColor, ['#080f2e', '#142354', '#f2f5ff', '#c1cbe2', '#ff602e'])
    def text(value, x, top, width, size=18, color=None, bold=False, leading=None):
        style = ParagraphStyle('p', fontName='ReviewSansBold' if bold else 'ReviewSans',
            fontSize=size, leading=leading or size*1.35, textColor=color or ink)
        p = Paragraph(html.escape(value), style)
        _, height = p.wrap(width, H)
        p.drawOn(c, x, top-height)
        return height
    for index, slide in enumerate(slides, 1):
        c.setFillColor(bg); c.rect(0, 0, W, H, fill=1, stroke=0)
        c.setFillColor(accent); c.rect(40, 486, 28, 3, fill=1, stroke=0)
        text(slide['kicker'], 40, 475, 880, 9.5, accent, True)
        title_h = text(slide['title'], 40, 447, 880, 29, bold=True, leading=35)
        top = 447-title_h-26
        width = 430 if slide.get('image') else 840
        y = top
        for bullet in slide['bullets']:
            c.setFillColor(accent); c.circle(45, y-10, 2.6, fill=1, stroke=0)
            height = text(bullet, 60, y, width-20, 17, secondary, leading=24)
            y -= height+16
        if slide.get('image'):
            path=HERE/slide['image']
            reader=ImageReader(str(path)); iw, ih = reader.getSize()
            image_w=410; image_h=min(260, image_w*ih/iw)
            c.setFillColor(card); c.roundRect(500, top-image_h-16, 420, image_h+18, 9, fill=1, stroke=0)
            c.drawImage(reader,505,top-image_h-8,width=image_w,height=image_h,preserveAspectRatio=True,anchor='c')
            text(slide['caption'],500,top-image_h-31,420,10,secondary,leading=14)
        if slide.get('callout'):
            height=Paragraph(html.escape(slide['callout']),ParagraphStyle('c',fontName='ReviewSans',fontSize=12,leading=17)).wrap(840,200)[1]
            box_top=min(y-4,105)
            box_bottom=box_top-height-22
            if box_bottom<49:
                raise ValueError(f'Slide {index}: callout would overlap footer ({box_bottom})')
            c.setFillColor(card);c.roundRect(40,box_bottom,880,height+22,8,fill=1,stroke=0)
            text(slide['callout'],54,box_top-10,850,12,secondary,leading=17)
        if y<50:
            raise ValueError(f'Slide {index}: body overflow ({y})')
        c.setStrokeColor(HexColor('#27345c'));c.line(40,40,920,40)
        text(slide['source'],40,30,790,8,secondary,leading=10)
        c.setFont('ReviewSans',9);c.setFillColor(secondary);c.drawRightString(920,20,f'{index} / {len(slides)}')
        if index==1:
            c.linkURL('https://github.com/v1ewzz/psb-legal-assistant/tree/codex/nikita-final-review',(55,top-75,900,top),relative=0)
        c.showPage()
    c.save()
    print(output)


if __name__=='__main__':
    main()
