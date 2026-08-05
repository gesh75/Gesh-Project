# Notion integrations (Agentic-RH / GESH)

## 1. GitHub → Control Updates (CI)

1. Create a Notion **Internal Integration** at https://www.notion.so/my-integrations  
2. Copy the secret → GitHub repo **Settings → Secrets → `NOTION_TOKEN`**  
3. In Notion, share these with the integration (Invite):
   - Active Projects  
   - Control Updates  
   - Decisions  
   - Agentic-RH Soak Sessions  
4. Optional repo **Variables**:
   - `NOTION_CONTROL_UPDATES_DB` = `ee4a034ecb4940cc8cd3452b997e5cd7`
   - `NOTION_ACTIVE_PROJECTS_DB` = `fec5d195b038400bb4752d3e88e0b0f5`
   - `NOTION_RELATED_PROJECT_PAGE` = `3b3ff394850081a69231fd526025af6e` (Agentic-RH row)
   - `NOTION_PROJECT` = `Agentic-RH`
5. Push to `main` (or run workflow **Notion Control Update** manually).

## 2. STATUS.json → Active Projects

```bash
export NOTION_TOKEN=secret_...
python3 scripts/notion/sync_status.py
# or dry-run:
python3 scripts/notion/sync_status.py --dry-run
```

Edit `docs/control/STATUS.json` after each verified run; CI also refreshes `main_sha` on push.

## 3. O2b soak sessions

```bash
python3 scripts/notion/post_soak_session.py \
  --name "2026-08-05 ET soak" \
  --result green \
  --pnl 0 \
  --trades 0 \
  --counts-o2b \
  --sha "$(git rev-parse HEAD)"
```

DB: https://app.notion.com/p/564225425d1c416cb0d2058f8f725edc

## 4. Notion AI connectors (UI — cannot enable via API)

In Notion: **Settings → Connections** (or Notion AI connectors):
- Enable **GitHub** (gesh75 orgs/repos)
- Optionally **Gmail** for briefing/RH mail search  
Then workspace search + MCP search can surface PRs/emails next to pages.

## 5. Zapier / Make — RH trade mail → Briefing Log

Recipe (create once in Zapier/Make):
1. **Trigger:** Gmail — New email from `noreply@robinhood.com` (or label `RH/execution`)
2. **Action:** Notion — Create page in Briefing Log parent  
   Parent page: https://app.notion.com/p/396ff394850081fa97e7dc07546ec05d  
   Or append via Notion API using the same `NOTION_TOKEN`  
3. Map subject + snippet into the page body; do **not** put credentials in Notion.

## 6. GitHub Issues / PRs ↔ Decisions

Decisions DB now has **GitHub issue** and **GitHub PR** URL properties.  
When opening a binding decision, paste the issue/PR URL.  
Optional later: Action that creates a Decision row when an issue is labeled `decision`.

## 7. GitHub Pages Command Center

Workflow: `.github/workflows/notion-command-center-pages.yml`  
Source: `docs/notion-command-center/index.html`  
Enable Pages: **Settings → Pages → GitHub Actions**.  
Expected URL: `https://gesh75.github.io/agentic-rh-gesh/` (path depends on Pages config; artifact is the folder contents).

## 8. Agent paste form

Control Updates → view **Agent paste form** (open submissions).  
Agents/humans can submit Name / SHA / Next without opening the full DB.

## IDs quick reference

| Resource | ID |
|---|---|
| Control Updates DB | `ee4a034ecb4940cc8cd3452b997e5cd7` |
| Active Projects DB | `fec5d195b038400bb4752d3e88e0b0f5` |
| Decisions DB | `ea80e92de4e346a2a72b734d05cc714d` |
| Soak Sessions DB | `564225425d1c416cb0d2058f8f725edc` |
| Agentic-RH Active Projects row | `3b3ff394850081a69231fd526025af6e` |
| GESH Financial Active Projects row | `3b3ff394850081598af3e5513202c552` |
| Command Center page | `3b3ff394850081829035ef07866d3c37` |
