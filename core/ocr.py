"""Leer el texto de una foto (o de un PDF escaneado) de una lista de precios.

Usa RapidOCR, que se instala como cualquier librería de Python (sin programas aparte).
Las cajas de texto que devuelve se arman en renglones por su altura en la imagen, así
"CACAO & ZANAHORIA ........ $1800" queda en una sola línea, como la escribió el proveedor.
"""
import re

from . import ErrorDeDatos

_motor = None


def _ocr():
    global _motor
    if _motor is None:
        try:
            # "rapidocr", no el viejo "rapidocr_onnxruntime": ese no se instala en Python 3.13
            # o más nuevo, y la instalación entera fallaba para quien bajaba Python hoy
            from rapidocr import RapidOCR
        except ImportError:
            raise ErrorDeDatos("Para leer fotos falta instalar el lector de imágenes. Cerrá la app y "
                               "volvé a abrirla con \"Abrir actualizador\": se instala solo.")
        _motor = RapidOCR()
    return _motor


def _renglones(cajas):
    """[(caja, texto, confianza)] -> líneas de texto en el orden en que se leen."""
    items = []
    for caja, texto, conf in cajas or []:
        ys = [p[1] for p in caja]
        items.append({"y": sum(ys) / 4, "alto": max(ys) - min(ys), "x": min(p[0] for p in caja), "t": texto})
    items.sort(key=lambda i: i["y"])
    lineas, actual = [], []
    for it in items:
        if actual and abs(it["y"] - actual[0]["y"]) > max(actual[0]["alto"], it["alto"]) * 0.6:
            lineas.append(actual)
            actual = []
        actual.append(it)
    if actual:
        lineas.append(actual)
    return ["  ".join(i["t"] for i in sorted(l, key=lambda i: i["x"])) for l in lineas]


def corregir(linea):
    """Errores típicos de lectura en precios: la letra O por el cero ("$2OOO", "$1500O")."""
    return re.sub(r"\$\s*[\dOo][\dOo.,]*", lambda m: m.group(0).replace("O", "0").replace("o", "0"), linea)


def _leer(imagen):
    r = _ocr()(imagen)
    cajas = [] if r.boxes is None else list(zip(r.boxes, r.txts, r.scores))
    return "\n".join(corregir(l) for l in _renglones(cajas))


def texto_de_imagen(contenido):
    import numpy as np
    try:
        import cv2
        imagen = cv2.imdecode(np.frombuffer(contenido, np.uint8), cv2.IMREAD_COLOR)
    except ImportError:
        imagen = contenido
    if imagen is None:
        raise ErrorDeDatos("No pude abrir la imagen. Tiene que ser una foto (JPG o PNG).")
    return _leer(imagen)


def texto_de_pdf_escaneado(pdf):
    """Un PDF que es una foto por página: se lee cada página como imagen."""
    import numpy as np
    textos = []
    for pg in pdf.pages:
        imagen = np.array(pg.to_image(resolution=200).original.convert("RGB"))[:, :, ::-1]
        textos.append(_leer(imagen))
    return "\n".join(textos)
