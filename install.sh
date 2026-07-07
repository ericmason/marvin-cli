#!/bin/bash
# Install marvin CLI

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_PATH="/usr/local/bin/marvin"

# Make the script executable
chmod +x "$SCRIPT_DIR/marvin.py"

# Create symlink
if [ -L "$INSTALL_PATH" ] || [ -f "$INSTALL_PATH" ]; then
    echo "Removing existing $INSTALL_PATH..."
    sudo rm "$INSTALL_PATH"
fi

echo "Creating symlink: $INSTALL_PATH -> $SCRIPT_DIR/marvin.py"
sudo ln -s "$SCRIPT_DIR/marvin.py" "$INSTALL_PATH"

# Check for requests
if ! python3 -c "import requests" 2>/dev/null; then
    echo ""
    echo "Installing requests library..."
    pip3 install requests
fi

echo ""
echo "Done! Run 'marvin setup' to configure your API tokens."
echo "Get tokens at: https://app.amazingmarvin.com/pre?api"
