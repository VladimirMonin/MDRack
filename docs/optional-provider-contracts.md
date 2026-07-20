# Optional provider contracts

MDRack keeps provider adapters opt-in. Importing or constructing
`LMStudioTextProvider` / `OpenRouterTextProvider` performs no network I/O and
these adapters are not wired into the default `MDRackEngine` or CLI paths.

Both adapters use the same offline-testable contract:

- OpenAI-compatible chat-completions payloads with bounded prompt and output
  sizes;
- a caller-injected transport for deterministic tests (the default transport is
  used only when `generate()` is explicitly called);
- at most `max_retries` retries for timeout, connection, and server failures;
- stable error categories (`invalid_input`, `input_limit`, `timeout`,
  `unavailable`, `authentication`, `http_error`, `server_error`,
  `invalid_response`, and `output_limit`);
- an in-memory bounded cache keyed by a SHA-256 fingerprint of provider, model,
  endpoint, prompt, and generation limit; cache contents can be cleared;
- logs containing only provider/model, attempt count, and output size.

The contract suite is `tests/unit/test_optional_provider_contracts.py` and uses
only a fake transport. It proves zero socket attempts during import and
construction, cache isolation, retry bounds, safe failure categories, and
credential/prompt exclusion from result metadata. These tests are
`unit/offline` evidence only: they do not claim live provider availability,
quality, cost, credentials, or endpoint compatibility.
