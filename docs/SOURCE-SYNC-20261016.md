# Local / GitHub / Apps Script source reconciliation, 16 October 2026

This source update records code already accepted on the two PCs. It does not deploy a collector, replace a running worker, enable cloud schedules, or invoke a model. The service budget remains zero. Render deployment, provider eligibility/cooldowns, all-source collection, automated Drive ingestion and PC-off acceptance remain separate gates.

## Source ownership

- `cloud-probes/`: existing Render/Apps Script preflight source. The runtime/probe files remain unchanged except for the Drive diagnostic already present in the live Apps Script editor. Both API workflows remain manual-only.
- `pc/cryptodata/src/`: source snapshot from the accepted local collector, including OI window/pagination/partial-result handling. It is versioned separately from `ops/src/`, which remains the existing emergency runner. No untested substitution of that runner is made.
- `pc/cryptodata/config/`: public asset/instrument/semantics catalogues. Operational settings, Drive account/folder configuration, private credentials and databases are excluded.
- `lan/ai-lan-coordinator/`: transport/boot/lease-fix-v2 and its source dependency closure. This is source control, not an installer or permission to overwrite either PC. Local identity, TLS keys/pin/config, install guards, AI ledger and policy remain unchanged. `ai-policy.example.json` is an inactive example; it must never overwrite machine policy automatically.

The OI implementation is byte-identical to the accepted PC source SHA-256 `28f276773f39211723ee2fc975e15b70b3c53a83f67d980239190fe0829ce843`. The LAN coordinator is byte-identical to accepted v2 SHA-256 `6b9d87ad704a7ad06b2e21d331c5f15e60792dffe419fcc104def935b2eb8917`. The OI regression test changes only its import path from an absolute PC directory to its own directory for reproducible checkout tests.

## Apps Script reconciliation

Authenticated editor readback confirms four source files match the local source after CRLF/LF normalization and trimming. The live manifest contains only the previously authorized `script.external_request` and `drive.file` scopes, V8, Asia/Ho_Chi_Minh, and the already enabled Drive v3 service. No live file, trigger, property, scope or function is changed or executed by this sync.

Repository mapping:

| Editor file | Source file |
|---|---|
| Mã.gs | pc/cryptodata/src/apps_script_probe.gs |
| HyperliquidRoute.gs | cloud-probes/apps_script_hyper_route.gs |
| HyperliquidPacket.gs | cloud-probes/apps_script_hyper_packet.gs |
| HyperliquidDrive.gs | cloud-probes/apps_script_hyper_drive.gs |
| appsscript.json | cloud-probes/appsscript.json |

The pre-existing local deployment manifest was stale (external_request only). The repository manifest mirrors already-authorized live state. The operational local template remains untouched in this staging-only change. Old proposed manifests and previous run windows/IDs remain historical evidence, not instructions to run them again.

## Verification and release discipline

Tests run against staging and isolated temporary fixtures. External/provider networking is disabled by the test harness; loopback pinned HTTPS is allowed for lease tests. AI tests use synthetic Python child processes, not Codex/Gemini/Antigravity models. Raw responses, live databases, identity/auth files, keys, receipts with private data and account settings are excluded from Git.

Publish this change for review on a dedicated branch. Main and the deployed Render service must remain unchanged until deployment settings, exact diff and the relevant gates have been verified. Rollback of the source sync is a Git revert/change of the review branch, not a rewind of live queue/AI ledgers or a worker identity replacement.

The initial PC suite had 14 errors from missing production databases/backups in isolation. Checkout-only test refactoring replaces these dependencies with deterministic synthetic fixtures, preserves validation assertions and makes recovery an actual discovered unittest. Quality and Apps Script tests also use checkout-relative paths. Runtime source remains byte-identical to the accepted local files. Initial failures and timeout logs are preserved outside Git alongside rerun results.
