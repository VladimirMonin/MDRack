---
applyTo: "docs/**"
name: "TOOLING.MCPInstrument"
description: "When to use: explicit image/screenshot inspection or MCP-assisted visual evidence; not for ordinary Markdown or Mermaid source review."
---

# MCP-assisted visual inspection

## Scope

Use a configured visual/MCP instrument only when the task supplies an image, screenshot,
or rendered artifact and visual semantics matter. MDRack itself has no GUI and no MCP server;
this instruction does not authorize adding either.

## Preconditions

- Confirm the tool is actually available in the current session; do not assume a server name,
  model, API key, Windows path, or external provider configuration.
- Keep visual inspection read-only unless the task separately authorizes edits.
- Do not upload private user content or vault screenshots to an external service without explicit approval.

## Evidence

Ask a bounded question about the supplied artifact. Record the artifact identity, what was
visually checked, tool/provider boundary when relevant, result, uncertainty, and any areas the
tool could not inspect. A tool summary is evidence, not source-of-truth for code or schema.

## Mermaid boundary

For Mermaid in architecture documentation, prefer syntax validation and local rendering.
A screenshot inspection may supplement rendering but does not prove source syntax, links,
architecture correctness, or schema fidelity. If no Mermaid validator/renderer is available,
record a waiver rather than substituting an unrelated vision call.

## Prohibited behavior

Do not hardcode machine-specific paths, credentials, stale model identifiers, or unrelated
project configuration. Do not claim a visual PASS without an actual artifact and tool result.
