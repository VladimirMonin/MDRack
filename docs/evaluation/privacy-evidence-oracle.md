# Privacy and evidence oracle scaffold

Status: v0.4 W2-Q2 offline scaffold. This is not final release proof; the same oracle must be rerun after the standalone UX and final evidence surfaces exist.

## Contract

`tests/privacy_oracle.py` owns one frozen set of evidence surfaces and one sentinel matrix. The matrix uses distinct values for content, title, relative and absolute paths, root, URL, vector, metadata, facet, locator, provider identity/body, credential marker, and raw/chained exceptions. It also supplies forbidden keys for recursive mapping checks.

The frozen surfaces are:

- CLI stdout and stderr;
- embedded/Python API payloads;
- logs;
- evaluation and diagnostic reports;
- provider-facing evidence;
- cache or generated JSON evidence.

A test passes only when every captured surface is free of every sentinel and recursive forbidden key. Finding records and exceptions contain categories and structural locations only; they never echo rejected material.

`mdrack.eval.privacy.serialize_safe_json()` scans the complete JSON-compatible tree before serialization. Callers may write only its returned text. Unsafe values or keys raise the fixed `PrivacyViolation` before a cache/report writer is invoked.

## Branch matrix

The focused suite covers success, empty, degraded, validation failure, storage failure, provider failure, cleanup failure, and interruption. Raw exception messages and explicit exception chains are negative controls: the oracle must detect them, while safe eval/log/diagnostic projections must omit them.

Intentional public evidence is tested separately with fixed logical identifiers, status/category values, aggregate counts, and metrics. Private sentinels are never allowlisted merely because a public result payload exists.

## Offline boundary

The scaffold patches both `socket.create_connection` and `socket.socket.connect`; the matrix must complete with zero attempted sockets. It does not call a live provider, read active user data, validate Windows behavior, or prove future W4/W11 surfaces that do not yet exist.
