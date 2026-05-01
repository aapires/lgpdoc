# Local Setup — OpenAI Privacy Filter Integration

## Requirements

- Python 3.11+
- Git
- 4 GB free disk space (model checkpoint)
- GPU optional — CPU works, but is slower (~200-400 tokens/s vs 1500+ on CUDA)

---

## 1. Create and activate the virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

---

## 2. Install the base package and dev tools

```bash
pip install -e ".[dev]"
```

This installs the `anonymizer` package in editable mode plus `pytest` and `pytest-cov`.
No model is downloaded at this step — you can run all unit tests without a GPU.

---

## 3. Install the ML stack (real model)

```bash
.venv/bin/pip install -e ".[ml]"
```

This pulls in `opf` (from GitHub), `torch`, and `transformers>=4.50`.
The model checkpoint (~2.6 GB) is downloaded the **first time the OPF CLI
runs** — not by the Python API. Trigger the download once via the CLI:

```bash
.venv/bin/python -m opf redact --device cpu --format json "ping"
```

That writes the checkpoint to `~/.opf/privacy_filter/`. After that, every
detection (CLI or Python API) reuses the cached files.

To store the model elsewhere, set the environment variable:

```bash
export OPF_CHECKPOINT=/path/to/your/checkpoint
```

Or just rely on `start-anom.sh` — it checks the `opf` package is installed
and prints a clear install hint if it's missing.

---

## 4. Verify the installation

```bash
# Smoke-test with the mock (no model required)
python scripts/anonymize_file.py \
  --input docs/sample_synthetic.txt \
  --mock

# Smoke-test with the real model
python scripts/anonymize_file.py \
  --input docs/sample_synthetic.txt \
  --device auto
```

---

## 5. Run the tests

```bash
# All tests (no model needed — CI-safe)
pytest

# With coverage
pytest --cov --cov-report=term-missing
```

---

## 6. Anonymize a file (full example)

Create a synthetic input file:

```bash
cat > /tmp/test_doc.txt <<'EOF'
Dear Jane Doe,

Please find attached the invoice for account 4111-1111-1111-1111.
You can reach us at support@company-fake.org or call 555-0100.
Your appointment is confirmed for 15/06/2026 at 42 Placeholder Street.

Regards,
The Support Team
EOF
```

Run anonymization:

```bash
python scripts/anonymize_file.py \
  --input /tmp/test_doc.txt \
  --output /tmp/test_doc_redacted.json \
  --device auto \
  --operating-point precision \
  --min-confidence 0.8
```

Inspect the output:

```bash
cat /tmp/test_doc_redacted.json
```

Expected structure:

```json
{
  "source_file": "/tmp/test_doc.txt",
  "source_bytes": 312,
  "redacted_text": "Dear [PERSON], ...",
  "stats": {
    "private_person": 1,
    "account_number": 1,
    "private_email": 1,
    "private_phone": 1,
    "private_date": 1,
    "private_address": 1
  },
  "applied_spans": [...]
}
```

---

## 7. Configuration reference

| Flag | Default | Description |
|---|---|---|
| `--input` | _(required)_ | Path to input `.txt` file |
| `--output` | stdout | Path for JSON output |
| `--policy` | `policies/default.yaml` | Redaction policy YAML |
| `--max-bytes` | `1048576` (1 MiB) | Reject files larger than this |
| `--device` | `auto` | `auto` / `cpu` / `cuda` |
| `--operating-point` | `precision` | `precision` (viterbi) / `recall` (argmax) |
| `--min-confidence` | `0.0` | Drop spans below this score |
| `--checkpoint` | `~/.opf/privacy_filter` | Custom model checkpoint directory |
| `--mock` | off | Use regex mock instead of real model |
| `--verbose` | off | Debug-level logging to stderr |

---

## 8. Security notes

- **Logs never contain document text.** Only file path, byte count, span positions,
  entity types, and confidence scores are emitted.
- **`DetectedSpan.text_hash`** stores a SHA-256 digest of the raw PII value,
  not the value itself. This enables auditing and deduplication without persisting
  plaintext.
- Files above `--max-bytes` are rejected before any content is read into memory.
- Do not commit real documents into the repository. Use synthetic fixtures only.

---

## 9. Updating the model

```bash
# Re-pull the latest checkpoint from Hugging Face
rm -rf ~/.opf/privacy_filter
# The next run will re-download automatically.
```

To pin a specific version, clone the model repo manually and point `--checkpoint`
at the local directory.

---

## 10. OCR for scanned PDFs and image uploads

The `PdfExtractor` ships with a Tesseract-based OCR fallback for pages
that have no extractable text layer. Standalone `.png` / `.jpg` /
`.jpeg` uploads also go through OCR.

### Automatic install via `start-anom.sh`

The startup script checks for missing OCR deps and installs them on
first run:

* On **macOS** with Homebrew: runs `brew install tesseract tesseract-lang`
  and `brew install poppler` if either binary is missing, plus
  `pip install -e '.[ocr]'` if the Python packages aren't there yet.
* On **Linux / no Homebrew**: prints the install command and continues
  (OCR features become unavailable until you run the command yourself).

You don't need to install anything manually if you use `./start-anom.sh`
on macOS — first start does the setup, subsequent starts skip it.

To opt out (e.g. CI):

```bash
./start-anom.sh --no-ocr-setup
```

### Manual install

If you start the API some other way, install everything yourself:

```bash
# macOS
brew install tesseract tesseract-lang poppler
.venv/bin/pip install -e '.[ocr]'

# Debian / Ubuntu
sudo apt install tesseract-ocr tesseract-ocr-por poppler-utils
.venv/bin/pip install -e '.[ocr]'
```

`tesseract-lang` brings every supported language pack (~1 GB).
The default OCR language is **Brazilian Portuguese** (`por`); override
per process with the `ANONYMIZER_OCR_LANGUAGE` env var.

### Behaviour matrix

| File type | OCR extras installed | Behaviour |
|---|---|---|
| Digital PDF (text layer) | – | pypdf only, no OCR triggered |
| Scanned PDF (no text layer) | yes | OCR runs per scanned page |
| Scanned PDF (no text layer) | **no** | empty blocks returned (legacy) |
| `.png` / `.jpg` upload | yes | OCR runs, single block |
| `.png` / `.jpg` upload | **no** | upload rejected with 400 |

### Tuning

* `ocr.DEFAULT_MIN_TEXT_CHARS` (default 30) — pages whose text layer
  yields fewer chars than this are sent to OCR.
* `ocr_pdf_pages(..., dpi=200)` — bump to 300 for hard-to-read scans
  at the cost of CPU + memory.

### Privacy note

OCR output is document content. The OCR module logs only metadata
(page numbers, char counts, language). The recognised text feeds
straight into the existing detection / redaction pipeline — same
guardrails as native text extraction.
