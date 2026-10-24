# MDRack

> 1.4.0rc3 source candidate: [native temporal/media preparation](docs/decisions/0017-native-temporal-preparation.md); [conditional publication and explicit v2 maintenance](docs/decisions/0014-conditional-publication-and-v2-upgrade.md).
> Core pin 1.0.0rc2, media pin 1.0.0rc3, SQLite 1.0.0rc3 and app-v2 schema 0005.
> Existing [prepared/native image API](docs/contracts/native-image-v1.md)
> and [ADR-0013](docs/decisions/0013-native-image-contract.md). Native CLI/engine
> orchestration supports explicit offline providers and a separately qualified
> [opt-in caller-owned native text/image process](docs/evaluation/native-text-image-product-2026-10-24.md).
> Default LM Studio HTTP pixels remain unsupported; bounded native qualification is
> not target-corpus quality, whole-programme or release acceptance.


The retained MDRack 1.3.0 base is a local Python 3.11+ command-line and embedded retrieval rack for
Markdown, typed metadata, timed transcripts, frame-caption text, and explicitly
supplied images. Its primary operational user is an agent working under a human
operator's explicit authority over locally held data.

The application depends on standalone `mdrack-core` and `mdrack-sqlite`
distributions. The first is the stdlib-only provider/persistence-neutral kernel;
the second is the stdlib-plus-core generic resource catalog/search adapter. The
`mdrack` distribution owns Markdown/image ingestion, app migration generations,
LM Studio integration, Click JSON commands, and `MDRackEngine`; it vendors neither
package.

## Quick start

```bash
uv sync --all-extras
uv run mdrack --help
uv run mdrack guide
mkdir -p ./notes
uv run mdrack --root ./notes init
uv run mdrack --root ./notes scan --provider fake
uv run mdrack --root ./notes search "architecture" --mode text
```

This is an offline installation smoke: fake embeddings are deterministic test
data, not semantic-quality evidence. Configure LM Studio before ordinary
semantic or hybrid use; see [Getting started](docs/getting-started.md) and the
[CLI guide](docs/getting-started.md#static-cli-guide).

## Agent-first work with human data

For a real task, an agent first identifies whether it is using the source-checkout
command (`uv run mdrack`) or an already installed `mdrack` command, then asks for
an explicit data root and the exact operation to perform. Keep private sources
outside this repository and out of shell history, reports, and commits. The static
guide and `--help` are read-only discovery steps; `init`, `scan`, explicit ingest,
rebuild, delete, and provider/model actions change derived local state and must be
named and authorized before the agent runs them.

An authorized workflow can scan Markdown in the chosen root or ingest one selected
raw text/Markdown file, image with caller-supplied text, WAVE file, or ISO-BMFF
video. Legacy textual WAVE/ISO-BMFF ingestion requires an explicitly selected local
adapter executable. The separate [native temporal path](docs/contracts/native-temporal-v1.md)
uses existing local CPU FFmpeg/ffprobe for bounded frame/audio/visual-video preparation;
it provides no built-in transcription, model inference or semantic-quality guarantee. Search by text and optional scope, retain only returned logical
IDs and portable locators or time/line evidence, then use a new CLI process with
the same root to run `status` against the same derived store. Source files remain
unchanged. The complete safe sequence and its evidence boundary are in
[Getting started](docs/getting-started.md#agent-workflow-for-authorized-human-data),
[operations](docs/operations.md#agent-workflow-and-private-data), and the shipped
[MDRack agent skill](skills/mdrack/SKILL.md).

A real acceptance claim is stronger than this quick start: it requires an agent to
exercise the installed public surface with separately authorized human-like private
data while checking source immutability and privacy. Synthetic/offline checks are
supporting evidence only; they do not substitute for that run.

The CLI also provides read, files, status, doctor, rebuild, eval, and
LM Studio model-management commands. Host applications can use
`MDRackEngine` without importing Click.

## How it works

1. The default markdown-it adapter parses UTF-8 Markdown into source blocks
   with H1–H6 heading paths and exact line/character provenance.
2. Markdown image syntax contributes only safe textual alt/alias prose; paths and
   referenced files are never inspected or indexed as assets.
3. The app projects documents into typed core resources and writes a complete
   graph atomically to the fixed SQLite catalog at `<store>/catalog.sqlite3`.
4. Core retrieval accepts ready lexical/vector branches, applies scope filters
   before limits, groups resource evidence, and performs deterministic weighted RRF.
5. Explicit media ingestion keeps native vectors separate from optional textual
   projections, never stores original media bytes in the catalog, and uses logical
   IDs for reads and similarity.

## Documentation

- [Getting started: installation, configuration, CLI, and engine](docs/getting-started.md)
- [MDRack agent skill](skills/mdrack/SKILL.md) — self-contained terminal workflow
- [Operations, diagnostics, privacy, and troubleshooting](docs/operations.md)
- [Development guide for humans and coding agents](docs/development.md)
- [Evaluation contracts and native corpus/public runner](docs/evaluation/README.md) — separate software/installed/native functional and reviewed-data evidence; target-corpus model fitness remains open
- [Current architecture index](docs/current-architecture/README.md)
- [Proposed native-media / late-enrichment programme](docs/proposals/native-media/README.md) — full programme; scoped native components are implemented, while whole-programme quality/operations/release obligations remain open
- [System overview](docs/current-architecture/system-overview.md)
- [Indexing and structural chunking](docs/current-architecture/indexing-and-chunking.md)
- [SQLite persistence and current schema](docs/current-architecture/sqlite-persistence.md)
- [Text, semantic, and hybrid retrieval](docs/current-architecture/retrieval.md)
- [Asset handling](docs/current-architecture/assets.md)
- [CLI and embedded interfaces](docs/current-architecture/public-interfaces.md)
- [Current limitations](docs/current-architecture/limitations.md)
- [Completed MDRack 1.3 compact-storage plan (historical)](docs/plans/2026-07-24-v1.3-compact-storage-sqlite-vec.md)
- [MDRack 1.3.0 release notes and source-publication status](docs/release-1.3.md)
- [MDRack 1.1 local release](docs/release-1.1.md)
- [CLI contracts](docs/cli-contracts.md)
- [v0.3 compatibility registry](docs/compatibility/v0.3-compatibility-registry.md)
- [Recovery procedures](docs/recovery.md)
- [ADR-0001: reranking deferred](docs/decisions/0001-reranking-deferred.md)
- [ADR-0002: provider/storage-neutral core](docs/decisions/0002-provider-storage-neutral-core.md)
- [ADR-0004: SQLite operating envelope](docs/decisions/0004-sqlite-operating-envelope.md)
- [Offline release verification](docs/offline-release-verification.md) — supported distribution cells, matrix evidence, and strict no-live gates
- [Licensing and commercial use](docs/licensing.md) — MIT policy, commercial-use boundary, and Windows bundle gate
- [Third-party runtime notices](THIRD_PARTY_NOTICES.md) — exact locked resolver graph and upstream-license obligations
- [v0.4 W5-CI contract](docs/contracts/v0.4-w5-ci-contract.md)
- [v0.4 offline release-candidate packet](docs/evidence/v0.4-release-packet.md) — exact stage/release/DoD evidence and non-claims
- [v0.4 public-surface compatibility ledger](docs/compatibility/v0.4-public-surface-ledger.json)
- [v0.3 release evidence](docs/evidence/v0.3-release-gate.md)

Files under `docs/plans/` and the legacy architecture/design documents are
historical unless explicitly marked as an active plan. They are not the current
product contract.

## Images

Markdown image syntax never starts image ingestion. It preserves eligible alt or
textual alias once as ordinary prose and discards target/path/title/dimensions.
`mdrack image ingest` is a separate explicit local-file operation. Caption/OCR
text is caller-supplied or produced by an injected extractor; live LM Studio use
requires an explicit provider choice. Source bytes remain outside SQLite and are
never modified.

The explicit [caller-owned native composition](examples/native_cuda/README.md) also
supports real image vectors without caption substitution. Its unchanged v1 protocol
admits fixed RGB PNG documents/text-query-for-image; explicit retrieval-v2 additionally
qualifies short text DOCUMENT and one text QUERY route over image/text candidates.
Normal engine/catalog/installed PE and fresh evaluated CUDA checks are recorded
separately from private corpus/model fitness. No runtime/model is bundled, loaded into
Python or made the default; provider roles, complete space identity and caller lifecycle
remain explicit. The [reviewed paired-data companion](docs/evaluation/paired-query-companion-review-2026-10-24.md)
retains both complete corpus families and does not grant model execution or publication.

## Known limitations

- SQLite is the only persistent database. Fresh compact generations use canonical
  float32 vectors and the builtin exact Python scan; there is no ANN/vector
  extension dependency in the base distribution.
- Structural `overlap_chars` is currently not consumed, so structural chunks do
  not overlap.
- Production reranking is disabled. `rerank_rank` and `rerank_score` remain
  `null`; non-null reranker injection fails closed.
- Default textual media paths index supplied transcripts/frame-caption text; they
  do not transcribe raw audio or perform pixel/acoustic inference. The separately
  selected bounded native image/text process above is not a generic audio/video
  inference capability or independent semantic-quality guarantee.
- The CLI also accepts explicitly authorized local raw-media adapters:
  `ingest audio SOURCE_PATH --source-ref REF --allow-external-stt
  --stt-command COMMAND` for RIFF/WAVE input and `ingest raw-video SOURCE_PATH
  --source-ref REF --allow-external-video-extractor
  --video-extractor-command COMMAND` for ISO-BMFF input. These shell-free
  stdin adapters are caller-selected; they are not built-in transcription,
  decoding, pixel/acoustic search, or provider quality evidence.
- Provider-free and real-source checks prove retrieval behavior and source
  integrity, not universal semantic quality from a live embedding model.
- Legacy `files` and `sections` inspection commands still expose internal record
  IDs; new resource/image/search contracts expose logical IDs only.
- The resource-core schema lives in one fixed `catalog.sqlite3`. There is no
  candidate-generation cutover or runtime rollback; destructive recreation is
  separately authorized.

See the complete [limitations ledger](docs/current-architecture/limitations.md).

## Verification and recovery

Run the complete offline verification suite with `scripts/verify.sh` on Linux or
`scripts/verify.ps1` on Windows. For a changed development checkout use
`bash scripts/verify.sh --development` or `./scripts/verify.ps1 --development`:
only historical release-packet identity is SKIPPED, not a release PASS. Default
invocation remains strict. Documentation maintenance is described in the
[development guide](docs/development.md#bounded-documentation-maintenance). The release-specific cells and strict gates are
documented in [offline release verification](docs/offline-release-verification.md):
they cover `mdrack`, `mdrack-core`, `mdrack-media`, and `mdrack-sqlite` as wheel
and sdist, Linux/Windows × Python 3.11/3.12 evidence, offline E2E/privacy lanes,
and installed smoke. Provider calls, network fallback, and remote execution are
not part of the default path. Fixed-catalog recovery and destructive recreation
boundaries are documented in [recovery](docs/recovery.md).

For a reproducible Windows executable build, see
[Windows EXE build](docs/windows-exe-build.md).

## License and commercial use

MDRack is standard MIT software. Commercial use, modification, redistribution,
sublicensing, and sale are allowed, provided that the exact copyright and
permission notice — `Copyright (c) 2026 VladimirMonin` — stays with copies or
substantial portions. MDRack is provided without warranty. This is a project
license summary, not legal advice or clearance for a particular distribution.

The policy for the four Python distributions and future Windows bundle is in
[licensing and commercial use](docs/licensing.md).
The exact locked resolver dependency graph, including the Windows-only
`colorama` branch and `certifi`/MPL-2.0, is recorded in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Resolver-installed
dependencies are not relicensed by MDRack and are not bundled in the four base
Python wheel/sdist artifacts.
