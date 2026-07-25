#!/bin/bash
# GESH Financial — One-time setup script
# Run once: bash setup.sh

set -e

echo "============================================"
echo "  GESH Financial Platform — Setup"
echo "============================================"
echo ""

# PR105-RUN-01: .python-version is the single declared runtime source. Prefer the
# matching interpreter and FAIL CLEARLY on an unsupported one, rather than silently
# building a venv the production chain cannot use.
DECLARED="$(tr -d ' \t\r\n' < .python-version 2>/dev/null || echo 3.12)"
PYTHON=$(command -v "python${DECLARED}" || command -v python3 || true)
if [ -z "$PYTHON" ]; then
  echo "ERROR: Python ${DECLARED} not found. Install from https://python.org"
  exit 1
fi
PY_VER=$($PYTHON --version 2>&1 | awk '{print $2}')
PY_MM=$(echo "$PY_VER" | cut -d. -f1,2)
if [ "$PY_MM" != "$DECLARED" ]; then
  echo "ERROR: this repository declares Python ${DECLARED} (.python-version) but"
  echo "       ${PYTHON} is ${PY_VER}. Install Python ${DECLARED} and re-run."
  exit 1
fi
echo "✅ Python: $PY_VER (declared ${DECLARED})"

# Create virtual environment
if [ ! -d "venv" ]; then
  echo "→ Creating virtual environment..."
  $PYTHON -m venv venv
fi
source venv/bin/activate
echo "✅ Virtual environment ready"

# Install dependencies
echo "→ Installing dependencies (this takes ~1 min)..."
pip install --quiet --upgrade pip
# PR105-RUN-02: ONE installation contract. CI installs requirements.lock, so setup,
# documentation and production must install the same reviewed, fully-pinned tree —
# otherwise the environment that was tested is not the environment that ships.
# requirements.txt remains the human-maintained DIRECT-dependency source.
pip install --quiet -r requirements.lock

# NLTK vader lexicon for sentiment analysis
python3 -c "import nltk; nltk.download('vader_lexicon', quiet=True)"
echo "✅ Dependencies installed"

# .env setup
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ""
  echo "⚠️  ACTION REQUIRED:"
  echo "   Edit .env and fill in your own credentials:"
  echo "   - ROBINHOOD_USER / ROBINHOOD_PASS  (your Robinhood login)"
  echo "   - ANTHROPIC_API_KEY                 (from console.anthropic.com)"
  echo ""
  echo "   Optional (for extra features):"
  echo "   - GEMINI_API_KEY   → free at aistudio.google.com"
  echo "   - FINNHUB_API_KEY  → free at finnhub.io"
  echo "   - TELEGRAM_BOT_TOKEN / CHAT_ID → for trade alerts"
  echo ""
  read -p "   Press Enter after editing .env to continue..." _
else
  echo "✅ .env already exists"
fi

# Validate required keys
source .env 2>/dev/null || true
if [ -z "$ROBINHOOD_USER" ] || [ "$ROBINHOOD_USER" = "your_robinhood_email@example.com" ]; then
  echo "ERROR: Set ROBINHOOD_USER in .env"
  exit 1
fi
if [ -z "$ANTHROPIC_API_KEY" ] || [ "$ANTHROPIC_API_KEY" = "sk-ant-..." ]; then
  echo "ERROR: Set ANTHROPIC_API_KEY in .env"
  exit 1
fi
echo "✅ Credentials validated"

echo ""
echo "============================================"
echo "  Setup complete! Start the app with:"
echo ""
echo "  source venv/bin/activate"
echo "  python server.py"
echo ""
echo "  Then open: http://localhost:9000"
echo "============================================"
