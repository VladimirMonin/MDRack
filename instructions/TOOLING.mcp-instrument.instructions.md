# Model Content Protocol Instrument — Agent Guide

## Purpose

The **Model Content Protocol Instrument** is a minimal Python tool that lets text-only AI agents "see" images. It sends a local image file to the polza.ai vision API and returns a text description.

## Location

```
E:\vibecode621\model-content-protocol\
```

## Quick Start for Agents

```python
from src.mcp_client import describe_image

# Describe an image (default model: google/gemini-2.5-flash-lite)
result = describe_image("/path/to/image.png")

# With a custom prompt
result = describe_image("/path/to/image.png", prompt="What colors are in this image?")

# With a different model
result = describe_image("/path/to/image.png", model="google/gemini-3.5-flash")
```

## Public API

### `describe_image(image_path, prompt=None, model=None) -> str`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `image_path` | `str` | required | Absolute or relative path to a PNG or JPEG file |
| `prompt` | `str` or `None` | `"Опиши это изображение"` | Custom analysis question |
| `model` | `str` or `None` | `"google/gemini-2.5-flash-lite"` | Model ID override |

**Returns:** The model's text description of the image.

**Raises:**
- `FileNotFoundError` — file does not exist
- `ValueError` — unsupported format, file too large (>20 MB), or missing API key
- `RuntimeError` — API failure or empty response

## Supported Models

| Model ID | Tier | Note |
|----------|------|------|
| `google/gemini-2.5-flash-lite` | Default | Lightweight, fast |
| `google/gemini-3.5-flash` | Higher | Better vision accuracy |
| `openai/gpt-4o` | Premium | OpenAI flagship |
| `anthropic/claude-3-5-sonnet` | Premium | Anthropic flagship |
| Any model ID | — | Must be available on polza.ai |

## CLI Usage

```bash
cd model-content-protocol
uv run python -m src.mcp_client describe <path> [--prompt "text"] [--model "model-id"]
```

## OpenCode MCP Server

The project exposes the image tool to OpenCode through `src/mcp_server.py`.
The server uses FastMCP and stdio transport.

Project-local OpenCode config lives at:

```
E:\vibecode621\opencode.json
```

The local MCP server entry must use:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "polza-vision": {
      "type": "local",
      "command": [
        "uv",
        "run",
        "--directory",
        "E:/vibecode621/model-content-protocol",
        "python",
        "-m",
        "src.mcp_server"
      ],
      "enabled": true,
      "timeout": 60000
    }
  }
}
```

Do not put `POLZA_AI_API_KEY` in `opencode.json`. The server command runs in
`model-content-protocol`, so `python-dotenv` reads `model-content-protocol/.env`.
Restart OpenCode after changing `opencode.json`; config is loaded at startup.

## Configuration

The tool reads `POLZA_AI_API_KEY` from a `.env` file in the project root:

```
model-content-protocol/.env
```

Create it by copying `.env.example` and inserting your key.

## Dependencies

- Python 3.10+
- `openai>=1.0`
- `python-dotenv`
- `mcp[cli]>=1.0`
- Managed via UV

## Project Structure

```
model-content-protocol/
├── .env                      # API key (gitignored)
├── .env.example              # Template
├── .gitignore
├── pyproject.toml
├── README.md
├── screenshots/              # Test images (gitignored)
├── src/
│   ├── mcp_client.py         # Core logic: describe_image() + CLI
│   └── mcp_server.py         # FastMCP server for OpenCode
├── tests/
│   ├── test_mcp_client.py    # Unit tests (no API key)
│   ├── test_mcp_server.py    # MCP stdio tool discovery test
│   └── test_integration.py   # Integration tests (skip without key)
└── uv.lock
```
