# Grok Account Manager — Design Spec

**Date:** 2026-07-14  
**Status:** Draft for user review  
**Location:** `C:\Ai\grokbuild-tools`

## 1. Problem

Users hold multiple SuperGrok / xAI accounts and want a desktop tool that can:

1. Capture N accounts from the current Grok Build login
2. Show detailed quota / rate-limit information per account
3. One-click switch which account Grok Build uses
4. Aggregate **per-account token usage totals** (how many tokens / how much quota consumed)
5. Switching must **not** wipe or rewrite session history, settings, skills, or plugins

## 2. Goals and non-goals

### Goals

- Desktop UI (Python + CustomTkinter)
- Capture accounts from `~/.grok/auth.json` only (no embedded OAuth UI in v1)
- Switch Grok Build identity by rewriting **only** `~/.grok/auth.json`
- After switch, best-effort auto-restart of the `grok` process
- Quota probe with remaining/limit detail when the API exposes it
- Per-account cumulative token statistics from local session logs + switch timeline
- Plain-text local vault for multi-account credentials (user-accepted tradeoff)

### Non-goals (v1)

- Built-in browser OAuth / device-code login inside the app
- Full isolation of sessions per account
- Encrypting the credential vault (Windows Credential Manager / master password)
- Scraping grok.com web UI for product quotas not available via API/session data
- Cross-machine sync

## 3. Architecture

```
┌─────────────────────────────────────────────┐
│  Desktop UI (CustomTkinter)                 │
│  Account list | Detail | Capture/Switch/Sync│
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  Core services                               │
│  • AccountStore  — local accounts.json       │
│  • AuthBridge    — read/write ~/.grok/auth.json │
│  • QuotaProbe    — token probe + rate headers│
│  • UsageStats    — session log aggregation   │
│  • GrokProcess   — detect/stop/start grok    │
└─────────────────────────────────────────────┘
```

### Hard boundary (must not change)

| May change | Must not change |
|------------|-----------------|
| `~/.grok/auth.json` | `~/.grok/sessions/**` |
| Backup `auth.json.bak` (adjacent or in app data) | `~/.grok/config.toml` |
| App data under `~/.grok-account-manager/` | skills, plugins, logs, models cache |

Switching accounts is an **auth overlay**, not a full profile switch.

### App data directory

`%USERPROFILE%\.grok-account-manager\`

| File | Purpose |
|------|---------|
| `accounts.json` | Multi-account credentials + metadata + per-account stats |
| `switch_log.json` | Timeline of which `user_id` was active (for usage attribution) |
| `usage_cursor.json` | Incremental scan cursor for session usage |
| `app.log` | Optional diagnostics (never log full tokens) |
| `settings.json` | UI prefs (e.g. skip switch confirm) |

## 4. Account capture and switch

### Capture (“从当前 Grok 登录抓取”)

1. Read `~/.grok/auth.json`
2. Validate OIDC entry has usable credentials (`key` and/or `refresh_token`, identity fields)
3. Upsert account by stable primary key `user_id`:
   - `id` = `user_id`
   - `label` default = `email` (editable alias)
   - `email`, `user_id`, `team_id`, JWT `tier` when present
   - Full auth map entry as stored by Grok (needed for faithful write-back)
   - `captured_at`, `last_used_at`, `updated_at`
4. Same `user_id` again → update tokens, do not duplicate
5. Append switch_log entry if this account is currently active
6. Optionally run quota probe immediately

### Switch

1. Load target account full auth entry from vault
2. If current `auth.json` identity is **not** in vault → auto-capture first (prevent silent credential loss)
3. Write backup of current `auth.json` to `auth.json.bak` (last-one backup is enough for v1)
4. Atomically write `~/.grok/auth.json` containing only the target entry in Grok’s expected shape
5. Append `switch_log` with `{ user_id, at_unix, source: "switch" }`
6. `GrokProcess`:
   - Find running `grok` / `grok.exe`
   - Graceful terminate, force-kill after timeout
   - Relaunch via PATH or known install paths (`%USERPROFILE%\.grok\bin\grok.exe`)
   - On launch failure: still report switch success for credentials; show “请手动重启 Grok”
7. Update `last_used_at`; UI marks account as current

### Current account detection

Compare `auth.json` `user_id` / `email` to vault. Highlight current. If logged-in identity is not in vault, show “未收录” with one-click capture.

### Delete / rename

- Rename: local `label` only
- Delete: remove from vault only; **do not** clear `auth.json` (even if deleting the current account)
- Stats for deleted accounts are removed with the vault entry (v1)

### Safety

- Confirm before switch (optional “don’t ask again” in settings)
- Logs redact tokens / refresh tokens
- README states plain-text vault risk
- Project `.gitignore` excludes real secrets; sample fixtures only in tests

## 5. Quota probe

### Sources of truth

1. **Identity / tier / expiry** from vault entry + JWT claims (`tier`, `exp`, `email`, `team_id`)
2. **Rate-limit detail** from a minimal authenticated request to `https://api.x.ai/v1/chat/completions` using account `key` (after refresh if needed)

Observed response headers (subject to API change):

- `x-ratelimit-limit-requests` / `x-ratelimit-remaining-requests`
- `x-ratelimit-limit-tokens` / `x-ratelimit-remaining-tokens`

### Procedure

1. If access `key` expired or near expiry → refresh via OIDC refresh_token against `auth.x.ai`; persist refreshed entry into vault (and into `auth.json` if this is current account)
2. Send tiny completion (`max_tokens=1`), prefer a cheap/build-related model with fallback list
3. Parse headers + identity into `quota` cache on the account
4. Support refresh current / refresh all; show `last_probed_at` and errors without inventing numbers

### Honesty boundary

UI copy must distinguish:

- **Current window remaining** (rate-limit headers; typically not lifetime)
- **Cumulative usage** (session aggregation; section 6)

Consumer-only web quotas (e.g. some Imagine daily caps) are out of scope unless exposed by the same authenticated APIs.

## 6. Per-account token statistics

### User intent

For each account, show a **single totals block**: how many tokens were used / how much quota was consumed under that account—not a machine-wide blob.

### Data source (local, read-only)

Scan `~/.grok/sessions/**/updates.jsonl` for `turn_completed` (or equivalent) updates containing:

```json
"usage": {
  "inputTokens": ...,
  "outputTokens": ...,
  "totalTokens": ...,
  "cachedReadTokens": ...,
  "reasoningTokens": ...,
  "modelCalls": ...,
  "numTurns": ...
}
```

with top-level `timestamp` (unix seconds).

**Never write** to session files.

### Attribution

Sessions are shared across account switches by design, so files usually lack `user_id`. Attribution uses `switch_log`:

1. Ordered list of `{ user_id, at_unix }` (capture, switch, optional app-start detection of current auth)
2. Each usage event at `timestamp` belongs to the latest log entry with `at_unix <= timestamp`
3. Events before any log entry → bucket **`unassigned`**
4. Do not invent ownership for pre-tool history

### Per-account cumulative fields

| Field | Meaning |
|-------|---------|
| `input_tokens` | Sum of input tokens |
| `output_tokens` | Sum of output tokens |
| `total_tokens` | Sum of total tokens |
| `reasoning_tokens` | Sum of reasoning tokens |
| `cached_read_tokens` | Sum of cached read tokens |
| `model_calls` | Sum of model calls |
| `turns` | Sum of turns / completed usage events |
| `last_sync_at` | Last successful aggregation time |

### Display semantics

- **Primary “消耗” metric:** cumulative `total_tokens` (with input/output split)
- **Secondary “当前额度”:** window remaining/limit from probe; optional `used ≈ limit - remaining` when both present
- Labels must not imply that window remaining is lifetime remaining

### Sync behavior

- Manual **同步统计**
- Optional auto-sync on app open and after switch
- Incremental via `usage_cursor.json` (e.g. last processed file offset / `(session_id, timestamp, prompt_id)` set)
- Full rebuild available if cursor corrupts

### Acceptance (stats)

1. Use account A → sync → A totals increase  
2. Switch to B → use → B increases, A does not  
3. Switch alone does not zero stats  
4. Unassigned bucket visible when applicable  

## 7. UI

```
┌──────────────────────────────────────────────────────┐
│  Grok Account Manager              [刷新额度] [同步统计]│
├────────────────────┬─────────────────────────────────┤
│ Account list       │ Detail pane                     │
│ ● label / email    │ Identity, tier, token expiry    │
│ ○ ...              │ Quota window remaining/limit    │
│                    │ Token totals (per account)      │
│                    │ [切换为当前] [刷新额度] [改名] [删除]│
├────────────────────┴─────────────────────────────────┤
│ [收录当前 Grok 登录]   status bar                     │
└──────────────────────────────────────────────────────┘
```

- Dark theme
- Highlight current account; list may show short stats (`1.2M tok · 468 req left`)
- Network / process work on background threads; UI remains responsive
- Confirm dialogs for switch (and delete)

## 8. Project layout

```
C:\Ai\grokbuild-tools\
  README.md
  requirements.txt
  pyproject.toml                 # optional packaging
  docs/superpowers/specs/
    2026-07-14-grok-account-manager-design.md
  src/grok_account_manager/
    __init__.py
    __main__.py                  # python -m grok_account_manager
    app.py                       # CustomTkinter UI
    models.py                    # dataclasses / typed dicts
    store.py                     # AccountStore + switch_log + cursor
    auth_bridge.py               # auth.json read/write/backup
    quota.py                     # QuotaProbe + token refresh
    usage.py                     # session scan + attribution
    process.py                   # GrokProcess
  tests/
    test_store.py
    test_auth_bridge.py
    test_usage_attribution.py
    fixtures/                    # redacted sample JSON only
```

### Run

```bash
pip install -r requirements.txt
python -m grok_account_manager
```

## 9. Error handling

| Case | Behavior |
|------|----------|
| Missing `auth.json` | Capture disabled with clear message |
| Expired tokens, refresh fails | Mark account “需重新登录后收录”; keep last known stats |
| Probe HTTP/network error | Show error on account; keep last quota cache |
| Grok process kill/start fails | Credentials still switched; instruct manual restart |
| Partial session parse errors | Skip bad lines; log count; continue |
| Concurrent write to `auth.json` | Atomic replace (write temp + replace); document race if Grok rewrites at same moment |

## 10. Testing strategy

- Unit tests with temp dirs: capture upsert, switch write shape, backup, attribution by `switch_log`
- Fixture `updates.jsonl` snippets with known timestamps and usage objects
- No tests that print or require live production tokens in CI
- Optional manual checklist for live quota probe / process restart on Windows

## 11. Security notes

- Vault is **plaintext JSON** by explicit product choice for v1
- Do not commit `accounts.json`, real `auth.json`, or logs containing secrets
- UI never displays full refresh_token / access token by default (optional “copy token” out of scope for v1)

## 12. Implementation phases (for planning)

1. **Core models + store + auth bridge** (no UI)
2. **Quota probe + usage attribution**
3. **Grok process control**
4. **CustomTkinter UI wiring**
5. **README + manual verification on Windows**

## 13. Open risks

- xAI rate-limit header names / presence may change
- OIDC refresh endpoint details must match what Grok uses; may need adjustment after live testing
- Usage attribution for history before the tool existed is intentionally incomplete
- Auto-restart may fail if Grok was started with special env/cwd; credentials switch still holds

## 14. Decisions log (from product interview)

| Decision | Choice |
|----------|--------|
| Account intake | Capture from current Grok login (`auth.json`) |
| Quota display | Detailed remaining/limit/tier when available |
| UI stack | Python + CustomTkinter |
| Switch while Grok running | Best-effort auto-restart |
| Credential storage | Plaintext JSON vault |
| Architecture | Auth-only swap (Approach A), shared sessions/settings |
| Token stats | Per-account cumulative totals via session logs + switch timeline |
