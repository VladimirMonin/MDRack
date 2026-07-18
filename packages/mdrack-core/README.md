# mdrack-core

`mdrack-core` is the standalone, standard-library-only contract and orchestration
kernel used by MDRack. It accepts already prepared resources and ready lexical or
vector branches. It does not parse source documents, open locators, access a
filesystem or network, call model providers, or persist data itself.

Distribution version `1.0.0rc1` publishes core contract version
`1.0.0-rc.1`. The release candidate freezes ordered public exports while leaving
final `1.0.0` stability explicitly unclaimed.

## Install

```bash
python -m pip install mdrack-core==1.0.0rc1
```

Python 3.11 or newer is required. There are no runtime dependencies.

## Use

Implement `CatalogPort` with an in-memory or persistent adapter, then pass the
adapter to `CoreIndexingService`, `RetrievalService`, or
`ResourceDiscoveryService`. Callers own source preparation, deterministic logical
IDs, query vectors, embedding-space identity, and typed locator validation.

See [API.md](API.md) for the public surface and [CHANGELOG.md](CHANGELOG.md) for
release notes.

## Boundaries

- `mdrack_core` never imports `mdrack`.
- Core owns portable records, validation, ports, retrieval/fusion, and safe event
  schemas.
- Adapters own persistence and candidate generation.
- Host applications own source parsing, providers, filesystem access, CLI and
  public compatibility mapping.
- Locators and similarity bases are opaque caller-owned values.

## License

MIT
