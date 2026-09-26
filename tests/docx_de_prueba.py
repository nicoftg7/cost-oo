"""Arma un .docx mínimo (sin depender de python-docx) para las pruebas."""
import io
import zipfile
from xml.sax.saxutils import escape

TIPOS = ('<?xml version="1.0" encoding="UTF-8"?>'
         '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
         '<Default Extension="xml" ContentType="application/xml"/>'
         '<Default Extension="jpeg" ContentType="image/jpeg"/>'
         '<Override PartName="/word/document.xml" '
         'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
         '</Types>')
RELS = ('<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>')


def _p(texto):
    return f'<w:p><w:r><w:t xml:space="preserve">{escape(texto)}</w:t></w:r></w:p>'


def _tabla(filas):
    celdas = lambda f: "".join(f"<w:tc>{_p(c)}</w:tc>" for c in f)
    return "<w:tbl>" + "".join(f"<w:tr>{celdas(f)}</w:tr>" for f in filas) + "</w:tbl>"


def docx(*bloques, fotos=()):
    """bloques: textos (párrafos) o listas de filas (tablas). fotos: bytes JPEG pegados."""
    cuerpo = "".join(_tabla(b) if isinstance(b, list) else _p(b) for b in bloques)
    doc = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f'<w:body>{cuerpo}</w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", TIPOS)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/document.xml", doc)
        for i, f in enumerate(fotos, 1):
            z.writestr(f"word/media/image{i}.jpeg", f)
    return buf.getvalue()
