# Grok Account Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Windows desktop app that captures N SuperGrok accounts from `~/.grok/auth.json`, shows quota + per-account token totals, and one-click switches Grok Build login without touching sessions/settings.

**Architecture:** CustomTkinter UI over pure-Python services. Only `auth.json` is mutated for switches; credentials live in `~/.grok-account-manager/accounts.json`; usage is attributed via `switch_log` + read-only scan of `~/.grok/sessions/**/updates.jsonl`.

**Tech Stack:** Python 3.11+, CustomTkinter, urllib (stdlib HTTP), pytest, Windows process control via `psutil` + `subprocess`.

**Spec:** `docs/superpowers/specs/2026-07-14-grok-account-manager-design.md`

---

## File map

| Path | Responsibility |
|------|----------------|
| `requirements.txt` | Runtime deps |
| `pyproject.toml` | Package metadata / pytest config |
| `README.md` | Install, run, safety notes |
| `src/grok_account_manager/__init__.py` | Version |
| `src/grok_account_manager/__main__.py` | `python -m grok_account_manager` |
| `src/grok_account_manager/paths.py` | Resolve `~/.grok` and app data dirs |
| `src/grok_account_manager/models.py` | Dataclasses: Account, Quota, UsageStats, SwitchEntry |
| `src/grok_account_manager/store.py` | Load/save accounts, switch_log, settings, cursors |
| `src/grok_account_manager/auth_bridge.py` | Read/write/backup `auth.json`, capture + switch |
| `src/grok_account_manager/quota.py` | Token refresh + rate-limit probe |
| `src/grok_account_manager/usage.py` | Session usage scan + attribution |
| `src/grok_account_manager/process.py` | Find/kill/start grok |
| `src/grok_account_manager/app.py` | CustomTkinter UI |
| `tests/test_*.py` | Unit tests with temp dirs |
| `tests/fixtures/` | Redacted sample updates.jsonl |

---

### Task 1: Scaffold + models + paths

**Files:**
- Create: `requirements.txt`, `pyproject.toml`, `src/grok_account_manager/__init__.py`, `paths.py`, `models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Create package skeleton and dependencies**

`requirements.txt`:
```
customtkinter>=5.2.0
psutil>=5.9.0
```

`pyproject.toml`:
```toml
[project]
name = "grok-account-manager"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["customtkinter>=5.2.0", "psutil>=5.9.0"]

[project.scripts]
grok-account-manager = "grok_account_manager.__main__:main"

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: Implement `models.py` and `paths.py`**

- `AppPaths`: `grok_home`, `auth_json`, `sessions_dir`, `app_home`, `accounts_json`, `switch_log_json`, `usage_cursor_json`, `settings_json`
- Dataclasses with `to_dict` / `from_dict`:
  - `UsageStats`: token counters + `last_sync_at`
  - `QuotaInfo`: limit/remaining requests & tokens, tier, expires_at, last_probed_at, error
  - `Account`: id/user_id, label, email, team_id, auth_entry dict, stats, quota, timestamps
  - `SwitchEntry`: user_id, at_unix, source

- [ ] **Step 3: Tests for serialize round-trip; commit**

---

### Task 2: AccountStore

**Files:**
- Create: `src/grok_account_manager/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Failing tests** — upsert by user_id, rename, delete, switch_log append, settings get/set, load empty defaults
- [ ] **Step 2: Implement atomic JSON write** (`tmp` + `replace`)
- [ ] **Step 3: Pass tests; commit**

---

### Task 3: AuthBridge (capture + switch)

**Files:**
- Create: `src/grok_account_manager/auth_bridge.py`
- Test: `tests/test_auth_bridge.py`

Behavior:
- `read_auth()` → dict or empty
- `current_identity()` → user_id, email, entry key, raw entry
- `capture_current(store)` → Account upsert from auth.json
- `switch_to(store, account_id, auto_capture_current=True)` → backup, write target entry only, switch_log, update last_used
- Never touch sessions/config

Atomic write of auth.json: write `.tmp` then `os.replace`.

- [ ] **Step 1: Tests with temp grok home fixture**
- [ ] **Step 2: Implement; pass; commit**

---

### Task 4: UsageStats scanner

**Files:**
- Create: `src/grok_account_manager/usage.py`, `tests/fixtures/sample_updates.jsonl`
- Test: `tests/test_usage.py`

- Walk `sessions/**/updates.jsonl`
- Parse lines with `params.update.usage` / `inputTokens`
- Attribute via switch_log binary search / reverse scan
- Update account.stats; maintain unassigned bucket in store
- Incremental cursor optional but preferred

- [ ] **Step 1: Fixture + tests for attribution boundary**
- [ ] **Step 2: Implement; pass; commit**

---

### Task 5: QuotaProbe

**Files:**
- Create: `src/grok_account_manager/quota.py`
- Test: `tests/test_quota.py` (mock urllib)

- Decode JWT exp without verifying signature (stdlib base64)
- Refresh token if needed (OIDC token endpoint discovery or known auth.x.ai paths; implement with clear failure path)
- POST minimal chat completion; parse rate-limit headers
- Update account.quota in store

- [ ] **Step 1: Unit tests with mocked HTTP**
- [ ] **Step 2: Implement; pass; commit**

---

### Task 6: GrokProcess

**Files:**
- Create: `src/grok_account_manager/process.py`
- Test: `tests/test_process.py` (mock psutil)

- `list_grok_pids()`, `restart_grok(timeout_secs=8)` → status message
- Launch candidate: `Path.home()/".grok/bin/grok.exe"`, then `shutil.which("grok")`

- [ ] **Step 1: Tests with mocks**
- [ ] **Step 2: Implement; pass; commit**

---

### Task 7: UI + entrypoint

**Files:**
- Create: `src/grok_account_manager/app.py`, `__main__.py`
- Create: `README.md`

UI actions (background threads + `after()` UI updates):
- Capture current
- Select account → detail (quota + stats)
- Switch (confirm) → AuthBridge + restart
- Refresh quota (one / all)
- Sync usage stats
- Rename / delete

- [ ] **Step 1: Implement UI**
- [ ] **Step 2: `python -m grok_account_manager` smoke launch**
- [ ] **Step 3: README; commit**

---

### Task 8: End-to-end verification

- [ ] Run full pytest
- [ ] Manual: capture current account, show identity, sync stats if sessions exist
- [ ] Confirm switch only changes auth.json (diff mtimes of config.toml / sessions)

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| Capture from auth.json | 3 |
| Multi-account vault plaintext | 2 |
| Switch only auth.json + backup | 3 |
| Auto-restart grok | 6 |
| Quota detail | 5 |
| Per-account token totals | 4 |
| Unassigned usage | 4 |
| CustomTkinter UI | 7 |
| No session mutation | 3, 4 |
| README / safety | 7 |

## Execution note

Prefer **inline execution** in one session for greenfield scaffolding speed; keep TDD for pure logic modules (store, auth, usage, quota parse).
