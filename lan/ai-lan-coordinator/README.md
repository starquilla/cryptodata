# Accepted AI-LAN transport source (lease-fix-v2)

Version control snapshot, not an automatic installer. Keep exactly one coordinator and a local SQLite queue. No shared database on Drive/SMB. Identity enrollment, TLS keys/pin/config, install metadata and per-machine ledgers/policies must remain on their original host. There is no identity or credential migration in this snapshot.

The included `registry.json` keeps metered/production adapters disabled. The example policy keeps all AI providers disabled and is not an operational policy update. Enabling a provider, running a model or installing boot tasks requires the existing separate reviewed workflow. Tests use temporary identities and loopback TLS; they do not call real models.

Offline regression modules: `test_coordinator test_network test_ai_runtime test_auto_handoff test_boot_acceptance_audit test_claim_delivery test_lease_fix`. Boot-runtime source is Windows-specific. Do not run it from the checkout: the accepted local installation uses its original guarded files and task principal.
