# Literature review: QuickBooks data extraction tools

Date: 2026-11-20
Baseline: `qbw_to_sqlite.py` (this repo). It copies a `.QBW`, mounts the copy read-only with a SQL Anywhere 17 engine (`dbeng17 -r`), and copies every `QBAdminGroup` base table into SQLite.

## Summary

- Of the sources reviewed, our tool is the only one that mounts the real SQL Anywhere engine and dumps raw tables. The closest peers are **OpenQBW** (parses the `.qbw` file directly, with no engine and no login) and **OpenSQLAnywhere** (the page-store library that OpenQBW builds on).
- **QBExtract** takes a different route. It uses the official QuickBooks SDK on a running Desktop company file and produces a curated, relationship-preserving entity export. It is the best model for the semantic layer we lack.
- The commercial and SaaS tools (CData, Starfish, SpectralCore, Bruin/ingestr, Meltano) add useful *operational* patterns: incremental merge by key, chunked loads, type-mapping rules, and exit-status discipline. None of them handles `.QBW` files.
- Highest-value gaps in our tool, in priority order: a failing run exits 0, declared column types are discarded, nothing is hashed or recorded for provenance, and `.QBB` backups are unsupported.

## Sources reviewed

| Source | Type / license / activity | QuickBooks product | Approach | Read status |
|---|---|---|---|---|
| [OpenQBW](https://github.com/Sigilweaver/OpenQBW) (crate `openqbw` 0.2.0) | OSS, Rust + Python, Apache-2.0, 15 stars, pushed 2026-11-20 | Desktop `.qbw` (and `.qbb` backups) | Parses the SA17 page store directly; no SQL Anywhere engine, no login | Read (README, docs.rs) |
| [OpenSQLAnywhere](https://github.com/Sigilweaver/OpenSQLAnywhere) (crate `opensqlany` 0.3) | OSS, Rust, Apache-2.0, 4 stars, no releases | SA17 files (QBW is a use case) | Clean-room reader for the SQL Anywhere on-disk page format | Read (README, issues, releases) |
| [QBExtract / quickbooks-desktop-extractor](https://github.com/josephsprei-lang/quickbooks-desktop-extractor) | OSS, Python (EXE build), MIT, 13 stars | Desktop / Enterprise 2019+ | QB SDK qbXML via COM; requires QB running and an authorization prompt | Read (README) |
| [tap-quickbooks (Meltano Hub, hotgluexyz variant)](https://hub.meltano.com/extractors/tap-quickbooks) | OSS Singer tap, "Active (Stable)" | QuickBooks Online only | OAuth2 API extraction; state messages; threaded | Read (hub page) |
| [Meltano](https://meltano.com/) | OSS EL platform, 600+ connectors | Any (generic) | Extract-load pipelines with orchestration, dbt integration | Read (homepage only) |
| [Bruin / ingestr](https://getbruin.com/ingestion/platforms/copy-data-from-quickbooks-to-sqlite/) | OSS CLI plus commercial cloud | QuickBooks via OAuth2 URI, SQLite destination | `ingestr` CLI: full copy or `merge` incremental | Read (marketing + examples) |
| [SpectralCore FullConvert](https://www.spectralcore.com/fullconvert/databases/howto/quickbooks/sqlite) | Commercial | QuickBooks to SQLite | Full copy with chunking and editable type mapping | Read (marketing) |
| [CData Sync](https://www.cdata.com/data/integration/quickbooks-to-sqlite/) | Commercial | "QuickBooks Desktop" (per the page) | Continuous replication, automatic schema replication | Read (marketing); linked KB article not read |
| [StarfishETL](https://www.starfishetl.com/connect/QuickBooks%20Online/SQLite) | Commercial iPaaS | QuickBooks Online | Pre-built maps; key matching; initial load then sync | Read (marketing; quote only) |
| [NewLaw](https://newlaw.com/data-migration/from/quickbooks-online/to/sqlite) | Service | QuickBooks Online | Not known | **Not reviewed**: HTTP 429, no Retry-After |
| [ContextGate](https://www.contextgate.ai/#/?connectorname=quickbooks) | Commercial MCP gateway | "QuickBooks Online" listed in a directory | Governance and audit layer for AI tool calls | Read (landing page, connectors directory; connector detail page not fetched) |

The docs.rs page for `openqbw` and the Meltano hub page were re-read after the first attempt. The first agent spawns were blocked by a guard, and the fetches were retried once the user approved.

## Baseline: what `qbw_to_sqlite.py` does today

Facts checked against the code:

- Output is written directly to `--out` (`qbw_to_sqlite.py:350`). A failure partway through leaves a partial file. A re-run into an existing file keeps tables that the new run does not recreate.
- Each table is dropped and recreated (`:260-261`). There is no incremental mode and no primary-key handling.
- `CREATE TABLE` uses column names only, with no declared types (`:261`). The SQL Anywhere types (`NUMERIC(19,4)`, `DATE`, and so on) are lost. Money stays exact only because `Decimal` is converted to `str` (`:240-241`).
- A table that fails is printed as `SKIPPED` (`:361`), but the script still prints `[done]` and returns 0 (`:379`). A run with skipped tables looks successful.
- The source `.QBW` is not hashed before or after the copy. The output records no provenance: no source hash, no extraction time, no row-count manifest.
- `.QBB` backups are not handled.
- Text decoding is per value: UTF-8 when valid, else cp1252 (`fix_text`, `:218-229`). This is comparable to QBExtract's ASCII normalization, but it keeps the non-ASCII text instead of stripping it.

## Findings: features worth adopting

Listed in rough priority order. Each item names the source it came from.

1. **Honest exit status.** Count skipped tables and exit non-zero when any exist, then print the list. Source: OpenQBW's exit codes (0 success, 1 error, 2 usage, 3 verify mismatch) and its strict-by-default stance.
2. **Atomic output.** Build into a temp file in the same directory and rename on success. Refuse to write over the input or any QuickBooks file. Source: OpenQBW `export` and `migrate`.
3. **Chain of custody.** Record the SHA-256 of the source `.QBW` before the copy and of the working copy after, and store both in the output. Source: QBExtract's `meta` block (company, export time, extractor version) combined with OpenQBW's verify step.
4. **Provenance table.** Write a `_qbw_meta` table containing the source name, hashes, extraction time, tool version, per-table row counts, and the list of skipped tables. This is the most useful forensic addition for little code.
5. **Keep declared column types, and primary keys.** Read `SYS.SYSCOLUMN` and `SYS.SYSINDEX` to emit typed `CREATE TABLE` statements and primary keys. Source: OpenQBW's `schema <table>` and catalog parsing, and SpectralCore's editable type-mapping rules. Primary keys are also a prerequisite for item 9.
6. **Verify step.** After each copy, compare the source `COUNT(*)` with the destination count and report mismatches. Optionally run a page CRC check. Sources: OpenQBW `verify` (exit 3 on read-back mismatch) and OpenSQLAnywhere `inspect --verify-crc`.
7. **Strict and lenient modes with record-level warnings.** Strict stops at the first undecodable record. Lenient skips it and lists it. Our tool only skips whole tables today. Source: OpenQBW (55 records skipped in its sample, all listed).
8. **An entity-level export.** QBExtract emits 20 curated entities with `FullName` references, a customer-to-job `parent` hierarchy, invoice headers with line items, and sales-tax fields. Our raw dump leaves the user to decode `QBAdminGroup_abmc_*` tables. Options: a documented mapping, SQL views, or a QBExtract-style export on top of the raw dump. OpenQBW's line-item extraction (130 source tables in its sample) is related work.
9. **Incremental re-runs by key.** Bruin/ingestr's `--incremental-strategy merge` with `--incremental-key` and `--primary-key` updates existing rows and inserts new ones. Meltano's tap-quickbooks emits state every N records. This needs item 5 first.
10. **Chunking, limits and table filters.** Bruin has `--limit` and interval parameters for large tables. QBExtract chunks by year because QuickBooks caps result sets at about 5,000 records on some configurations. Our `fetchmany(2000)` already streams, but there is no `--tables` filter, no `--limit` for test runs, and no date filter.
11. **`.QBB` backup support.** OpenQBW reads the zlib-compressed company stream inside a `.qbb`, with a 1 GiB default cap on the inflated size. Many clients send backups rather than live files, so this is probably the most practical functional gap.
12. **A no-login path.** OpenQBW parses the page store without an SA engine and without a credential. OpenSQLAnywhere reads the page format without SAP installed. Our tool depends on creating a passwordless QuickBooks user first, which is its main operational limit. Treat the no-login route as research-grade. OpenQBW says the meaning of line-item values is "not established" and that its `source_table` attribution is unverified (it agrees with a content check on 0.3% of pages).
13. **Corrupt-file handling.** QBExtract's `--corrupt-safe` narrows queries to avoid walking corrupt ledgers. It also reports per-chunk `FAILED`/`ERROR` results and supports re-running a single range. Our analogue is per-table skipping, which is coarser.
14. **Redaction and audit logging, if data reaches an AI agent.** ContextGate's pattern is to redact personal data in tool results and keep a log of every call. This only matters if extracted data is handed to an LLM. It is not an extraction feature.

## Not worth adopting

- **CData, StarfishETL, SpectralCore, NewLaw.** These are commercial GUIs or migration services. The pages describe no file-level technique, and pricing is by quote or subscription. The one idea worth borrowing is SpectralCore's chunked loading of large tables, which item 10 covers.
- **Meltano as a platform.** Orchestration, schedules and retries are overkill for a tool that runs one company file at a time. Revisit only if we automate across many companies.
- **tap-quickbooks.** It targets QuickBooks Online through the API. We work on Desktop files. Its state messages and threaded loads are noted above.
- **ContextGate as an extractor.** It is a governance gateway for AI tool calls, not a data source. Item 14 covers the only relevant idea.

## Caveats on the sources

- **OpenSQLAnywhere** has 4 stars, no releases and 1 open issue (#16, "Enterprise 24.0 page-store structure: per-sector rotation, sector-7 reconstruction, header stash, checkpoint copies"). That title suggests the page-store format changed in later QuickBooks Enterprise versions. We did not test this. The issue body was not read.
- **OpenQBW** is at 0.2.0 and says it is in active development. Its README lists known limits (line-item value meaning not established; 4.5% of line items may not be read on its sample because of sector-boundary anchors; strict mode stops on the first undecodable record). Its supported QuickBooks Desktop versions are not stated.
- **QBExtract's** production-use and corruption claims are the author's own and were not verified. Its last push was 2026-05-26.
- **Marketing pages** (CData, Starfish, SpectralCore, Bruin, Meltano, ContextGate) make capability claims that were not verified. Several fields are "not stated" in the summaries above.
- **Bruin's examples are internally inconsistent.** One sentence refers to a `users` table and the examples use `customers`. Its QuickBooks object list is not given.

## Gaps in this review

- **NewLaw** was not reviewed (HTTP 429, no Retry-After). Retry later.
- **ContextGate's QuickBooks connector detail page** was not fetched. The landing page has no QuickBooks section.
- **CData's linked knowledge-base article** (`cdata.com/kb/tech/quickbooks-sync-sqlite.rst`) was not fetched. It may answer the open questions about its SQLite mapping.
- **Starfish's support portal** and **Meltano's settings YAML** were not fetched.

## Suggested next steps

1. Fix the exit status and make output atomic (items 1 and 2). These are small and remove the silent-failure risk.
2. Add a provenance table with hashes and row counts (items 3, 4, 6).
3. Read declared types and primary keys from the catalog (item 5). This is the foundation for typed output and incremental merges.
4. Decide whether the raw dump is the deliverable, or whether an entity-level export (item 8) is also needed for the intended use.
5. Add `.QBB` support, probably by reusing OpenQBW's approach (item 11), once there is a sample to test against.

## Implementation status

Status as of 2026-11-20, on branch `feat/extractor-hardening` (uncommitted). Each
item is numbered as in the findings above.

| # | Item | Status | Notes |
|---|---|---|---|
| 1 | Honest exit status | Done | Exit 3 when any table is a problem. Exit 1 for fatal errors |
| 2 | Atomic output | Done | Written to a temp file, then renamed. Refuses QuickBooks files as output |
| 3 | Chain of custody | Done | SHA-256 of the source and of the working copy, in `_qbw_meta` |
| 4 | Provenance tables | Done | `_qbw_meta`, `_qbw_tables`, `_qbw_columns`, `_qbw_rejected` |
| 5 | Declared types and primary keys | Done | From `SYS.SYSCOLUMN`. Unique index `<table>__pk` |
| 6 | Read-back verification | Partly | Row counts checked against `COUNT(*)`. Page CRC not implemented: no verified spec |
| 7 | Lenient and strict modes | Done | Lenient logs bad rows and continues. `--strict` aborts the table |
| 8 | Entity-level export | Partly | Views for invoices (with lines), customers, items and accounts. Other transaction types not yet |
| 9 | Incremental merge | Done | `--mode merge` upserts by primary key. Deletions not detected |
| 10 | Chunking, limits, filters | Done | `--tables`, `--exclude`, `--limit`, `--where`, `--batch-size` |
| 11 | `.QBB` backup support | Done, untested on a real backup | Synthetic container test only |
| 12 | No-login path | Experimental | `--backend openqbw`. Returned no transactions or line items on the sample |
| 13 | Corrupt-file handling | Partly | A failed table is `PARTIAL` and keeps the rows read so far. No record-level resume |
| 14 | AI-agent redaction and audit log | Not implemented | Out of scope for an extractor. Provenance (item 4) covers the audit trail |

Verification: the converter on the sample file gives the same row count as the
original output for all 297 tables. All 32 unit and integration tests pass, and
ruff lint and format are clean.
