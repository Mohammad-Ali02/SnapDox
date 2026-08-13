# SnapDox

Convert documents and images on your own machine. No uploads, no page limits, no watermarks —
the same thing CloudConvert and Smallpdf do, except your files never leave the computer.

There are two front-ends over one conversion core: a drag-and-drop web UI, and a CLI.

```bash
python serve.py
```

Then open <http://127.0.0.1:5000>.

```bash
snapdox report.docx report.pdf           # explicit output path
snapdox report.docx --to pdf             # output beside the input
snapdox *.docx --to pdf --outdir built   # batch
snapdox scan.pdf --to png --dpi 300      # one PNG per page
snapdox logo.png --to svg                # real vector tracing
snapdox --list                           # everything that can become everything
```

## What it converts

| From | To | How |
|---|---|---|
| Word, Excel, PowerPoint, ODF, RTF, TXT, HTML | **PDF** | LibreOffice |
| Between Office formats (docx↔odt, xlsx↔csv, pptx↔odp) | | LibreOffice |
| **PDF → Word** | | pdf2docx, three layout modes (below) |
| PDF | PNG, JPEG, TIFF, WebP, TXT, HTML | PyMuPDF |
| Images | PDF, Word | PyMuPDF, python-docx |
| PNG/JPEG/BMP | **SVG** | vtracer — traced paths, not an embedded bitmap |
| SVG | PDF, PNG, JPEG | svglib, staying vector until the final rasterize |
| Markdown, EPUB | Word, HTML, ODT, RTF, TXT | Pandoc |

Pairs with no direct converter are chained automatically through PDF, Word, PNG or HTML — that's
where `pptx → png` and `epub → pdf` come from. Chains are capped at two hops so quality never
degrades through a long series of lossy steps. `snapdox --targets docx` shows the routes for one
format.

## PDF to Word

This is the conversion people care about most and the one with real trade-offs, so it has a
`--pdf-layout` choice (a dropdown in the web UI):

- **`flow`** *(default)* — paragraphs, images and genuinely bordered tables. Best for almost
  everything.
- **`tables`** — additionally guesses at borderless tables. Use it for invoices, statements and
  datasheets, where the grid *is* the content. Avoid it otherwise: on an ordinary two-column
  document it reads the columns as table cells and shreds the paragraphs across them.
- **`text`** — plain text in correct reading order, layout discarded. Nothing fights you when you
  edit it. Columns are detected and read down one side before the other, rather than zig-zagging
  between them.

Measured on a real two-column, seven-page test paper: `flow` recovers 97% of source lines in 3.6s
with no phantom tables; `text` recovers 100% in 0.6s; `tables` recovers 96% in 26s and produces 14
spurious tables. Hence the default.

**Scanned PDFs are refused, deliberately.** A scan has no text layer, so there is nothing to turn
into an editable document — SnapDox says so instead of handing back a Word file containing one
giant picture of a page. Convert it to images instead, or OCR it first.

## Requirements

Python 3.10+, plus two external programs found automatically on the usual install paths:

| Program | Needed for | Override |
|---|---|---|
| [LibreOffice](https://libreoffice.org) | Office formats | `SNAPDOX_SOFFICE` |
| [Pandoc](https://pandoc.org) | Markdown, EPUB | `SNAPDOX_PANDOC` |

Everything else installs from PyPI:

```bash
pip install -e .
```

If a program is missing, only the conversions that need it are affected, and the error says which
one to install.

## Notes on the internals

**Converters are data, not `if`-statements.** Each one registers itself in `snapdox/registry.py`
as an edge `(source → target)`. The CLI's `--list` output and the web UI's dropdown are both
generated from that table, so neither can drift from what the engines actually implement.

**LibreOffice profiles are pooled, not created per job.** Building a fresh profile costs ~15s; a
warm one converts in under 3. Each job still gets an *isolated* profile from the pool, because
otherwise a LibreOffice window already open on your desktop silently swallows the request and
reports success having converted nothing.

**Image sources are never routed into text extraction.** `png → pdf → docx` would be a valid path
through the graph and would fail every single time, since a picture has no text to recover. Those
edges are flagged so the router won't offer them, and a genuine image-into-Word converter handles
the case properly.

**Compiled engines can raise `BaseException`.** vtracer is Rust; its panics derive from
`BaseException`, so an `except Exception` lets them through — which killed the worker thread and
left web jobs polling "running" forever. Both the engine and the job runner catch deliberately
broadly, and there's a regression test for it.

## Testing

```bash
python -m pytest tests -q
```

Fixtures are generated at session start rather than committed, so there are no binary blobs in the
repo and every fixture's content is visible in `tests/conftest.py`. The suite asserts on what comes
out — that text survives a round trip, that a traced SVG contains real `<path>` elements, that
columns come back in reading order — plus the failure paths: scanned PDFs, encrypted PDFs, bad page
ranges, unsupported pairs, and hostile upload filenames.

## Layout

```
snapdox/
  formats.py     the format table; aliases fold onto canonical names here
  registry.py    the conversion graph and its router
  pipeline.py    convert() — resolves a route, runs it, cleans up intermediates
  options.py     per-conversion settings
  errors.py      one class per failure a user can actually cause
  engines/       libreoffice, pdf-to-word, pdf, raster, vector, pandoc
  cli.py
web/
  app.py         Flask routes
  jobs.py        thread pool, job store, TTL sweeper
tests/
```

The web UI binds to `127.0.0.1` and has no authentication — it assumes it's yours alone. Serving it
on another address prints a warning saying so.
