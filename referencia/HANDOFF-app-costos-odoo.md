# Handoff: de un toolkit casero a una app de actualización de costos para Odoo

Documento para arrancar el proyecto en Claude Code. Describe **qué se construyó, qué se
aprendió a los golpes, y qué hay que construir ahora** para que cualquier negocio con
Odoo pueda usarlo.

---

## 1. El problema

Un club de compras con ~500 productos en Odoo y ~140 proveedores actualiza costos cada
mes. Los proveedores mandan precios de seis formas distintas: mensajes de WhatsApp,
planillas, PDFs, tablas pegadas, capturas del sistema del proveedor, y listas mayoristas
de varias páginas. Nadie manda un CSV limpio.

Actualizarlo a mano son horas de buscar producto por producto, y los errores no se ven:
un costo mal cargado no rompe nada, solo hace que vendas con menos margen del que creés
durante un mes entero.

**Lo que se construyó** lee esos mensajes, los cruza contra el catálogo de Odoo y
devuelve un archivo listo para importar. En el último ciclo procesó **178 renglones de 30
proveedores** y dejó 77 costos listos con cero errores de matcheo.

---

## 2. Qué existe hoy (el prototipo)

Cuatro módulos Python, ~1.200 líneas, sin framework:

| Archivo | Qué hace |
|---|---|
| `catalogo.py` | Normaliza el export de Odoo a un catálogo consultable |
| `parser.py` | Lee los mensajes y los cruza contra el catálogo |
| `lista.py` | Listas de distribuidoras con código propio |
| `salidas.py` | Genera el archivo de importación y el reporte |

Dependencias: `pandas`, `rapidfuzz`, `openpyxl`, `pdfplumber`.

### Las ocho memorias

El prototipo aprende guardando decisiones humanas en CSVs. **Esto es el corazón del
producto**: sin las memorias el matcheo arranca de cero cada mes; con ellas, el segundo
ciclo de un proveedor sale entero en verde.

| Archivo | Para qué | Filas hoy |
|---|---|---|
| `alias.csv` | Cómo escribe cada proveedor → referencia de Odoo | 133 |
| `proveedor_alias.csv` | Apodos de proveedor | 4 |
| `codigos_proveedor.csv` | Código propio de distribuidora → referencia | 0 |
| `ignorar.csv` | Lo que cotizan y el negocio no vende | 33 |
| `aprobados.csv` | Excepciones confirmadas a mano | 16 |
| `rotaciones.csv` | Variantes que el proveedor alterna | 2 |
| `reglas_proveedor.csv` | Quién cotiza por kilo, quién sin IVA | 2 |
| `impuestos.csv` | Impuestos a sumar por proveedor o producto | 6 |

---

## 3. Lo que se aprendió a los golpes

Esta sección es la que más vale. Cada punto salió de un caso real, varios de bugs que
llegaron a producir archivos mal.

### 3.1 La regla que no se negocia: el ID externo

El archivo de importación tiene que llevar el **ID externo** de Odoo
(`__export__.product_template_...`) en la columna `id`. Con cualquier otra cosa, Odoo
**no actualiza: crea productos nuevos**, y eso no se deshace desde un archivo.

**El bug que lo enseñó:** la función que buscaba la columna de ID lo hacía por
coincidencia parcial de nombre. `"id"` está adentro de `"cantIDad a la mano"`. Tomó las
cantidades de stock como IDs, y Odoo creó 15 productos duplicados con los datos mezclados.

Dos controles obligatorios, ya implementados:
- Buscar la columna de ID **solo por nombre exacto**, y abortar si no está.
- Antes de escribir cualquier archivo, verificar que todos los `id` tengan forma de ID
  externo. Si alguno no la tiene, **no generar el archivo**.

### 3.2 Las dos trampas del fuzzy matching

Se usa `rapidfuzz`. El score es `0.55·token_set + 0.45·token_sort`.

**Trampa 1 — los alias se cruzan entre proveedores.** Sin acotar, el alias "Prepizzas"
de un proveedor se aplicaba a otro y le metía $5.700 donde iban $2.100. Los alias van
acotados a su proveedor; solo los que no tienen proveedor cargado valen para cualquiera.

**Trampa 2 — `token_set` da 100 cuando un texto es subconjunto de otro.** "c/miel" se
comía a "c/miel y cacao" y dejaba el mismo producto dos veces en el archivo con costos
distintos. "BONDIOLA" entraba por el alias "BONDIOLA FET". Solución: exigir además
`token_sort_ratio >= 85` sobre el texto completo, probando con y sin la cantidad de
compra al inicio (`"15 canelones"` contra el alias `"canelones"` da 84 y quedaba afuera
por dos puntos).

### 3.3 Normalización de unidades

Se normalizan los dos lados igual. Los casos que rompieron algo:

- `750 g` = `750g` = `0,75 kg`; litros y ml → cc; `gs` también es gramos
- `1/2 kg` son **500 g** — se leía como "2 kg"
- `24x300g` o `10 x 400g` = bulto de N unidades de M gramos → quedarse con el gramaje
- `x N` sin unidad detrás: **hasta 48 es cantidad de unidades** (`x 12` huevos), **de 100
  para arriba es gramaje** (`reggianito x 500` son 500 g, no 500 potes)
- `(12 UxB)` describe la caja, no el producto: se descarta
- Plurales recortados y abreviaturas expandidas (`AC`→aceite, `merme`→mermelada)

### 3.4 Estructuras de mensaje que hay que soportar

Todas salieron de mensajes reales:

- **Bloques por proveedor**: una línea suelta con el nombre abre el bloque. Descartar
  saludos y etiquetas **antes** de buscar proveedor, o "Sabores" matchea con el proveedor
  "Sabores de TraVajadoras".
- **Encabezado con precio + variantes indentadas debajo**: `Galletitas $2700` seguido de
  `Coco` / `Avena y naranja` son dos productos a $2700.
- **Segundo número entre paréntesis** = precio de venta sugerido: `$2700 (4000)`.
- **Tablas pegadas** con columna de marca: la marca de la fila **pisa** al proveedor del
  bloque, con vuelta atrás si esa marca no da buen match.
- **Líneas de factura del sistema del proveedor**: el costo real es **Importe/Cantidad**,
  no el precio unitario, que viene sin el descuento aplicado.
- **Varios productos con su precio en un mismo renglón**.
- **Cantidad de compra al inicio** (`20 merme de higos`).
- **Precio en la línea de abajo**.
- **`Sin salames`** = faltante, y devuelve *todos* los productos bajo ese término.
- **`Hamburguesas igual`** / **`viandas (mismo precio)`** = sin cambio, multi-producto.
- **`No modifiqué precios`** = el bloque entero sin cambios.
- Emojis, viñetas, saludos al inicio de una línea con precio, líneas cortadas a mitad.

### 3.5 Transformaciones de precio

No todos los proveedores cotizan lo mismo que se guarda en Odoo:

- **Por kilo**: regla de tres con el peso que sale del nombre del producto en Odoo.
- **Sin impuestos**: `precio × (1 + IVA) × (1 + percepción)`, con alícuotas por proveedor
  o por producto.
- **Con descuento por renglón**: Importe/Cantidad.

### 3.6 El truco de verificación

Cuando la lista trae columna de precio anterior, o es una factura: **reconstruir el costo
que ya está en Odoo**. Si da exacto, queda confirmada la transformación *y* qué renglón
corresponde a cada producto, que es la parte difícil.

Caso real: dividir el costo de Odoo por el peso del producto dio la columna ANTERIOR de
la lista con diferencia de un peso en los cinco productos. Eso confirmó de una que
"Salame picado grueso 300 g" se cotiza como salame **entero**, no feteado.

### 3.7 Las redes de seguridad

- Al import automático solo entra lo que matchea con score ≥88 y varía ≤30%.
- **Un mismo producto no puede entrar dos veces.** Odoo aplicaría el último sin avisar.
  Si dos renglones apuntan al mismo producto, gana el de más confianza.
- **Un cambio de presentación frena el costo.** "25 g contra 50 g" puede ser envase
  nuevo, pero "200 g contra 360 g" es otro producto. Solo se corrige lo aprobado.
- **Nada se despublica solo.** Los faltantes se avisan.
- Ninguna columna de inventario en los archivos: las cantidades nunca se tocan.

### 3.8 Lo que el sistema encontró sin que se lo pidieran

Vale como funcionalidad, no como anécdota: detectó un proveedor cargado con dos
escrituras distintas, productos colgando de la marca equivocada, presentaciones
desactualizadas, y costos que venían atrasados hacía meses. **La auditoría del catálogo
es un subproducto tan valioso como los costos.**

---

## 4. Qué construir

### Decisiones tomadas

1. **Conexión con Odoo: las dos, archivos por defecto.** El flujo de archivos funciona
   con cualquier Odoo sin credenciales. La API (XML-RPC) queda como modo avanzado.
2. **Interfaz: web local.** Se abre en el navegador, se arrastran los archivos, y los
   matcheos dudosos se confirman en pantalla. Tiene que servirle a alguien no técnico.
3. **Datos: anonimizados.** Se mantienen las estructuras descubiertas pero con nombres
   de proveedores y productos cambiados.

### Arquitectura sugerida

```
odoo-cost-updater/
├── core/           el motor, sin dependencias de interfaz
│   ├── catalog.py      normaliza el export
│   ├── normalize.py    unidades, abreviaturas, plurales
│   ├── parse.py        estructuras de mensaje
│   ├── match.py        alias → código → fuzzy
│   ├── transform.py    por kilo, impuestos, descuentos
│   └── output.py       archivo de importación + validaciones
├── memory/         las ocho memorias, con su esquema
├── odoo/           adaptadores: archivos (default) y XML-RPC
├── web/            interfaz local
├── examples/       catálogo y mensajes de ejemplo
└── tests/
```

**La pantalla de confirmación es el corazón del producto**, no un accesorio. Muestra
cada match dudoso con las tres mejores alternativas, el costo viejo y el nuevo, y la
variación; cada confirmación se escribe en `alias.csv`. Eso es lo que hace que el mes
siguiente salga solo.

### Tests que no pueden faltar

Uno por cada bug real de la sección 3:

1. Export sin columna `id` → aborta con mensaje claro
2. Columna `id` con valores que no son IDs externos → no genera el archivo
3. Alias de un proveedor no matchea con producto de otro
4. `"c/miel"` no matchea con `"c/miel y cacao"`
5. `"15 canelones"` sí matchea con el alias `"canelones"`
6. `1/2 kg` → 500 g
7. `x 12` → 12 unidades; `x 500` → 500 gramos
8. Dos renglones al mismo producto → solo entra el de más confianza
9. Precio por kilo × peso del nombre
10. Importe/Cantidad con descuento
11. Presentación distinta sin aprobar → frena el costo

### Roadmap

**v1 — el motor y la web**: lo de arriba, modo archivos, memorias en CSV.
**v2 — API de Odoo**: leer el catálogo y escribir costos directo.
**v3 — lo que el prototipo no llegó a hacer**: alta de productos nuevos, OCR para listas
escaneadas, historial de costos por proveedor, alertas de variación anómala contra el
promedio histórico.

---

## 5. Para el repo y para LinkedIn

**El README tiene que abrir con el problema, no con la instalación.** "Tu proveedor te
manda los precios por WhatsApp y vos los cargás a mano en Odoo" se entiende solo.

Incluir: un ejemplo de entrada real (anonimizado) y su salida, y el diagrama del flujo.

**Sugerencia**: escribir el README en inglés. El código y el producto se entienden en
cualquier idioma, y un repo en inglés llega mucho más lejos. Los ejemplos pueden quedar
en castellano, que además muestra que el matcheo funciona en un idioma con acentos y
abreviaturas.

**El ángulo para LinkedIn** no es "hice un script". Es esto: un proceso manual de horas
que ahora son minutos, construido sobre un problema que cualquiera con un ERP y
proveedores chicos reconoce al instante. Los números concretos del último ciclo —178
renglones, 30 proveedores, 6 formatos distintos de mensaje— dicen más que cualquier
adjetivo. Y la historia del bug del `"id"` adentro de `"cantidad"` es el tipo de detalle
que hace que un posteo técnico se lea completo.
