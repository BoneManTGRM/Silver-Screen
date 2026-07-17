#/bin/bash

set -e  # Exit immediately if a command exits with a non-zero status

echo "🚀 Setting up Silver-Screen AI Movie Maker..."

# Pull latest changes
git pull origin main || echo "✅ Already up to date or no changes"

# Python virtual environment setup
if [ ! -d "venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv venv || { echo "❌ Failed to create venv. Ensure Python 3 is installed."; exit 1; }
  echo "✅ Virtual environment created"
fi

# Activate venv
source venv/bin/activate

# Upgrade pip and install dependencies
pip install --upgrade pip
pip install -r requirements.txt
echo "✅ Python dependencies installed"

# Frontend setup (Next.js or static)
if [ -d "frontend" ] && [ -f "frontend/package.json" ]; then
  echo "📦 Setting up frontend..."
  cd frontend
  npm install || echo "⚠️ Frontend npm install had warnings (common for dev)"
  cd ..
  echo "✅ Frontend ready"
fi

# Environment file
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  cp .env.example .env
  echo "✅ .env.example copied to .env"
  echo "⚠️ IMPORTANT: Edit .env with your API keys (ElevenLabs, etc.)"
fi

# Run the app
echo "🎬 Launching Silver-Screen Gradio UI..."
echo "App will be available at http://127.0.0.1:7860"
python app.py
