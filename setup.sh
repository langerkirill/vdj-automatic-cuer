#!/bin/bash

echo "🎵 VirtualDJ Automatic Cuer - Setup"
echo "===================================="
echo ""

# Check for Python 3
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed."
    echo "Please install Python 3.9 or higher from https://www.python.org/downloads/"
    exit 1
fi

# Check Python version (requires 3.9+)
python_version=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
required_version="3.9"

if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    echo "❌ Python $python_version found, but Python 3.9 or higher is required."
    echo "Please upgrade Python from https://www.python.org/downloads/"
    exit 1
fi

echo "✅ Python $python_version found"
echo ""

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv

    if [ $? -ne 0 ]; then
        echo "❌ Failed to create virtual environment"
        exit 1
    fi

    echo "✅ Virtual environment created"
else
    echo "✅ Virtual environment already exists"
fi

# Install dependencies
echo "📦 Installing dependencies in virtual environment..."
./venv/bin/pip install -r requirements.txt

if [ $? -ne 0 ]; then
    echo "❌ Failed to install dependencies"
    exit 1
fi

echo "✅ Dependencies installed"
echo ""

# Setup .env file
if [ -f .env ]; then
    echo "⚠️  .env file already exists"
    read -p "Do you want to overwrite it? (y/N): " overwrite
    if [[ ! $overwrite =~ ^[Yy]$ ]]; then
        echo "Skipping .env creation"
        echo ""
        echo "✅ Setup complete!"
        exit 0
    fi
fi

echo "🔑 Setting up Gemini API key..."
echo ""
echo "Get your API key from: https://aistudio.google.com/apikey"
echo "Default AutoCue model: gemini-3.7-flash. Sorter uses gemini-3.7-flash."
echo ""
read -p "Enter your Gemini API key: " api_key

if [ -z "$api_key" ]; then
    echo "❌ No API key provided"
    exit 1
fi

echo "GEMINI_API_KEY=$api_key" > .env
echo "GEMINI_MODEL=gemini-3.7-flash" >> .env
echo "MUSIC_SORTER_GEMINI_MODEL=gemini-3.7-flash" >> .env
echo "✅ .env file created"
echo ""

echo "✅ Setup complete!"
echo ""
echo "🚀 To get started:"
echo "1. Activate the virtual environment:"
echo "   source venv/bin/activate"
echo ""
echo "2. Analyze a track (CLI):"
echo "   python3 automatic_music_cuer_gemini.py \"path/to/song.mp3\""
echo ""
echo "3. Or launch the Music Sorter UI (Add Cues + Ready for Sort):"
echo "   ./ui/run.sh"
echo "   then open http://127.0.0.1:8787"
echo ""
echo "💡 Use --dry-run to preview changes first:"
echo "   python3 automatic_music_cuer_gemini.py --dry-run \"path/to/song.mp3\""
