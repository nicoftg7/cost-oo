"""La memoria de cada negocio: decisiones humanas guardadas en CSV.

Es el corazón del producto. Sin ellas el matcheo arranca de cero cada mes; con ellas
el segundo ciclo de un proveedor sale entero en verde. Son CSV a propósito: se abren
con Excel y cualquiera puede corregir una fila a mano.
"""
import os
import re
from pathlib import Path

import pandas as pd

from .normalize import expandir, normalizar
from .pais import IVA_OPCIONES, IVA_POR_PRODUCTO, tasas_de_opcion  # noqa: F401 (IVA_OPCIONES lo usa la web)

ESQUEMAS = {
    # cómo escribe cada proveedor -> referencia de Odoo
    "alias": ["texto", "referencia", "proveedor", "nota"],
    # apodos de proveedor ("La Quesera" = "Lácteos Don Julio")
    "proveedor_alias": ["alias", "proveedor"],
    # código propio de distribuidora -> referencia de Odoo. unidades: si la lista cotiza el
    # bulto cerrado ("HARINA 10 x 1 Kg"), por cuánto dividir para llegar al costo unitario
    "codigos_proveedor": ["proveedor", "codigo_proveedor", "descripcion_proveedor", "referencia_odoo", "unidades"],
    # lo que cotizan y el negocio no vende
    "ignorar": ["texto", "proveedor", "motivo"],
    # excepciones confirmadas: tipo = gramaje | presentacion | variacion
    "aprobados": ["referencia", "tipo", "motivo"],
    # variantes que el proveedor alterna (la que manda va publicada)
    "rotaciones": ["grupo", "referencia", "proveedor", "nota"],
    # quién cotiza por kilo, quién sin IVA
    "reglas_proveedor": ["proveedor", "cotiza_por", "iva", "nota"],
    # impuestos de las listas: ambito = proveedor | referencia. incluido = "si" cuando los
    # precios ya traen el IVA
    "impuestos": ["ambito", "clave", "iva", "percepcion", "nota", "incluido"],
    # cada costo que se mandó a Odoo, con de dónde salió. La última fila de cada
    # producto dice a quién se le compra ("fuente habitual").
    # estado = generado | verificado | no se aplicó
    "historial": ["fecha", "referencia", "producto", "costo_anterior", "costo_nuevo",
                  "fuente", "origen", "linea", "estado"],
    # mensajes y listas ya procesados: si vuelve el mismo texto, no es un precio nuevo
    "procesados": ["fuente", "huella", "fecha", "origen"],
    # los proveedores con los que se trabaja. tipo = mensaje | lista | manual.
    # iva_lista = una clave de IVA_OPCIONES (cómo vienen sus precios). alias separados por |
    # margen = fijo (el precio de venta sigue al costo) | variable (se revisa contra el precio actual)
    "proveedores": ["nombre", "alias", "tipo", "iva_lista", "margen", "nota"],
    # jerga y abreviaturas propias del negocio ("bahi" = "bahia"; significa vacío = borrar la palabra)
    "abreviaturas": ["texto", "significa", "nota"],
    # filas del export de Odoo que no son productos (cuotas, bonos, tareas internas)
    "no_son_productos": ["empieza_con", "nota"],
    # cómo trabaja el negocio. costo_sin_iva = si: en Odoo el costo va sin IVA
    "ajustes": ["clave", "valor"],
}


# En codigos_proveedor, una referencia vacía significa "este código no lo vendemos"
NO_LO_VENDEMOS = ""


class Memoria:
    """Carpeta con los CSV de ESQUEMAS. Si falta alguno se crea vacío con su encabezado."""

    def __init__(self, carpeta):
        self.carpeta = Path(carpeta)
        self.carpeta.mkdir(parents=True, exist_ok=True)
        for nombre, cols in ESQUEMAS.items():
            p = self.ruta(nombre)
            if not p.exists() or p.stat().st_size == 0:
                pd.DataFrame(columns=cols).to_csv(p, index=False, encoding="utf-8")

    def ruta(self, nombre):
        return self.carpeta / f"{nombre}.csv"

    def leer(self, nombre):
        try:
            df = pd.read_csv(self.ruta(nombre), dtype=str, keep_default_na=False,
                             encoding="utf-8-sig")
        except (FileNotFoundError, pd.errors.EmptyDataError):
            df = pd.DataFrame(columns=ESQUEMAS[nombre])
        for c in ESQUEMAS[nombre]:
            if c not in df:
                df[c] = ""
        return df.fillna("")

    def guardar(self, nombre, df):
        """Escribe de forma atómica: nunca queda un CSV a medio escribir."""
        p = self.ruta(nombre)
        tmp = p.with_suffix(".tmp")
        df.to_csv(tmp, index=False, encoding="utf-8")
        os.replace(tmp, p)

    def agregar(self, nombre, fila):
        df = self.leer(nombre)
        df = pd.concat([df, pd.DataFrame([fila])], ignore_index=True)
        self.guardar(nombre, df.reindex(columns=ESQUEMAS[nombre]).fillna(""))

    # ---- cargas ya preparadas para el matcheo ----
    def cargar_abreviaturas(self):
        """Suma la jerga propia del negocio a la genérica. Va antes de calcular claves."""
        from .normalize import cargar_abreviaturas
        ab = self.leer("abreviaturas")
        cargar_abreviaturas(zip(ab["texto"], ab["significa"]))

    def no_son_productos(self):
        return [t for t in self.leer("no_son_productos")["empieza_con"] if str(t).strip()]

    def alias(self):
        a = self.leer("alias")
        a["clave"] = a["texto"].map(lambda t: normalizar(expandir(t)))
        return a

    def ignorar(self):
        g = self.leer("ignorar")
        g["clave"] = g["texto"].map(lambda t: normalizar(expandir(t)))
        return g

    def impuestos(self):
        """por_producto: el IVA depende del producto (proveedor "mixto")."""
        imp = self.leer("impuestos")
        # antes "con IVA incluido" no dejaba regla (no había nada que sumar): se arma de la ficha
        tiene = set(imp.loc[imp["ambito"] == "proveedor", "clave"].map(normalizar))
        viejos = [{"ambito": "proveedor", "clave": r.nombre, "iva": f"{t[0]:g}" if isinstance(t[0], float) else t[0],
                   "percepcion": "0", "nota": "según la ficha del proveedor", "incluido": "si"}
                  for r in self.leer("proveedores").itertuples()
                  if str(r.iva_lista).startswith("incluido") and normalizar(r.nombre) not in tiene
                  and (t := tasas_de_opcion(r.iva_lista))]
        if viejos:
            imp = pd.concat([imp, pd.DataFrame(viejos)], ignore_index=True)
        # la regla vale también con los otros nombres del proveedor: la planilla dice "La
        # Quesera" pero el precio llega como "Lácteos Don Julio" (su nombre en Odoo)
        grupos = [[r.alias, r.proveedor] for r in self.leer("proveedor_alias").itertuples()]
        grupos += [[r.nombre] + [x.strip() for x in str(r.alias).split("|") if x.strip()]
                   for r in self.leer("proveedores").itertuples()]
        reglas = imp[imp["ambito"] == "proveedor"]
        tiene = set(reglas["clave"].map(normalizar))
        extra = []
        for g in grupos:
            r = next((r for r in reglas.itertuples() if normalizar(r.clave) in {normalizar(x) for x in g}), None)
            for otro in g if r is not None else []:
                if normalizar(otro) not in tiene:
                    extra.append({**r._asdict(), "clave": otro})
                    tiene.add(normalizar(otro))
        if extra:
            imp = pd.concat([imp, pd.DataFrame(extra).drop(columns="Index")], ignore_index=True)
        imp["por_producto"] = imp["iva"].eq(IVA_POR_PRODUCTO)
        imp["incluido"] = imp["incluido"].eq("si")
        for c in ("iva", "percepcion"):
            imp[c] = pd.to_numeric(imp[c], errors="coerce").fillna(0.0)
        return imp

    # ---- aprender de una decisión ----
    def aprender_alias(self, texto, referencia, proveedor, nota=""):
        """Guarda 'así escribe este proveedor este producto'. Si ya había un alias para el
        mismo texto y proveedor, lo reemplaza: la última decisión manda."""
        df = self.leer("alias")
        clave = normalizar(expandir(texto))
        mismo = (df["texto"].map(lambda t: normalizar(expandir(t))) == clave) & \
                (df["proveedor"].map(normalizar) == normalizar(proveedor))
        df = pd.concat([df[~mismo], pd.DataFrame([{
            "texto": texto, "referencia": referencia, "proveedor": proveedor, "nota": nota}])],
            ignore_index=True)
        self.guardar("alias", df)

    def aprender_ignorar(self, texto, proveedor, motivo):
        self.agregar("ignorar", {"texto": texto, "proveedor": proveedor, "motivo": motivo})

    def aprobar(self, referencia, tipo, motivo):
        df = self.leer("aprobados")
        if ((df["referencia"] == referencia) & (df["tipo"] == tipo)).any():
            return
        self.agregar("aprobados", {"referencia": referencia, "tipo": tipo, "motivo": motivo})

    def aprender_codigo(self, proveedor, codigo, descripcion, referencia, unidades=""):
        """Código propio de una distribuidora -> referencia de Odoo. Una vez mapeado no se
        vuelve a preguntar nunca. referencia vacía = no lo vendemos."""
        df = self.leer("codigos_proveedor")
        mismo = (df["proveedor"].map(normalizar) == normalizar(proveedor)) & \
                (df["codigo_proveedor"].map(clave_codigo) == clave_codigo(codigo))
        if not unidades and mismo.any():       # reconfirmar el producto no pierde lo del bulto
            unidades = df.loc[mismo, "unidades"].iloc[0]
        df = pd.concat([df[~mismo], pd.DataFrame([{
            "proveedor": proveedor, "codigo_proveedor": codigo,
            "descripcion_proveedor": descripcion, "referencia_odoo": referencia,
            "unidades": str(unidades or "")}])],
            ignore_index=True)
        self.guardar("codigos_proveedor", df)

    def codigos_de(self, proveedor):
        """{codigo normalizado: referencia} para una distribuidora."""
        df = self.leer("codigos_proveedor")
        df = df[df["proveedor"].map(normalizar) == normalizar(proveedor)]
        return {clave_codigo(c): str(r).strip() for c, r in zip(df.codigo_proveedor, df.referencia_odoo)}

    def unidades_de(self, proveedor):
        """{codigo normalizado: unidades por bulto} de los códigos que se cotizan por bulto."""
        df = self.leer("codigos_proveedor")
        df = df[df["proveedor"].map(normalizar) == normalizar(proveedor)]
        salida = {}
        for c, u in zip(df.codigo_proveedor, df.unidades):
            try:
                if int(float(u)) > 1:
                    salida[clave_codigo(c)] = int(float(u))
            except (TypeError, ValueError):
                pass
        return salida

    # ---- historial y fuentes ----
    def registrar_historial(self, filas):
        if not filas:
            return
        df = pd.concat([self.leer("historial"), pd.DataFrame(filas)], ignore_index=True)
        self.guardar("historial", df.reindex(columns=ESQUEMAS["historial"]).fillna(""))

    def fuentes_habituales(self):
        """{referencia: fuente} según el último costo que se mandó a Odoo de cada producto."""
        h = self.leer("historial")
        h = h[h["estado"] != "no se aplicó"]
        if not len(h):
            return {}
        return dict(zip(h["referencia"], h["fuente"]))

    def marcar_procesado(self, fuente, huella, origen, fecha):
        df = self.leer("procesados")
        if ((df["fuente"] == fuente) & (df["huella"] == huella)).any():
            return
        self.agregar("procesados", {"fuente": fuente, "huella": huella, "fecha": fecha, "origen": origen})

    def procesado(self, huella):
        """Fecha en que ya se procesó este texto/lista, o ''."""
        df = self.leer("procesados")
        f = df[df["huella"] == huella]
        return f.iloc[-1]["fecha"] if len(f) else ""

    # ---- proveedores ----
    def buscar_proveedor(self, texto):
        """Nombre guardado del proveedor ('distribuidora norte', 'DN', 'Distribuidora Norte S.A.'
        -> 'Distribuidora Norte'), o '' si es nuevo."""
        from rapidfuzz import fuzz
        k = normalizar(texto)
        if not k:
            return ""
        df = self.leer("proveedores")
        for nombre, alias in zip(df["nombre"], df["alias"]):
            claves = [normalizar(nombre)] + [normalizar(a) for a in str(alias).split("|") if a.strip()]
            if k in claves:
                return nombre
        for nombre in df["nombre"]:
            if fuzz.token_sort_ratio(k, normalizar(nombre)) >= 93:
                return nombre
        return ""

    def registrar_proveedor(self, texto, tipo="", nota=""):
        """Devuelve el nombre guardado; si es un proveedor nuevo, lo agrega."""
        texto = " ".join(str(texto).split())
        nombre = self.buscar_proveedor(texto)
        df = self.leer("proveedores")
        if nombre:
            if tipo:
                fila = df["nombre"] == nombre
                if not df.loc[fila, "tipo"].iloc[0]:
                    df.loc[fila, "tipo"] = tipo
                    self.guardar("proveedores", df)
            return nombre
        self.agregar("proveedores", {"nombre": texto, "alias": "", "tipo": tipo, "iva_lista": "",
                                     "margen": "fijo", "nota": nota})
        return texto

    def iva_de(self, proveedor):
        df = self.leer("proveedores")
        f = df[df["nombre"] == (self.buscar_proveedor(proveedor) or proveedor)]
        return f.iloc[0]["iva_lista"] if len(f) else ""

    def sabe_iva(self, proveedor):
        """Si ya se sabe cómo cotiza este proveedor (con o sin IVA), por su ficha o por impuestos.csv."""
        from .transform import impuesto_de
        return bool(self.iva_de(proveedor)) or impuesto_de([proveedor], "", self.impuestos()) is not None

    def fijar_iva(self, proveedor, valor):
        """Qué hay que sumarle a las listas de este proveedor. Se guarda en la ficha del
        proveedor y en impuestos.csv, que es lo que usa el cálculo."""
        if valor not in IVA_OPCIONES:
            return
        nombre = self.registrar_proveedor(proveedor)
        df = self.leer("proveedores")
        df.loc[df["nombre"] == nombre, "iva_lista"] = valor
        self.guardar("proveedores", df)
        imp = self.leer("impuestos")
        imp = imp[~((imp["ambito"] == "proveedor") & (imp["clave"].map(normalizar) == normalizar(nombre)))]
        tasas = tasas_de_opcion(valor)
        if tasas:
            imp = pd.concat([imp, pd.DataFrame([{
                "ambito": "proveedor", "clave": nombre, "iva": f"{tasas[0]:g}" if isinstance(tasas[0], float) else tasas[0],
                "percepcion": f"{tasas[1]:g}", "nota": "según la ficha del proveedor",
                "incluido": "si" if tasas[2] else ""}])],
                ignore_index=True)
        self.guardar("impuestos", imp)

    def fijar_iva_producto(self, referencia, iva, percepcion, nota=""):
        """El IVA de un producto puntual (la harina va al 10,5% aunque el resto de la lista vaya
        al 21%). Pisa cualquier regla del proveedor."""
        imp = self.leer("impuestos")
        imp = imp[~((imp["ambito"] == "referencia") & (imp["clave"] == referencia))]
        imp = pd.concat([imp, pd.DataFrame([{"ambito": "referencia", "clave": referencia, "iva": str(iva),
                                             "percepcion": str(percepcion), "nota": nota}])], ignore_index=True)
        self.guardar("impuestos", imp)

    def margen_variable(self):
        """Proveedores cuyo precio de venta no sigue al costo (se revisa contra el precio actual)."""
        df = self.leer("proveedores")
        return set(df.loc[df["margen"] == "variable", "nombre"])

    def nombres_proveedores(self):
        return sorted(self.leer("proveedores")["nombre"].tolist(), key=str.lower)

    def costo_sin_iva(self):
        """True si el negocio carga el costo en Odoo sin IVA (y Odoo lo suma después)."""
        a = self.leer("ajustes")
        return bool(len(a[(a["clave"] == "costo_sin_iva") & (a["valor"] == "si")]))

    def fijar_costo_sin_iva(self, sin_iva):
        a = self.leer("ajustes")
        a = pd.concat([a[a["clave"] != "costo_sin_iva"],
                       pd.DataFrame([{"clave": "costo_sin_iva", "valor": "si" if sin_iva else "no"}])],
                      ignore_index=True)
        self.guardar("ajustes", a)

    def conteos(self):
        return {n: len(self.leer(n)) for n in ESQUEMAS}


def clave_codigo(c):
    """'AL-0014' == 'al 0014' == 'AL0014'"""
    return re.sub(r"[^A-Z0-9]", "", str(c).upper())
