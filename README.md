# moodle-llm-chat

An agentic course assistant that lives inside Moodle.

- **`local_llmchat`** (Moodle plugin, `moodle/local_llmchat/`) pushes course content to the
  API, issues short-lived chat tokens carrying the user's visible content and roles, and
  embeds the chat UI.
- **`moodle_llm_chat`** (this Python package) indexes that content into an encrypted SQLite
  store and runs a LangGraph agent with retrieval, navigation, time and `ask_user` tools,
  streamed to the browser over a websocket.

Every tool call is restricted to what the asking Moodle user may see, and is re-checked
with Moodle before any document is read.

> Status: being rebuilt from the former Matrix bot. See the plan in the PR description for
> what is done and what is next.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest
```

Configuration is via `MLC_*` environment variables — see `.env.example` and
`moodle-llm-chat doctor`.

Supported providers for chat: Gemini, OpenAI (and OpenAI-compatible servers), Ollama,
Anthropic. Embeddings: Gemini, OpenAI(-compatible), Ollama.
