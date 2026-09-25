# Changelog

The story of this project, in order. Not a list of commits — a list of problems and what
solved them. Each entry exists because something real broke, was confusing, or was missing.

## Phase 0 — The problem

A buying club with ~500 products and ~140 small suppliers updates its costs in Odoo every
month. Suppliers send prices however they send them: WhatsApp messages, spreadsheets, PDF
price lists, pasted tables, photos of a handwritten flyer, screenshots of their own invoicing
system. Nobody sends a clean CSV. Someone read every message and typed every cost into Odoo
by hand. It took hours, and a wrong cost doesn't throw an error — it just quietly sells at a
worse margin for a month before anyone notices.

## Phase 1 — A CSV toolkit, built by hitting every wall by hand

The first version was four scripts and no interface: read a message, guess the product, write
a CSV. It worked, in the sense that costs got updated. Getting there taught most of the rules
the real app now runs on:

- **The rule that isn't negotiable: the external ID.** Odoo's import only updates existing
  products if the `id` column carries its *external* ID
  (`__export__.product_template_...`). Anything else and Odoo *creates new products* instead
  of updating them — and that doesn't undo itself from a file. The bug that taught this: the
  code that located the ID column matched by partial name. `"id"` is a substring of
  `"cant**id**ad a la mano"` (quantity on hand). It took stock counts as product IDs, and Odoo
  quietly created 15 duplicate products with mixed-up data. The fix has two parts, both now
  permanent: match the ID column by exact name only and abort if it's missing, and validate
  every row's ID looks like an external ID before any file gets written.
- **Fuzzy matching has two traps.** The matcher scores `0.55·token_set + 0.45·token_sort`
  with `rapidfuzz`. Unscoped, one supplier's alias for "Prepizzas" got applied to a different
  supplier's product and put $5,700 where $2,100 belonged — aliases now stay scoped to the
  supplier that taught them. Separately, `token_set_ratio` scores 100 whenever one string is
  a subset of another: `"c/miel"` matched `"c/miel y cacao"` and the same product entered the
  file twice with two different costs. A `token_sort_ratio >= 85` floor over the *whole*
  string fixed it, checked both with and without a leading purchase quantity (`"15 canelones"`
  against the alias `"canelones"` scores 84 without that — two points short).
- **Units lie in predictable ways.** `750 g` = `750g` = `0,75 kg`. `1/2 kg` is 500 g, not "2
  kg" (a naive parser reads the `2` and ignores the `1/2`). `24x300g` is a case of 24 units of
  300 g each — keep the 300 g, not the total. A bare `x N` is a unit count up to 48
  (`x 12` eggs) and a gram count from 100 up (`reggianito x 500` is 500 g, not 500 jars).
- **Invoice-style lines lie about the unit price.** The real cost is `Importe / Cantidad`; the
  printed unit price is what it cost *before* the line's own discount.
- **A safety net that has no exceptions:** the same product can never enter the import file
  twice (Odoo would silently keep the last one), and a change of pack size (25 g → 50 g) stops
  the cost cold until a human confirms it isn't a different product entirely.

By the last real cycle before the rewrite, this toolkit processed 178 lines from 30 suppliers
across 6 different message formats in one pass, reproducing what was already in Odoo with zero
matching errors on the confirmed rows. That result is *why* it was worth rebuilding properly
instead of patching scripts forever.

## Phase 2 — v1: turning the toolkit into an app

Rebuilt from scratch as a real package (`core/` for the engine, `web/` for a local Flask UI,
`tests/` with one test per bug above), with three decisions made up front:

- **Files first, API later.** The import/export flow works against any Odoo, with no
  credentials and no risk of a bad write. Direct API access is a later, optional mode.
- **A local web UI, not a CLI.** Drag files in, confirm doubtful matches on screen. It has to
  work for someone non-technical, not just for the person who built it.
- **Memory as CSV, not a database.** Every confirmed decision — an alias, a distributor code,
  an approved exception — gets written to a CSV anyone can open in Excel and correct by hand.
  There are eight of them; together they're the reason a supplier's second month runs itself.

The confirmation screen became the actual center of the product: every doubtful match shows
its best alternatives, the old cost, the new one, and *why* the app isn't sure — because the
whole point is that a human decides once and the app remembers.

## Phase 3 — Hardening the parser against real supplier lists

Five real distributor price lists, run through the parser one by one, found five different
ways a "simple" price list breaks a naive reader:

- A **PDF table** where several products shared one merged price cell across a row, so the
  reader needs to pick which column is the actual unit cost — not the case price, not the
  suggested retail price — by reading the table by column header, not by position.
- A **distributor whose own product codes are 1–2 digits.** A generic "codes are at least 3
  characters" rule silently dropped every one of them, plus a normalization bug that read
  "y queso" (from "RAVIOLES DE JAMON Y QUESO") as if it were a brand name.
- A list that repeats the same code for two different rows (previous price / current price /
  a percentage column), where the only way to tell which row is which product is by reading
  the description text, not the code.
- A **PDF with no product codes at all** — just prose, like a WhatsApp message pasted into a
  page layout. Handled by reusing the same "read as a loose message" path already built for
  actual messages, instead of writing a second parser.
- A **photographed price list.** Added OCR (`RapidOCR`, installed like any other Python
  package, no external program) that groups the detected text boxes back into the lines a
  human would read, corrects the classic "letter O read as a zero" mistake in prices, and
  feeds the result into the same text pipeline as a typed message.

All five now go through one entry point, `leer_fuente()`: try a structured table, then
lines with a product code, then plain text as a message — and if it's a photo or a scanned
page, read it as an image first. Nothing about the rest of the app had to change.

## Phase 4 — Two requests from actually using it

- **A stale-list warning.** A price list can carry an old date without anyone noticing —
  easy to do by accident when a supplier resends last quarter's PDF. Past 60 days, the cycle
  summary now flags it directly: *"this list says 2023-08-22, that's 25 months — is this the
  latest one?"*
- **Manual distributor-code entry.** Until now, a distributor code only got learned by
  confirming it on the review screen during a cycle. Added a small form in *Learned* to type
  in a code and its Odoo product ahead of time, for the case where you already know the
  mapping and don't want to wait for the next cycle to teach it.

## Phase 5 — Giving it a face

The app worked; it looked like an internal tool nobody designed on purpose. Went through
three passes on the way to something that reads as a finished product rather than a script
with a UI bolted on:

1. A first pass toward a warm palette (cream background, terracotta accent) — reverted after
   testing, because warm tones read as *relaxed*, which is the wrong feeling for a tool meant
   for someone mid-shift with a dozen things open.
2. Rebuilt around Odoo's own brand violet (`#714B67`) instead: one hue driving buttons, links,
   focus states and hover — with the semantic colors (green/amber/red for done/needs-review/
   increased) kept deliberately outside that family, since those need to read as *different*
   at a glance, not as more violet.
3. A small wordmark, "Cost-oo", with the first "o" replaced by a reload icon in that same
   violet — three rounds of fixing it: swapping an emoji-based icon for a hand-coded SVG once
   the emoji rendered inconsistently across platforms, then re-cropping that SVG's own
   viewBox once it turned out the icon file had built-in padding that made it look larger
   than the letters around it.

Every screen also got a light pass of single, deliberate emoji at section headers and action
buttons — a scanning aid for someone moving fast, not decoration.

## Phase 6 — What a second business found in one afternoon

Everything so far had been tested against one real business's data. Setting up a second,
unrelated one — same distributor, completely different product catalog — broke three things
in an afternoon that a year of using it on a single business never surfaced:

- **A PDF whose own header column was called "ID Artículo".** The word "Artículo" also
  matches the pattern used to recognize a *description* column, so that column got claimed
  first — leaving the actual "Descripción" column unread and the article's numeric code
  sitting where the product name should be. Every fuzzy match against it failed, silently:
  there was no name left to compare. Column detection now checks for a code-like header
  (including a bare "id") before it checks for a description-like one.
- **A first list from a first supplier could never match anything, on purpose but wrong.**
  Name-matching was scoped to "brands already bought from this distributor" — a deliberate
  choice to avoid cross-brand false positives. For a business with history, that's a
  reasonable narrowing. For a *brand-new* pairing of business and distributor, that set is
  always empty, so the search space was empty too: not "no good match," but "no search at
  all." It worked on the original business only because months of confirmed codes and
  purchase history had already populated that scope. Now an empty scope falls back to
  searching the whole catalog instead of nothing.
- **A price typed with no space before it.** `"harina2000"` doesn't look like a price to a
  parser that expects a space, a `$`, or the word `pesos` before the digits — so the line
  was treated as a variant of the *previous* line, and `"aceite 2500"` / `"harina2000"` merged
  into one entry priced at 2500. The fallback price pattern now also accepts a price glued
  directly onto the end of a word.

None of these were found by more testing on the same data — they needed a genuinely
different catalog and a genuinely fresh distributor relationship to show up. Each got a
regression test the same day.

## Phase 7 — Stress-testing with businesses that don't exist

Everything up to here was tested against real data from one real business. This round did
the opposite on purpose: four small, fictional businesses (a hardware store, a kiosk, a
health-food shop, a bakery-supplies store) with invented catalogs, invented WhatsApp-style
messages full of typos, missing accents, glued-together words, and mixed capitalization, and
distributor lists with deliberately broken formatting (no product code column, the same code
reused for two different products, no header row at all, empty filler columns, inconsistent
whitespace). None of it touches a real supplier or a real price.

Nothing crashed — every malformed input produced either a sensible result or a clear,
controlled error. But five real gaps surfaced:

- **A line with no recognizable price used to vanish with no trace at all** — not as a
  product, not as a warning, nothing. `"semillas de chia•250g•2000"` (an odd separator
  instead of spaces) just disappeared. Now, a line with digits that doesn't match any known
  pattern becomes a note instead of silently dropping — the same principle the rest of the
  app already follows ("nothing gets lost quietly"), extended to a gap it didn't cover.
- **A distributor list with no header row lost its first product entirely**, because pandas
  always treats the first row as column names — so that row (a real product) became "column
  labels" instead of data, and the products after it lost their codes too (the columns ended
  up named after that row's own values, which don't look like "código" or "descripción" to
  anything). Detected with a simple, reliable signal: a real header is never itself a valid
  number, so if the column picked as "price" is literally named e.g. `"1550"`, that row gets
  put back as data before reading it again.
- **"no hay stock" — the single most common way to say something ran out — was being read as
  if it meant the opposite.** A rule discards "hay stock" / "con stock" as noise (that phrase
  by itself just means "there's stock," worth ignoring). It didn't account for a preceding
  negation, so "no hay stock" lost exactly the two words that mattered, and the missing-item
  warning silently vanished with it.
- As a related fix, a product named on one line with the stock warning on the *next* line
  (a common two-line WhatsApp habit) now carries the product's name into the warning, instead
  of reporting a warning with no product attached.

Two things were tested and found to already be handled correctly, and left alone: a
distributor code reused for two genuinely different products (the code gets dropped and both
rows fall back to name matching, rather than risking a wrong assignment), and a first list
from a first-ever distributor relationship (fixed in Phase 6; confirmed still solid here
against an unrelated catalog).

## Phase 8 — Real distributor lists, still no real business attached

A batch of real price lists from real distributors (kept out of the repo — this is exactly
the sensitive data Phase 6 removed) went through the reader with no real business's catalog
behind them, purely to see how the parser itself holds up. Nothing crashed across sixteen
files of wildly different layouts, but two produced a wrong number, which is a different
and worse category than producing no number:

- **A price got read as the wrong number entirely on a distributor list with no real table
  columns** (the whole row lands in a single cell, e.g. `"3 TAPAS P/EMP. FREIR x 12 un.
  $723,00"`). The cell-price reader took the *first* number it found — `12`, from the pack
  quantity — instead of the actual price. It now prefers a number with a `$` in front of it,
  falling back to the first bare number only when nothing has one.
- **A price came out as a product's own internal ID.** A title row ("OBSERVACIONES:") sitting
  above the real header made the spreadsheet library treat *that* row as the column names,
  which pushed the real header ("ID Artículo", "Descripción", "Precio...") down into the
  first row of data. Both the ID and the price are numeric, and column detection guessed
  wrong between them. Fixed by scanning the first few rows for one that matches known column
  names better than the current header does, and promoting it if so.

**One more was found, understood, and deliberately left unfixed.** A wholesaler's price list
put a five-digit price in the right half of the page; the reader's two-column detection (built
to split genuinely two-column pages, and already working correctly on other real lists with
over a thousand lines) mistook that price for the start of a second column of product codes,
purely because it happened to be a plain run of digits sitting past the page's midpoint. The
page then got sliced in half through the middle of the price itself — `15713` became `15`.
The honest fix touches the same column-splitting logic that several other real lists already
depend on working correctly, and verifying it wouldn't quietly break one of those needs more
time than this batch had. Documented here instead of patched in a hurry.

## Roadmap

- **v2:** an Odoo XML-RPC adapter to read the catalog and write costs directly (the
  file-based flow stays the default — it needs no credentials and can't write anything by
  accident).
- **v3:** new-product creation from an unmatched line, and alerts against the historical
  average cost per product (the cost history needed for that is already being recorded).
- Packaging the Windows launcher as a single `.exe` so a non-technical user never has to see
  Python at all.
- Make the two-column PDF detection word-aware (it currently decides and cuts at the
  character level) so a price that happens to sit past the page's midpoint can't be
  mistaken for the start of a second column (Phase 8).
