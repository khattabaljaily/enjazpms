"""
توليد ملف PDF منسّق لاستبيان عميل معبّأ، بشكل الاستبيان الأصلي (رأس داكن، عناوين ذهبية، خيارات معلَّمة).

يعتمد على PyMuPDF (Story) لأنه يشكّل الحروف العربية ويدعم RTL دون مكتبات إضافية.
"""
import io
import re
from html import escape

import pymupdf
from django.conf import settings
from django.utils import timezone
from PIL import Image, ImageDraw

from . import survey_schema as S

NAVY = '#1b2236'
GOLD = '#c9840f'
GOLD_SOFT = '#fdf3e1'
ORANGE = '#f5a623'
INK = '#1b2236'
MUTED = '#6b7280'
LINE = '#e3e7ee'
FILL_BG = '#f4f6fa'

PAGE_W, PAGE_H = 595, 842
MARGIN_X = 40
HEADER_H = 58
FOOTER_H = 34

FONT_DIR = settings.BASE_DIR / 'static' / 'fonts' / 'cairo'
SURVEY_IMG_DIR = settings.BASE_DIR / 'static' / 'img' / 'survey'


def _badge_png(num):
    """دائرة ذهبية برقم السؤال، كما في الاستبيان الأصلي."""
    from PIL import ImageFont
    size = 120
    img = Image.new('RGBA', (size, size), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, size - 2, size - 2), fill=(201, 132, 15, 255))
    font = ImageFont.truetype(str(FONT_DIR / 'Cairo-Bold.ttf'), 64)
    d.text((size / 2, size / 2), str(num), font=font, fill=(255, 255, 255, 255), anchor='mm')
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


def _bar_png():
    img = Image.new('RGBA', (148, 8), (245, 166, 35, 255))
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


def _marker_png(shape, checked):
    """أيقونة خيار (دائرة للاختيار الواحد، مربع للمتعدد) — ذهبية، ومعبّأة إن كانت محددة."""
    size = 96
    img = Image.new('RGBA', (size, size), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)
    box = (10, 10, size - 10, size - 10)
    gold = (201, 132, 15, 255)
    if shape == 'radio':
        d.ellipse(box, outline=gold, width=8, fill=(255, 255, 255, 255))
        if checked:
            d.ellipse((30, 30, size - 30, size - 30), fill=gold)
    else:
        d.rounded_rectangle(box, radius=16, outline=gold, width=8, fill=(255, 255, 255, 255))
        if checked:
            d.rounded_rectangle(box, radius=16, fill=gold)
            d.line([(26, 50), (42, 66), (72, 30)], fill=(255, 255, 255, 255), width=10, joint='curve')
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


CSS = f"""
@font-face {{ font-family: Cairo; src: url(Cairo-Regular.ttf); }}
@font-face {{ font-family: Cairo; font-weight: bold; src: url(Cairo-Bold.ttf); }}
body {{ font-family: Cairo; color: {INK}; font-size: 9.5pt; }}
p {{ margin: 0; line-height: 1.75; }}
.intro {{ font-size: 9.5pt; line-height: 1.7; margin-bottom: 8pt; }}
.note-warn {{ background-color: {GOLD_SOFT}; border-right: 3pt solid {GOLD}; padding: 6pt 9pt;
              color: #7a4b00; font-size: 8.5pt; margin: 4pt 0 6pt 0; }}
.note-info {{ background-color: #eef2fb; border-right: 3pt solid #4a63c9; padding: 6pt 9pt;
              color: #2b3a7a; font-size: 8.5pt; margin: 4pt 0 6pt 0; }}
.sec {{ font-size: 15pt; font-weight: bold; color: {GOLD}; margin-top: 14pt; margin-bottom: 2pt; }}
.q {{ font-size: 10.5pt; font-weight: bold; color: {INK}; margin-top: 9pt; }}
.qsub {{ font-size: 9.5pt; font-weight: bold; color: {GOLD}; margin-top: 7pt; }}
.hint {{ font-size: 8pt; color: {MUTED}; margin-bottom: 3pt; line-height: 1.5; }}
.lbl {{ font-size: 8.5pt; font-weight: bold; color: #4b5563; margin-top: 7pt; margin-bottom: 2pt; }}
.fill {{ background-color: {FILL_BG}; border: 0.8pt solid #cfd6e2; padding: 4pt 8pt; min-height: 12pt;
         font-size: 10pt; color: {INK}; }}
.fill-empty {{ color: #9aa3b2; }}
.opt {{ font-size: 9.5pt; }}
.opt-on {{ font-weight: bold; color: {INK}; }}
.opt-off {{ color: #5b6475; }}
.opt-hint {{ font-size: 7.5pt; color: {MUTED}; line-height: 1.4; }}
.num {{ color: {GOLD}; font-size: 8.5pt; font-weight: bold; }}
td {{ vertical-align: middle; padding: 1.5pt 0; }}
"""


def _rtl(html):
    """MuPDF يتجاهل CSS direction (يترك المحاذاة يساراً)؛ الصفة dir="rtl" هي التي تعمل."""
    return re.sub(r'<(p|div)(?=[\s>])', r'<\1 dir="rtl"', html)


def _fill(value, tall=False):
    value = (value or '').strip()
    style = ' style="height:46pt"' if tall else ''
    if not value:
        return f'<div class="fill"{style}><span class="fill-empty">—</span></div>'
    return f'<div class="fill"{style}>{escape(value).replace(chr(10), "<br>")}</div>'


def _marker(shape, checked):
    return f'<img src="m_{shape}_{int(checked)}.png" width="11" height="11">'


def _option_rows(options, selected, shape):
    rows = []
    for o in options:
        on = o['value'] in selected
        hint = f'<p class="opt-hint" style="margin-right:18pt">{escape(o["hint"])}</p>' if o.get('hint') else ''
        rows.append(
            f'<p class="opt {"opt-on" if on else "opt-off"}" style="margin-top:2.5pt">'
            f'{escape(o["label"])}&nbsp;&nbsp;{_marker(shape, on)}</p>{hint}')
    return ''.join(rows)


def _count_rows(items, data):
    rows = []
    for it in items:
        row = (data or {}).get(it['value'], {})
        on = bool(row.get('on'))
        cnt = (row.get('count') or '').strip()
        tail = f'&nbsp;&nbsp;<span class="num">— العدد: {escape(cnt)}</span>' if cnt else ''
        rows.append(
            f'<p class="opt {"opt-on" if on else "opt-off"}" style="margin-top:2.5pt">'
            f'{escape(it["label"])}{tail}&nbsp;&nbsp;{_marker("check", on)}</p>')
    return ''.join(rows)


def _question_html(q, answers):
    qid, t = q['id'], q['type']
    ans = answers.get(qid)
    out = []
    if not q['numbered'] and t in ('text', 'textarea', 'number'):
        out.append(f'<p class="lbl">{escape(q["label"])}</p>{_fill(ans, t == "textarea")}')
        return ''.join(out)

    if q.get('num'):
        out.append(f'<p class="q">{escape(q["label"])}&nbsp;&nbsp;<img src="n_{q["num"]}.png" width="15" height="15"></p>')
    else:
        out.append(f'<p class="qsub">{escape(q["label"])}</p>')
    if q['hint']:
        out.append(f'<p class="hint">{escape(q["hint"])}</p>')
    if q['image']:
        out.append(_survey_image_html(q['image']))
    if t == 'radio':
        out.append(_option_rows(q['options'], {ans} if ans else set(), 'radio'))
    elif t == 'checkbox':
        out.append(_option_rows(q['options'], set(ans or []), 'check'))
    elif t == 'count_list':
        out.append(_count_rows(q['items'], ans))
    elif t in ('text', 'textarea', 'number'):
        out.append(_fill(ans))
    if q['note']:
        is_warn = q['note'].startswith('هذا الاختيار')
        out.append(f'<div class="{"note-warn" if is_warn else "note-info"}">{escape(q["note"])}</div>')
    for s in q['sub']:
        out.append(_question_html(s, answers))
    return ''.join(out)


def _survey_image_html(rel):
    name = rel.rsplit('/', 1)[-1]
    return f'<p><img src="{name}" width="320"></p>'


def build_blocks(survey):
    """قائمة كتل HTML؛ كل كتلة سؤال كامل (لا يُقسم بين صفحتين)."""
    form = survey.form_type
    answers = survey.answers or {}
    blocks = [f'<p class="intro">{escape(S.INTRO[form])}</p>']
    for sec in S.numbered_sections(form):
        head = f'<p class="sec">{escape(sec["title"])}</p><p style="line-height:0.5;margin-bottom:8pt"><img src="bar.png" width="74" height="4"></p>'
        pending = head          # عنوان القسم يلتصق بأول أسئلته فلا يبقى وحيداً في آخر صفحة
        for q in sec['questions']:
            body = _question_html(q, answers)
            if not q['numbered'] and q['type'] in ('text', 'number'):
                pending += body      # الحقول البسيطة المتتالية في كتلة واحدة
                continue
            blocks.append(pending + body)
            pending = ''
        if pending:
            blocks.append(pending)
    return blocks


def _header_footer(page, title, n, total, stamp):
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(0, 0, PAGE_W, HEADER_H))
    shape.finish(color=None, fill=_rgb(NAVY))
    shape.draw_rect(pymupdf.Rect(0, HEADER_H, PAGE_W, HEADER_H + 3))
    shape.finish(color=None, fill=_rgb(ORANGE))
    shape.commit()
    css = ('@font-face{font-family:Cairo;src:url(Cairo-Bold.ttf);font-weight:bold}'
           '@font-face{font-family:Cairo;src:url(Cairo-Regular.ttf)}'
           'body{font-family:Cairo;}')
    arch = pymupdf.Archive(str(FONT_DIR))
    page.insert_htmlbox(
        pymupdf.Rect(150, 12, PAGE_W - MARGIN_X, HEADER_H - 4),
        f'<div dir="rtl" style="color:#f9d99a;font-weight:bold;font-size:15pt">{escape(title)}</div>',
        css=css, archive=arch)
    page.insert_htmlbox(
        pymupdf.Rect(MARGIN_X, 14, 140, HEADER_H - 4),
        f'<div style="text-align:left;color:{ORANGE};font-weight:bold;font-size:14pt">ENJAZ</div>',
        css=css, archive=arch)
    page.insert_htmlbox(
        pymupdf.Rect(MARGIN_X, PAGE_H - FOOTER_H + 6, PAGE_W - MARGIN_X, PAGE_H - 6),
        f'<div dir="rtl" style="text-align:center;color:{MUTED};font-size:8pt">'
        f'صفحة {n} من {total} · إنجاز · {escape(stamp)}</div>',
        css=css, archive=arch)


def _rgb(hexcolor):
    h = hexcolor.lstrip('#')
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def render_survey_pdf(survey):
    """يعيد bytes لملف PDF الاستبيان المعبّأ."""
    blocks = build_blocks(survey)

    arch = pymupdf.Archive(str(FONT_DIR))
    for shape in ('radio', 'check'):
        for on in (0, 1):
            arch.add(_marker_png(shape, bool(on)), f'm_{shape}_{on}.png')
    arch.add(_bar_png(), 'bar.png')
    for n in range(1, 40):
        arch.add(_badge_png(n), f'n_{n}.png')
    for img in SURVEY_IMG_DIR.glob('*.jpg'):
        arch.add(img.read_bytes(), img.name)

    top = HEADER_H + 14
    bottom = PAGE_H - FOOTER_H - 4
    mediabox = pymupdf.Rect(0, 0, PAGE_W, PAGE_H)

    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    dev = writer.begin_page(mediabox)
    y = top

    def new_page():
        nonlocal dev, y
        writer.end_page()
        dev = writer.begin_page(mediabox)
        y = top

    for html in blocks:
        while True:
            story = pymupdf.Story(html=_rtl(html), user_css=CSS, archive=arch)
            more, filled = story.place(pymupdf.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, bottom))
            if more and y > top:
                new_page()          # لا يتسع في بقية الصفحة: يبدأ صفحة جديدة كاملاً
                continue
            story.draw(dev)
            y = filled[3] + 4
            while more:             # كتلة أطول من صفحة كاملة (نادر): تُكمل على صفحات
                new_page()
                more, filled = story.place(pymupdf.Rect(MARGIN_X, top, PAGE_W - MARGIN_X, bottom))
                story.draw(dev)
                y = filled[3] + 4
            break
    writer.end_page()
    writer.close()

    doc = pymupdf.open('pdf', buf.getvalue())
    title = f'{S.FORM_TITLES[survey.form_type]}'
    stamp = timezone.localtime(survey.submitted_at or timezone.now()).strftime('%Y/%m/%d')
    total = doc.page_count
    for i, page in enumerate(doc, start=1):
        _header_footer(page, title, i, total, stamp)
    doc.set_metadata({'title': f'{title} — {survey.client_name}', 'author': 'ENJAZ'})
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out
