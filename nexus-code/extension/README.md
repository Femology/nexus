# Nexus-Code
An elite VS Code extension for AI-assisted coding, powered by a local Python daemon for semantic caching, context optimization, and high-performance LLM routing.

## Features
- **Bring Your Own Key (BYOK)**: Connect your own API keys for OpenAI, Anthropic, Google, DeepSeek, or use local models via Ollama.
- **Semantic Caching**: Never pay twice for the same answer. Multi-tier FAISS caching remembers past sessions and workspace context.
- **Context Optimizer**: Automatically strips irrelevant context to save tokens and improve reasoning.
- **Memory Graph**: Distills long conversational context into compressed facts.
- **Tool Loops**: Allows the AI to read your files, list directories, search for references, and execute terminal commands.

## Setup
1. Configure your API keys in the extension settings panel.
2. The local daemon will automatically start on `localhost:8000`.

## Architecture
Nexus-Code splits work between a lightweight TypeScript Extension Host and a heavy-duty Python optimization daemon. The Extension Host handles UI and VS Code APIs, while the Python daemon handles all ML workloads (embedding, vector search, LLMLingua compression).
