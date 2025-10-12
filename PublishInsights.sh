#!/bin/bash

# Script de lancement avec vérifications
echo "🚀 Starting InsightsAI..."

# Vérifier que .env existe
if [ ! -f .env ]; then
    echo "⚠️  .env file not found. Creating example..."
    cat > .env << EOF
# Modèles
OPENAI_MODEL=gpt-5
OPENAI_MODEL_ANALYZE=gpt-5
OPENAI_MODEL_GATHER=gpt-5-mini

# Reasoning / Verbosity
OPENAI_REASONING=low
OPENAI_VERBOSITY=low
MAX_OUTPUT_TOKENS=700

# Web search
USE_WEB_SEARCH=true
SEARCH_CONTEXT_SIZE=low

# Sécurité & perfs
OPENAI_MAX_CONCURRENCY=1
REQUEST_TIMEOUT_SECONDS=45

# PDF
PDF_ENGINE=weasyprint

# Google Places
GOOGLE_PLACES_API_KEY=replace-with-your-key

# CORS (dev)
ALLOWED_ORIGINS=*
EOF
    echo "✅ Created .env file. Please edit with your OpenAI API key."
    exit 1
fi

# Vérifier la clé API
if ! grep -q "OPENAI_API_KEY=" .env; then
    echo "❌ OPENAI_API_KEY not found in .env"
    exit 1
fi

if ! grep -q "GOOGLE_PLACES_API_KEY=" .env; then
    echo "❌ GOOGLE_PLACES_API_KEY not found in .env"
    exit 1
fi

# Lancer l'application
echo "✅ Environment ready. Starting server..."
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --env-file .env
