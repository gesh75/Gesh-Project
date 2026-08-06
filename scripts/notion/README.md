# Notion sync scripts

Stdlib-only Python helpers for Control Updates / Active Projects / Soak Sessions.

```bash
export NOTION_TOKEN=secret_...   # Internal Integration

# After editing docs/control/STATUS.json
python3 scripts/notion/sync_status.py

# Manual control update
python3 scripts/notion/post_control_update.py \
  --name "manual note" \
  --sha "$(git rev-parse HEAD)" \
  --summary "what changed" \
  --next "next exact action"

# O2b soak row
python3 scripts/notion/post_soak_session.py \
  --name "2026-08-05 ET" \
  --result green \
  --counts-o2b \
  --sha "$(git rev-parse HEAD)"
```

See `docs/control/NOTION_INTEGRATIONS.md` for GitHub secrets, connectors, Zapier, and Pages.
