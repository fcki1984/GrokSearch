# Changelog

## 0.2.0 - 2026-08-30 (task branch, unreleased; no tag)

Plan-tool removal runtime patch artifact: `17c24700e30c440bf1ed7b6ff4324385d650e032d32aff0dfd265960630b80c3` (SHA-256).

Grok SSE fix source/test diff artifact: `1858b59e2ca966039fcb3a5be718158c82b6f45a7162c7bc7eb22d66cfbbb2ee` (SHA-256).

- Breaking MCP contract change: `tools/list` now exposes seven tools instead of 13. Removed `plan_intent`, `plan_complexity`, `plan_sub_query`, `plan_search_term`, `plan_tool_mapping`, and `plan_execution`; retained `get_config_info`, `get_sources`, `switch_model`, `toggle_builtin_tools`, `web_fetch`, `web_map`, and `web_search`.
- Migration: remove calls to the six deleted `plan_*` tools and their explicit planning prerequisites, then invoke the required search or retrieval tool directly.
- Persistence: removed the in-memory planning engine and planning-session state. No environment variable, credential, transport, provider API, or persistent-storage contract changed.
- Fixed: an HTTP 200 SSE top-level Grok error now propagates immediately as an explicit provider error containing only its code, type, and message instead of being treated as an empty success; transport and HTTP status retries are unchanged.
- Privacy: Grok diagnostic logging now records request and response length metadata instead of content, and stream error details accept only bounded scalar `code`, `type`, and `message` values.
- Verified with Python 3.12.4, FastMCP 3.4.7, and MCP 1.29.1: pre/post metadata SHA-256 `29a4b58ea2b9737692fd4272b37d599840e3e42536a22352071f90bd47aa3595` / `8d33d6c60d59d0488a255409173e436b5ad3f221036dc2f12a69483cab41956a` produced diff SHA-256 `3b70e28062dc82d9c24b03730f38e9e0d4902bf6ca8715dd1e860f369252fa76`, matching retained metadata except for the approved `web_search.description` change; package stdio initialization and `tools/list` exited 0 in 0.589 seconds with exactly the seven retained names and zero stderr; the focused regression and complete existing suite each collected five tests and passed 5/5 with exit 0; `compileall` exited 0; and the planning-name residual scan found no matches in runtime, tests, README variants, or `pyproject.toml`.
- Verified the Grok SSE fix: the synthetic same-shape pre-fix probe was `RED_CONFIRMED`; after the fix, the complete existing `tests/test_regressions.py` suite collected six tests and passed 6/6 with exit 0, `py_compile` exited 0, and `git diff --check` exited 0.
- Known issues: external consumers of the removed tools remain unknown, so migration is breaking. The post-fix remote target environment has not been retested, and other provider or aggregator timeout causes remain unresolved. Context, latency, and tool-selection benefits remain hypotheses.
