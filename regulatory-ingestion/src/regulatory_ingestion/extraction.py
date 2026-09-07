from io import BytesIO

from bs4 import BeautifulSoup
from pypdf import PdfReader


class ContentExtractor:
    def extract(self, body: bytes, content_type: str, url: str) -> str:
        is_pdf = body.lstrip().startswith(b"%PDF-")
        if is_pdf:
            reader = PdfReader(BytesIO(body))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
        soup = BeautifulSoup(body, "html.parser")
        for node in soup.select("script, style, nav, footer, header"):
            node.decompose()
        return "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
