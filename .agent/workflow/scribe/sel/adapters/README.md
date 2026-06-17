# Adapter Policy

This bundle does not store generated root adapters.

Root `scribe` and root `scripts/` are legacy compatibility outputs generated
only by:

```bash
.agent/workflow/scribe/scribe install . --with-root-adapters
```

Canonical source lives in:
- `../scribe`
- `../scripts/`
- `../scripts/scribe_install_templates.py`

Keeping adapter files out of the bundle avoids two visually competing
implementations with the same filenames.
