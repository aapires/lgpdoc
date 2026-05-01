#!/usr/bin/env bash
# Bootstrap LGPDoc numa máquina macOS limpa.
#
#   ./bootstrap.sh                          # clona em ~/lgpdoc e instala tudo
#   ./bootstrap.sh --dir /caminho/lgpdoc    # local de instalação custom
#   ./bootstrap.sh --branch dev             # branch específica
#   ./bootstrap.sh --in-place               # não clona — instala no diretório atual
#   ./bootstrap.sh --with-opf               # também instala o stack ML (Catalina: NÃO)
#
# Alvo padrão: macOS Catalina (10.15) ou mais recente, Intel ou Apple Silicon.
# Em Catalina, várias dependências compilam do código-fonte — primeira execução
# pode levar 30–60 min.
#
# Quando termina, suba a aplicação com:
#   cd <dir> && ./start-anom.sh --mock

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
REPO_URL="https://github.com/aapires/lgpdoc.git"
INSTALL_DIR="${INSTALL_DIR:-$HOME/lgpdoc}"
BRANCH="main"
IN_PLACE="false"
WITH_OPF="false"

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)       INSTALL_DIR="$2"; shift 2 ;;
    --branch)    BRANCH="$2";      shift 2 ;;
    --in-place)  IN_PLACE="true";  shift ;;
    --with-opf)  WITH_OPF="true";  shift ;;
    -h|--help)
      sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "flag desconhecida: $1" >&2; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Sanity: macOS apenas
# ---------------------------------------------------------------------------
if [[ "$(uname)" != "Darwin" ]]; then
  echo "Este bootstrap foi feito para macOS. Saindo." >&2
  exit 1
fi

OS_VER="$(sw_vers -productVersion)"
OS_MAJOR="${OS_VER%%.*}"
echo ">> macOS detectado: $OS_VER"

if [[ "$OS_MAJOR" == "10" ]]; then
  cat <<EOF
   ⚠ Catalina (10.15) ou mais antigo detectado.
   - Instalar Python/Node via Homebrew pode compilar do código-fonte (~30–60 min).
   - O stack ML (OPF/torch) NÃO instala em Catalina — wheels só existem para macOS 11+.
     A flag --with-opf será ignorada se for passada aqui.
EOF
  if [[ "$WITH_OPF" == "true" ]]; then
    echo "   --with-opf desabilitada por incompatibilidade com Catalina."
    WITH_OPF="false"
  fi
fi

# ---------------------------------------------------------------------------
# Xcode Command Line Tools (necessário para compilar Python/Node se faltar bottle)
# ---------------------------------------------------------------------------
if ! xcode-select -p >/dev/null 2>&1; then
  echo ">> Xcode Command Line Tools não instalado."
  echo "   Vai abrir um popup do sistema. Conclua a instalação e o script segue."
  xcode-select --install || true
  until xcode-select -p >/dev/null 2>&1; do
    sleep 5
  done
fi
echo ">> Xcode CLT OK."

# ---------------------------------------------------------------------------
# Homebrew
# ---------------------------------------------------------------------------
if ! command -v brew >/dev/null 2>&1; then
  echo ">> instalando Homebrew (pede sua senha de admin)..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  # PATH local ao bootstrap (Apple Silicon usa /opt/homebrew, Intel usa /usr/local).
  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
fi
echo ">> Homebrew OK ($(brew --version | head -1))."

# ---------------------------------------------------------------------------
# Pacotes do sistema
# ---------------------------------------------------------------------------
ensure_brew_pkg() {
  # $1 = formula, $2 = comando que comprova presença (ou path absoluto)
  local formula="$1" check="$2"
  if eval "$check" >/dev/null 2>&1; then
    return 0
  fi
  echo ">> instalando $formula via Homebrew..."
  brew install "$formula"
}

ensure_brew_pkg "git"             "command -v git"
ensure_brew_pkg "python@3.11"     "command -v $(brew --prefix)/opt/python@3.11/bin/python3.11"
ensure_brew_pkg "node@20"         "command -v $(brew --prefix)/opt/node@20/bin/node"
ensure_brew_pkg "tesseract"       "command -v tesseract"
ensure_brew_pkg "tesseract-lang"  "test -d $(brew --prefix)/Cellar/tesseract-lang"
ensure_brew_pkg "poppler"         "command -v pdftoppm"

# Garantir python3.11 e node@20 visíveis nesta sessão.
PY_BIN="$(brew --prefix)/opt/python@3.11/bin/python3.11"
NODE_BIN_DIR="$(brew --prefix)/opt/node@20/bin"
if [[ ! -x "$PY_BIN" ]]; then
  PY_BIN="$(command -v python3.11 || true)"
fi
if [[ -z "${PY_BIN:-}" || ! -x "$PY_BIN" ]]; then
  echo "python3.11 não encontrado após instalação." >&2
  exit 1
fi
export PATH="$NODE_BIN_DIR:$PATH"
echo ">> usando $($PY_BIN --version)  e  Node $(node --version 2>/dev/null || echo '???')."

# ---------------------------------------------------------------------------
# Clone/update do repositório
# ---------------------------------------------------------------------------
if [[ "$IN_PLACE" == "true" ]]; then
  PROJECT_DIR="$(pwd)"
  echo ">> modo --in-place: usando $PROJECT_DIR"
elif [[ -d "$INSTALL_DIR/.git" ]]; then
  PROJECT_DIR="$INSTALL_DIR"
  echo ">> repositório já existe em $PROJECT_DIR, atualizando..."
  (
    cd "$PROJECT_DIR"
    git fetch origin
    git checkout "$BRANCH"
    git pull --ff-only
  )
else
  PROJECT_DIR="$INSTALL_DIR"
  echo ">> clonando $REPO_URL em $PROJECT_DIR..."
  mkdir -p "$(dirname "$PROJECT_DIR")"
  git clone --branch "$BRANCH" "$REPO_URL" "$PROJECT_DIR"
fi

cd "$PROJECT_DIR"

# ---------------------------------------------------------------------------
# venv + deps Python
# ---------------------------------------------------------------------------
if [[ ! -x .venv/bin/python ]]; then
  echo ">> criando .venv com $PY_BIN..."
  "$PY_BIN" -m venv .venv
fi

.venv/bin/pip install --upgrade pip --quiet

EXTRAS="dev,api,ocr"
if [[ "$WITH_OPF" == "true" ]]; then
  EXTRAS="${EXTRAS},ml"
  echo ">> instalando deps Python (${EXTRAS}) — incluindo stack ML (~3 GB de modelo no 1º uso)..."
else
  echo ">> instalando deps Python (${EXTRAS}) — sem [ml]; rode em --mock."
fi
.venv/bin/pip install -e ".[${EXTRAS}]" --quiet

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
if [[ -d "apps/reviewer-ui" ]]; then
  echo ">> instalando deps Node em apps/reviewer-ui..."
  (cd apps/reviewer-ui && npm install --no-fund --no-audit --silent)
fi

# ---------------------------------------------------------------------------
# Sanity check rápido
# ---------------------------------------------------------------------------
echo ">> rodando smoke test (mock)..."
if .venv/bin/pytest -q tests/test_extractors.py >/dev/null 2>&1; then
  echo "   smoke test: OK"
else
  echo "   smoke test FALHOU — investigue antes de subir a aplicação."
fi

# ---------------------------------------------------------------------------
# Próximos passos
# ---------------------------------------------------------------------------
cat <<EOF

==================================================================
 LGPDoc instalado em: $PROJECT_DIR
------------------------------------------------------------------
 Para subir a aplicação:

    cd $PROJECT_DIR
    ./start-anom.sh --mock

 Em seguida abra:  http://localhost:3000/jobs

 OBS — Node 20 via Homebrew é "keg-only". Para ter \`node\`/\`npm\`
 disponíveis em todo terminal novo, adicione ao seu ~/.zshrc:

    echo 'export PATH="$NODE_BIN_DIR:\$PATH"' >> ~/.zshrc

 (ou rode start-anom.sh sempre a partir de um shell que tenha
  carregado o PATH do Homebrew.)
==================================================================
EOF
