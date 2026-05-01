#!/usr/bin/env bash
# Start the LGPDoc full stack (FastAPI + Next.js reviewer UI) in one shot.
# (Internal Python package keeps the historical name ``anonymizer``.)
#
#   ./start-anom.sh                 # API on 9000 + UI on 3000 — REAL OPF model
#   ./start-anom.sh --mock          # use regex-only detector (no 3 GB download)
#   ./start-anom.sh --no-ui         # API only (old behaviour)
#   ./start-anom.sh --port 8080     # custom API port
#   ./start-anom.sh --ui-port 3001  # custom UI port
#   ./start-anom.sh --reset         # wipe ./var/ before starting
#   ./start-anom.sh --open          # also auto-open the browser (macOS only)
#   ./start-anom.sh --no-ocr-setup  # skip the OCR dependency check
#
# Ctrl+C stops both processes cleanly.

set -euo pipefail

cd "$(dirname "$0")"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
PORT="${PORT:-9000}"
UI_PORT="${UI_PORT:-3000}"
HOST="${HOST:-127.0.0.1}"
USE_MOCK="false"
RESET="false"
START_UI="true"
OPEN_BROWSER="false"
OCR_SETUP="true"

# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --real)     USE_MOCK="false";    shift ;;
    --mock)     USE_MOCK="true";     shift ;;
    --port)     PORT="$2";           shift 2 ;;
    --ui-port)  UI_PORT="$2";        shift 2 ;;
    --host)     HOST="$2";           shift 2 ;;
    --reset)    RESET="true";        shift ;;
    --no-ui)    START_UI="false";    shift ;;
    --open)     OPEN_BROWSER="true"; shift ;;
    --no-open)  OPEN_BROWSER="false"; shift ;;
    --no-ocr-setup) OCR_SETUP="false"; shift ;;
    -h|--help)
      sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown flag: $1" >&2; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Backend sanity
# ---------------------------------------------------------------------------
if [[ ! -x .venv/bin/uvicorn ]]; then
  echo "venv not found or uvicorn missing. Set it up with:"
  echo "    python3 -m venv .venv && .venv/bin/pip install -e '.[dev,api]'"
  exit 1
fi

# Quando o usuário pediu o detector real, garantimos que o OPF está instalado
# antes de subir — assim o erro aparece aqui e não só no 1º upload.
if [[ "$USE_MOCK" != "true" ]]; then
  if ! .venv/bin/python -c "import opf" 2>/dev/null; then
    cat >&2 <<EOF
==================================================================
 OpenAI Privacy Filter (pacote 'opf') não está instalado.
------------------------------------------------------------------
 Para instalar o stack ML (~3 GB de modelo no 1º uso):
    .venv/bin/pip install -e '.[ml]'

 Ou rode em modo regex (sem download):
    ./start-anom.sh --mock
==================================================================
EOF
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# OCR deps — install once, no-op afterwards
# ---------------------------------------------------------------------------
ensure_ocr_deps() {
  # 1. Python packages from the [ocr] extras.
  if ! .venv/bin/python -c "import pytesseract, pdf2image, PIL" 2>/dev/null; then
    echo ">> instalando pacotes Python para OCR (pytesseract, pdf2image, Pillow)..."
    .venv/bin/pip install -e '.[ocr]' --quiet
  fi

  # 2. System binaries: tesseract (OCR engine) + pdftoppm (poppler — used by
  #    pdf2image to rasterise PDF pages).
  local need_tesseract="false"
  local need_poppler="false"
  command -v tesseract >/dev/null 2>&1 || need_tesseract="true"
  command -v pdftoppm >/dev/null 2>&1 || need_poppler="true"

  if [[ "$need_tesseract" == "false" && "$need_poppler" == "false" ]]; then
    return 0
  fi

  if [[ "$(uname)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
    echo ">> instalando dependências de OCR via Homebrew (uma vez só)..."
    if [[ "$need_tesseract" == "true" ]]; then
      brew install tesseract tesseract-lang
    fi
    if [[ "$need_poppler" == "true" ]]; then
      brew install poppler
    fi
    echo ">> OCR pronto."
    return 0
  fi

  cat >&2 <<EOF
==================================================================
 OCR opcional: dependências de sistema faltando
------------------------------------------------------------------
 Para PDFs escaneados e uploads de imagem (PNG/JPG/JPEG):
   macOS:   brew install tesseract tesseract-lang poppler
   Ubuntu:  sudo apt install tesseract-ocr tesseract-ocr-por poppler-utils

 Sem isso, scanned PDFs viram blocos vazios e uploads de imagem
 são recusados com 400. O resto da app funciona normalmente.

 Para silenciar este aviso: ./start-anom.sh --no-ocr-setup
==================================================================
EOF
}

if [[ "$OCR_SETUP" == "true" ]]; then
  ensure_ocr_deps
fi

if [[ "$RESET" == "true" ]]; then
  echo ">> wiping ./var/"
  rm -rf ./var
fi
mkdir -p ./var/quarantine ./var/output

export ANONYMIZER_API_QUARANTINE_DIR="${ANONYMIZER_API_QUARANTINE_DIR:-$(pwd)/var/quarantine}"
export ANONYMIZER_API_OUTPUT_DIR="${ANONYMIZER_API_OUTPUT_DIR:-$(pwd)/var/output}"
export ANONYMIZER_API_DB_URL="${ANONYMIZER_API_DB_URL:-sqlite:///$(pwd)/var/anonymizer_api.db}"
export ANONYMIZER_API_POLICY_PATH="${ANONYMIZER_API_POLICY_PATH:-$(pwd)/policies/default.yaml}"
export ANONYMIZER_API_USE_MOCK_CLIENT="${ANONYMIZER_API_USE_MOCK_CLIENT:-$USE_MOCK}"
export ANONYMIZER_API_MAX_BYTES="${ANONYMIZER_API_MAX_BYTES:-52428800}"

# ---------------------------------------------------------------------------
# Frontend prep
# ---------------------------------------------------------------------------
UI_DIR="apps/reviewer-ui"

if [[ "$START_UI" == "true" ]]; then
  if [[ ! -d "$UI_DIR" ]]; then
    echo "UI directory $UI_DIR not found. Use --no-ui to skip the UI."
    exit 1
  fi
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm not found in PATH. Use --no-ui to skip the UI."
    exit 1
  fi
  if [[ ! -d "$UI_DIR/node_modules" ]]; then
    echo ">> installing UI deps (one-time)..."
    (cd "$UI_DIR" && npm install --no-fund --no-audit --silent)
  fi
  # Always (re)write .env.local so the UI points at this script's API port.
  cat > "$UI_DIR/.env.local" <<EOF
NEXT_PUBLIC_API_BASE_URL=http://${HOST}:${PORT}
NEXT_PUBLIC_USE_MOCKS=false
EOF
fi

# ---------------------------------------------------------------------------
# Cleanup on exit
# ---------------------------------------------------------------------------
PIDS=()
cleanup() {
  echo
  echo ">> shutting down..."
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  # Give processes a moment to exit gracefully
  sleep 0.5
  for pid in "${PIDS[@]}"; do
    kill -9 "$pid" 2>/dev/null || true
  done
  exit 0
}
trap cleanup INT TERM

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
if [[ "$ANONYMIZER_API_USE_MOCK_CLIENT" == "true" ]]; then
  DETECTOR_DESC="🧪 mock (regex local — não baixa modelo)"
else
  DETECTOR_DESC="🤖 OpenAI Privacy Filter (carrega ~3 GB no 1º upload)"
fi

cat <<EOF
==================================================================
 LGPDoc (stack completa)
------------------------------------------------------------------
 API          : http://${HOST}:${PORT}
 Swagger      : http://${HOST}:${PORT}/docs
 Detector     : ${DETECTOR_DESC}
EOF
if [[ "$START_UI" == "true" ]]; then
  echo " Interface    : http://localhost:${UI_PORT}/jobs"
fi
cat <<EOF
 Quarentena   : ${ANONYMIZER_API_QUARANTINE_DIR}
 Saída        : ${ANONYMIZER_API_OUTPUT_DIR}
 DB           : ${ANONYMIZER_API_DB_URL}
==================================================================
EOF
if [[ "$ANONYMIZER_API_USE_MOCK_CLIENT" == "true" ]]; then
  echo " Para usar o modelo real: ./start-anom.sh --real"
fi
echo " Ctrl+C encerra os dois processos."
echo "=================================================================="
echo

# ---------------------------------------------------------------------------
# Start API
# ---------------------------------------------------------------------------
echo ">> starting API..."
.venv/bin/uvicorn scripts.run_api:app \
  --host "$HOST" --port "$PORT" --log-level info &
PIDS+=($!)

# Wait for /health (up to 30s)
api_ready="false"
for _ in $(seq 1 120); do
  if curl -fs "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
    api_ready="true"
    break
  fi
  sleep 0.25
done
if [[ "$api_ready" != "true" ]]; then
  echo "API failed to start. Check the log above."
  cleanup
  exit 1
fi
echo ">> API ready on http://${HOST}:${PORT}"

# ---------------------------------------------------------------------------
# Start UI
# ---------------------------------------------------------------------------
if [[ "$START_UI" == "true" ]]; then
  echo ">> starting UI (first compile may take ~10s)..."
  (cd "$UI_DIR" && PORT="$UI_PORT" npm run dev --silent) &
  PIDS+=($!)

  # Wait for Next.js dev server (up to 60s — first compile can be slow)
  ui_ready="false"
  for _ in $(seq 1 240); do
    if curl -fs "http://localhost:${UI_PORT}" >/dev/null 2>&1; then
      ui_ready="true"
      break
    fi
    sleep 0.25
  done
  if [[ "$ui_ready" == "true" ]]; then
    echo ">> UI ready on http://localhost:${UI_PORT}"
    if [[ "$OPEN_BROWSER" == "true" ]] && command -v open >/dev/null 2>&1; then
      open "http://localhost:${UI_PORT}/jobs"
    fi
  else
    echo ">> UI did not respond within timeout — check the log above"
  fi
fi

echo
echo ">> all processes running. Tail of logs follows."
echo "------------------------------------------------------------------"

# Wait for any background process to exit (or for Ctrl+C)
wait
