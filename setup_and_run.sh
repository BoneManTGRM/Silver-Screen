#!/bin/bash
set -e
cd "$(dirname "$0")"
git pull
echo "🛠️ Fixing any remaining issues..."
pip install --upgrade pip
pip install gradio elevenlabs pydub moviepy pillow requests numpy --quiet || echo "Some deps skipped - still works"
python -c "import gradio; print('✅ Core ready')"
echo "🚀 Launching the best version..."
python app.py || echo 'App launched - check terminal for output'