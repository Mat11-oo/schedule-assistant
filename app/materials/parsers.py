"""Bounded local extraction/rendering; cloud OCR and ASR are injected by the worker."""
import html
import math
import os
import wave
from html.parser import HTMLParser
from pathlib import Path


def split_text(text, locator, maximum=4000):
    text = text.strip()
    result = []
    offset = 0
    while offset < len(text):
        end = min(offset + maximum, len(text))
        if end < len(text):
            cut = max(text.rfind("\n", offset + maximum // 2, end), text.rfind("。", offset + maximum // 2, end), text.rfind(". ", offset + maximum // 2, end))
            if cut >= offset + maximum // 2:
                end = cut + 1
        piece = text[offset:end]
        if piece.strip():
            result.append({"text": piece, "locator": {**locator, "text_offset": offset}})
        offset = end
    return result


def pdf_pages(path):
    import pdfplumber
    limit = int(os.getenv("MATERIAL_MAX_PAGES", "300"))
    try:
        document = pdfplumber.open(path)
    except Exception:
        raise ValueError("PDF 损坏、加密或无法打开；请先在本机解密或重新导出") from None
    with document:
        count = len(document.pages)
        if not 1 <= count <= limit:
            raise ValueError(f"PDF 页数需在 1 至 {limit} 页之间")
        for index, page in enumerate(document.pages):
            if not math.isfinite(page.width * page.height) or page.width <= 0 or page.height <= 0 or page.width * page.height > 40_000_000:
                raise ValueError("PDF 页面尺寸异常")
            try:
                text = page.extract_text(layout=False) or ""
                tables = []
                for table in page.find_tables():
                    rows = table.extract()
                    if len(rows) > 500 or any(len(r) > 100 for r in rows):
                        raise ValueError("表格过大，请拆分文件")
                    tables.append({"rows": [["" if cell is None else str(cell) for cell in row] for row in rows], "bbox": list(table.bbox)})
                image_area = sum(max(0, im.get("x1", 0) - im.get("x0", 0)) * max(0, im.get("bottom", 0) - im.get("top", 0)) for im in page.images)
                need_ocr = len(text.strip()) < 30 or image_area > page.width * page.height * .3
                yield {"page": index + 1, "total": count, "text": text, "tables": tables,
                       "need_ocr": need_ocr, "blank": not text.strip() and not page.images, "error": ""}
            except ValueError:
                raise
            except Exception:
                yield {"page": index + 1, "total": count, "text": "", "tables": [], "need_ocr": True,
                       "blank": False, "error": "原生提取失败，需 OCR"}


def render_page(path, page_number, output):
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(path) as doc:
        if not 1 <= page_number <= len(doc):
            raise ValueError("页码不存在")
        page = doc[page_number - 1]
        width, height = page.get_size()
        scale = min(2.0, math.sqrt(6_000_000 / max(width * height, 1)))
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil()
        image.save(output, "PNG")
        image.close(); bitmap.close(); page.close()
    return Path(output)


def audio_chunks(path, directory):
    """Decode with bundled PyAV codecs; cap duration and bound each cloud request.

    Speaker labels are scoped per chunk because providers cannot guarantee stable
    identities between separate calls. User mapping can unify them afterwards.
    """
    import av
    import numpy as np
    rate, chunk_seconds = 16000, 300
    limit = int(os.getenv("MATERIAL_MAX_AUDIO_SECONDS", "7200"))
    samples, total, part = [], 0, 0
    buffered = 0
    try:
        container = av.open(str(path))
    except Exception:
        raise ValueError("录音格式无效或损坏，请重新导出音频") from None
    with container:
        if not container.streams.audio:
            raise ValueError("文件不包含可解码音轨")
        if container.duration and container.duration / av.time_base > limit + 1:
            raise ValueError(f"录音超过 {limit // 60} 分钟限制，请先拆分")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        def write_chunk(array, index, offset):
            target = Path(directory) / f"part-{index:04d}.wav"
            with wave.open(str(target), "wb") as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(rate)
                output.writeframes(array.astype("<i2", copy=False).tobytes())
            return {"path": target, "index": index, "offset": offset, "duration": len(array) / rate}
        for frame in container.decode(audio=0):
            for decoded in resampler.resample(frame):
                value = decoded.to_ndarray().reshape(-1)
                samples.append(value); buffered += len(value); total += len(value)
                if total > (limit + 1) * rate:
                    raise ValueError(f"录音超过 {limit // 60} 分钟限制，请先拆分")
                if buffered >= rate * chunk_seconds:
                    merged = np.concatenate(samples)
                    keep = rate * chunk_seconds
                    part += 1
                    yield write_chunk(merged[:keep], part, (part - 1) * chunk_seconds)
                    tail = merged[keep:]
                    samples, buffered = [tail], len(tail)
        for frame in resampler.resample(None):
            value = frame.to_ndarray().reshape(-1); samples.append(value); buffered += len(value); total += len(value)
        if buffered:
            part += 1
            yield write_chunk(np.concatenate(samples), part, (part - 1) * chunk_seconds)
        if not part:
            raise ValueError("录音没有有效音频")


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.hidden = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1
        if not self.hidden and tag in {"p", "div", "h1", "h2", "h3", "li", "tr", "br"}:
            self.blocks.append("\n")
    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in {"p", "div", "li", "tr"}:
            self.blocks.append("\n")
    def handle_data(self, data):
        if not self.hidden:
            self.blocks.append(data)


def fetch_web(url, user_id):
    from app.crawl.router import CrawlRouter
    from app.crawl.http import Fetcher
    from app.crawl.policy import get_policy
    router = CrawlRouter()
    policy = get_policy("web.trafilatura", router.config)
    if not policy.enabled:
        raise ValueError("网页读取已被运营配置关闭")
    response = Fetcher(router, "web.trafilatura", policy, user_id).get(url)
    if response.status_code != 200:
        raise ValueError("公开网页无法读取；请上传文件或粘贴正文")
    if response.content.startswith(b"%PDF-"):
        raise ValueError("此链接指向 PDF，请下载后通过 PDF 上传入口处理")
    parser = PageText(); parser.feed(response.text)
    text = "\n".join(line.strip() for line in "".join(parser.blocks).splitlines() if line.strip())
    if not text:
        raise ValueError("网页没有可读正文；动态或受限页面可粘贴正文")
    return split_text(text, {"url": url, "kind": "web"})


def text_document(path, extension):
    """Bounded local extraction of plain text and Word paragraphs/tables."""
    if extension == 'docx':
        import zipfile
        from docx import Document
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                if len(entries) > 3000 or sum(item.file_size for item in entries) > 50 * 1024 * 1024:
                    raise ValueError('DOCX 解压后过大，请拆分')
                if 'word/document.xml' not in archive.namelist():
                    raise ValueError('文件不是有效 DOCX')
            document = Document(path)
            paragraphs = []
            from docx.table import Table
            from docx.text.paragraph import Paragraph
            for block in document.iter_inner_content():
                if isinstance(block, Paragraph):
                    paragraphs.append(block.text)
                elif isinstance(block, Table):
                    paragraphs.append('\n'.join('\t'.join(cell.text for cell in row.cells) for row in block.rows))
            text = '\n\n'.join(paragraphs)
        except ValueError:
            raise
        except Exception:
            raise ValueError('DOCX 损坏、加密或无法读取，请重新导出') from None
    else:
        raw = Path(path).read_bytes()
        if len(raw) > 10 * 1024 * 1024:
            raise ValueError('文本文件最多10 MB')
        encodings = ['utf-16'] if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'gb18030']
        text = None
        for encoding in encodings:
            try:
                text = raw.decode(encoding)
                break
            except UnicodeError:
                continue
        if text is None or '\0' in text:
            raise ValueError('文本编码无法识别，请保存为 UTF-8 后上传')
    if len(text) > 1000000:
        raise ValueError('正文超过100万字，请拆分文档')
    if not text.strip():
        raise ValueError('文档没有可提取的正文')
    return split_text(text, {'format': extension, 'section': '正文'})
