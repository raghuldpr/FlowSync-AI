# Using a real LLM provider

The client (`backend/app/llm/client.py`) calls any OpenAI-compatible `/chat/completions` endpoint. It is configured only by environment variables or the git-ignored `.env` file. Without them, answers come from the labelled deterministic fallback, so the demo still works.

Check each provider's current docs for model names, free-tier limits and tool-calling support; they change often.

| Provider | `LLM_BASE_URL` | Notes |
|---|---|---|
| Groq (first choice) | `https://api.groq.com/openai/v1` | Fast; pick a model that supports tool calling |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | OpenAI-compatible; tool-call behaviour can differ |
| OpenRouter free tier | `https://openrouter.ai/api/v1` | Free models can be slow or lack tool calling; last resort |

## Setup

1. Copy `.env.example` to `.env` (it is git-ignored). Never paste the key into chat or commit it.
2. Set:
   ```
   LLM_ENABLED=true
   LLM_BASE_URL=<from the table>
   LLM_MODEL=<model name from the provider>
   LLM_API_KEY=<your key>
   ```
3. Restart the backend. The LLM status (provider host, model, enabled) comes from `LLMClient.status()`.
4. Ask it something that needs tools, for example "Why is the busiest approach congested?", and check that the answer cites tool results. Also try a request for a signal change and confirm it only produces a proposal that needs operator confirmation.

## What to check (the layer has only been tested against a stand-in model)

- Tool calls come back well formed and the loop ends within the call limit.
- A malformed or refused response falls back cleanly instead of showing an error.
- A rate-limit response (free tiers) falls back cleanly.
- Turn the hotspot's internet off and confirm the fallback answers (failure drill `drill_llm_down`).

The cloud provider needs internet, so the hotspot must have data. The LLM's tools expose operational data (traffic, signals, incidents, warnings, route alternatives, system events); there is no tool that reads road-user sessions.
