# Nexus-Code 🚀

Nexus-Code is a high-performance, BYOK (Bring Your Own Key) AI coding assistant designed with token-optimization, caching, and blazing-fast inference at its core.

It leverages a dual-architecture: a lightweight VS Code Extension frontend (written in TypeScript) and a powerful local Daemon backend (written in Python/FastAPI).

## Quick Start (Run it ASAP)

If you have just cloned this repository and want to run it instantly without publishing:

### Prerequisites
- Node.js & npm (for the extension)
- Python 3.10+ (for the daemon)
- VS Code (or a compatible IDE)

### 1. Build and Package the Extension
Open your terminal and run the following commands:
```bash
# Navigate to the extension folder
cd nexus-code/extension

# Install the extension's npm dependencies
npm install

# Compile the extension and package it into a .vsix file
npm run compile
npm run prepackage
npx @vscode/vsce package --allow-missing-repository
```

### 2. Install the Extension in VS Code
Once the `.vsix` package is generated (e.g., `nexus-code-1.0.0.vsix`):
1. Open VS Code.
2. Go to the **Extensions** view (`Ctrl+Shift+X` or `Cmd+Shift+X`).
3. Click the `...` menu at the top right of the Extensions view.
4. Select **"Install from VSIX..."** and choose the `nexus-code-1.0.0.vsix` file you just built.

### 3. Start Chatting!
Click the Nexus-Code icon in your activity bar to open the chat interface. 
*Note: The very first time you launch it, the Python daemon will take a few seconds to silently set up its virtual environment in the background.*

---
**Enjoy your lightning-fast, ultra-optimized AI assistant!**
