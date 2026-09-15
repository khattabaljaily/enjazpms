"""
PDF/image extraction for supplier price lists — per the product owner, most
real-world price lists arrive as PDF (sometimes text-based/exported from
Excel or Word, sometimes a scanned/photographed paper list) and occasionally
as a plain photo, with Excel/CSV being the rare case. Uses local, free tools
only (PyMuPDF + Tesseract OCR) — no paid vision API, per explicit product
decision (accuracy on messy scans/handwriting will be lower than a vision
LLM would give; the review step in apps.catalog.ingest exists specifically
to catch and correct what OCR gets wrong).

Output shape matches apps.core.io_utils.parse_uploaded_file: (rows, error).
When PyMuPDF detects a real table on a text-based PDF page, rows come back
as proper header-keyed dicts (same shape as the Excel path). When there's no
detectable table (a scanned page after OCR, or a PDF page with free-flowing
text), each row is just {'text': <one line>} — apps.ai.services.
extract_drug_rows_batch already knows how to infer name/generic_name/
strength/dosage_form from free text (built for exactly this: price lists
that only have a name column), so this still feeds the same pipeline.
"""
import io
import logging

logger = logging.getLogger('data_import')

PDF_EXTENSIONS = ('.pdf',)
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.tif')

MIN_TEXT_LEN_PER_PAGE = 20  # below this, treat the PDF page as scanned/image-only and OCR it
OCR_LANGS = 'ara+eng'
OCR_CONFIG = '--psm 6'  # assume a uniform block of text (works better for tabular scans than default)
OCR_DPI = 300


def is_pdf_or_image(filename: str) -> bool:
    name = filename.lower()
    return name.endswith(PDF_EXTENSIONS) or name.endswith(IMAGE_EXTENSIONS)


def parse_pdf_or_image(file) -> tuple[list[dict], str | None]:
    name = file.name.lower()
    try:
        content = file.read()
        if name.endswith(PDF_EXTENSIONS):
            rows = _parse_pdf(content)
        elif name.endswith(IMAGE_EXTENSIONS):
            rows = _lines_to_rows(_ocr_image_bytes(content))
        else:
            return [], 'نوع الملف غير مدعوم. الرجاء رفع PDF أو صورة أو Excel/CSV'
    except Exception as exc:
        logger.error('parse_pdf_or_image failed: %s', exc, exc_info=True)
        return [], f'تعذّر قراءة الملف: {exc}'

    if not rows:
        return [], 'تعذّر استخراج أي نص أو بيانات من الملف'
    return rows, None


def _parse_pdf(content: bytes) -> list[dict]:
    import pymupdf

    rows = []
    doc = pymupdf.open(stream=content, filetype='pdf')
    try:
        for page in doc:
            text = page.get_text().strip()
            if len(text) >= MIN_TEXT_LEN_PER_PAGE:
                tables = page.find_tables()
                table_rows = []
                for table in tables.tables:
                    table_rows.extend(_table_to_rows(table.extract()))
                if table_rows:
                    rows.extend(table_rows)
                else:
                    rows.extend(_lines_to_rows(text))
            else:
                # little/no extractable text — this page is a scan/image, OCR it
                pix = page.get_pixmap(dpi=OCR_DPI)
                rows.extend(_lines_to_rows(_ocr_image_bytes(pix.tobytes('png'))))
    finally:
        doc.close()
    return rows


def _table_to_rows(table_data: list) -> list:
    """table_data: rows of cell strings from PyMuPDF's find_tables(), first row = header."""
    if not table_data or len(table_data) < 2:
        return []
    headers = [str(h).strip() if h else f'عمود_{i + 1}' for i, h in enumerate(table_data[0])]
    rows = []
    for raw_row in table_data[1:]:
        row = {
            headers[i]: (str(cell).strip() if cell is not None else '')
            for i, cell in enumerate(raw_row) if i < len(headers)
        }
        if any(row.values()):
            rows.append(row)
    return rows


def _ocr_image_bytes(img_bytes: bytes) -> str:
    import pytesseract
    from PIL import Image

    image = Image.open(io.BytesIO(img_bytes))
    return pytesseract.image_to_string(image, lang=OCR_LANGS, config=OCR_CONFIG)


def _lines_to_rows(text: str) -> list:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if len(line) >= 2:
            rows.append({'text': line})
    return rows
