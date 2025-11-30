#!/bin/bash

# Setup script for creating virtual environment and installing dependencies
# This ensures Python packages are isolated and don't conflict with system packages

echo "🔧 Setting up Python virtual environment..."

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
    echo "✅ Virtual environment created"
else
    echo "✅ Virtual environment already exists"
fi

# Activate virtual environment
echo "🔌 Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "📥 Installing Python dependencies..."
pip install -r requirements.txt

# Install Playwright browsers
echo "🎭 Installing Playwright browsers..."
playwright install chromium

echo ""
echo "✅ Setup complete!"
echo ""
echo "To start the server, run:"
echo "  yarn start"





