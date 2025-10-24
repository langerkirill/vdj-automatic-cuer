#!/bin/bash

echo "🎵 VirtualDJ Automatic Cuer - Setup"
echo "===================================="
echo ""

# Check for Python 3
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed."
    echo "Please install Python 3 from https://www.python.org/downloads/"
    exit 1
fi

echo "✅ Python 3 found: $(python3 --version)"
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
echo "Get your free API key from: https://aistudio.google.com/app/apikey"
echo ""
read -p "Enter your Gemini API key: " api_key

if [ -z "$api_key" ]; then
    echo "❌ No API key provided"
    exit 1
fi

echo "GEMINI_API_KEY=$api_key" > .env
echo "✅ .env file created"
echo ""

echo "✅ Setup complete!"
echo ""
echo "🚀 To get started:"
echo "1. Activate the virtual environment:"
echo "   source venv/bin/activate"
echo ""
echo "2. Analyze a track:"
echo "   python3 automatic_music_cuer_gemini.py \"path/to/song.mp3\""
echo ""
echo "💡 Use --dry-run to preview changes first:"
echo "   python3 automatic_music_cuer_gemini.py --dry-run \"path/to/song.mp3\""
