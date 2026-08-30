# Remove `plan_*` MCP Tools

Status: Implemented and verified on task branch; awaiting explicit user merge authorization.
Branch: `codex/refactor-remove-plan-tools`
Base: `origin/main` at `f16e17351d1bfbf17e098ab554780334a033a1ff`
Review path: Option 3 selected; cross-context consensus complete; native review APPROVE with no findings at design fingerprint `6f4e30e13b0b26e9ce4571168689b4865f98a984d015d18f6085c9f0ac2da0bc`.
Design token ceiling: 1,000,000 main-thread incremental tokens. The current runtime does not expose the required `total_token_usage` baseline, so no numeric baseline is claimed.

## Goal

Remove the six planning-only MCP tools and let the calling model plan internally before invoking tools that perform real work.

The resulting public MCP surface contains seven tools:

- `get_config_info`
- `get_sources`
- `switch_model`
- `toggle_builtin_tools`
- `web_fetch`
- `web_map`
- `web_search`

## Current Contract

The server currently exposes these planning tools:

- `plan_intent`
- `plan_complexity`
- `plan_sub_query`
- `plan_search_term`
- `plan_tool_mapping`
- `plan_execution`

Each planning tool sends model-authored data to the in-memory `PlanningEngine`. The engine does not search, fetch, map, or execute a plan. `web_search`, `web_fetch`, `web_map`, and `get_sources` do not read planning sessions or planning output.

The only repository consumers are:

- the exact registered-tool assertion in `tests/test_regressions.py`;
- planning guidance in `README.md` and `docs/README_EN.md`;
- the planning registration and engine implementation themselves.

`web_search` currently tells the model to call `plan_intent` first even though the server does not enforce or consume that sequence.

## Decision

Delete the complete planning-only path:

1. Remove the six `plan_*` registrations and their planning imports from `server.py`.
2. Delete `planning.py` after confirming no remaining repository imports.
3. Remove the `plan_intent` prerequisite from the `web_search` description.
4. Update the exact tool-surface regression to assert the seven retained tools.
5. Remove planning workflow text from both README variants.
6. Release the change as version `0.2.0` and add a short `CHANGELOG.md`, because tool removal changes the public MCP contract.

No replacement planning tool, compatibility alias, feature flag, or deprecated stub will be added. Any such mechanism would preserve the schema and tool-choice overhead this change is intended to remove.

## User Workflow

Before:

`user query -> six optional planning calls -> web_search/web_fetch/web_map -> answer`

After:

`user query -> model-internal decomposition -> web_search/web_fetch/web_map -> answer`

Simple and narrowly scoped models can call the retained tools directly. A host that needs externally visible approval, resumable execution, or cross-agent plan coordination must implement that as a separate workflow; those capabilities are not provided by this server today.

## Public Contract Change

This is an intentional breaking change to `tools/list`:

- Tool count changes from 13 to 7.
- The six removed `plan_*` names are no longer registered or exposed. Error codes, text, and client presentation for calls to unknown names remain MCP/FastMCP stack behavior and are not part of this server's contract.
- Search, fetch, map, source retrieval, configuration inspection, model switching, and built-in-tool toggling keep their existing names and input contracts.
- No environment variable, credential, transport, or provider API contract changes.

Migration guidance is one sentence: remove calls to all six `plan_*` tools and their explicit planning prerequisites, then invoke the required search or retrieval tool directly.

## File Changes

| File | Change |
| --- | --- |
| `src/grok_search/server.py` | Remove planning imports and six tool functions; shorten `web_search` guidance. |
| `src/grok_search/planning.py` | Delete the now-unreferenced in-memory planning engine. |
| `tests/test_regressions.py` | Change the exact tool list from 13 names to the seven retained names. |
| `README.md` | Remove the planning workflow section and describe direct tool use. |
| `docs/README_EN.md` | Apply the equivalent English documentation change. |
| `pyproject.toml` | Change the package version from `0.1.0` to `0.2.0`. |
| `CHANGELOG.md` | Record the removed tools, unchanged provider contracts, validation evidence, and known timeout limitation. |

## Compatibility And Failure Behavior

The removal must not alter provider request construction, retries, timeout values, source caching, response serialization, or error propagation. It therefore cannot by itself fix the observed 120-second MCP aggregator timeout.

There is no repository evidence that an external client depends on planning state. Because external use cannot be disproved, the release note must identify the six removed names explicitly rather than calling the change transparent.

No fallback will silently translate a planning call into a search call: the planning payload does not contain a stable, unambiguous execution contract.

## Verification

Existing behavior is locked by the exact tool-surface regression, so no new pre-edit test framework or fixture is required.

Before the first runtime edit, capture a temporary structured baseline from FastMCP `list_tools()` in the declared dependency environment. Normalize tools by name and retain every client-visible metadata field for the seven tools that will remain. The artifact is verification evidence, not a committed fixture.

After the edit, capture the same structure and compare it with the baseline:

- The retained names and input schemas must be identical.
- Other client-visible metadata must be identical except for the specified removal of the planning prerequisite from the `web_search` description.
- Any other difference blocks release.

Primary focused proof:

```bash
uv run --extra dev pytest -q tests/test_regressions.py
```

The assertion must observe exactly the seven retained tool names from FastMCP `list_tools()`.

Run a package-level stdio MCP smoke in the same declared dependency environment. It must start `grok-search`, complete MCP initialization, call `tools/list`, and observe exactly the seven retained names. Source compilation or an in-process import cannot replace this check.

Auxiliary checks:

```bash
rg -n "plan_(intent|complexity|sub_query|search_term|tool_mapping|execution)|planning_engine|search_planning" src tests README.md docs pyproject.toml
uv run --extra dev python -m compileall -q src/grok_search
```

The reference search must return no runtime, test, or user-documentation dependency on the removed workflow. Mentions in `CHANGELOG.md` and this design document are expected.

Because this change removes public MCP API registrations and a module imported by the shared server startup path, run the complete existing test suite once after the focused proof succeeds:

```bash
uv run --extra dev pytest -q
```

Do not add a test matrix, stress run, or per-tool dispatch mock by default. If the implementation diff changes a retained tool's decorator, registration arguments, public wrapper, or callable binding, add one minimal call proof for only the affected binding using existing test facilities. No such proof is required when retained bindings remain untouched.

Do not test a particular error code, message, or client presentation for removed tool names; those behaviors are not part of the revised server contract.

## Acceptance Criteria

- `tools/list` exposes exactly the seven retained tools.
- None of the retained tool descriptions requires a `plan_*` call.
- No runtime code imports or stores planning sessions, and the six removed names are not registered or exposed.
- Runtime metadata A/B proves all retained names, input schemas, and client-visible metadata remain unchanged except for the specified `web_search` description edit.
- Package-level stdio MCP initialization and `tools/list` succeed in the declared dependency environment.
- The focused regression, Python compilation check, and complete existing test suite pass.
- If a retained registration or callable binding is touched, its conditional minimal call proof passes.
- Both README variants describe direct tool use consistently.
- Version `0.2.0` and `CHANGELOG.md` identify the breaking removal, tell callers to remove old calls and planning prerequisites, and retain the timeout limitation.
- No secret, endpoint credential, or user query payload is added to source, tests, documentation, commits, or release notes.

## Stop Conditions

Stop implementation and revise this design if any retained runtime path is found to read planning state, if runtime metadata A/B finds an unapproved retained-tool difference, or if a repository test demonstrates a supported planning consumer not identified above.

Reassess the conditional verification scope if implementation touches a retained tool's decorator, registration parameters, public wrapper, or callable binding. Do not silently expand into seven independent dispatch mocks.

Do not add a compatibility layer without a concrete consumer and a separately approved design change.

## VERIFIED_EVIDENCE

FACT — On branch `codex/refactor-remove-plan-tools` from base `f16e17351d1bfbf17e098ab554780334a033a1ff`, the six-file runtime implementation patch has SHA-256 `17c24700e30c440bf1ed7b6ff4324385d650e032d32aff0dfd265960630b80c3` (15 insertions, 411 deletions). Pre-edit retained-tool metadata (`29a4b58ea2b9737692fd4272b37d599840e3e42536a22352071f90bd47aa3595`, 10,368 bytes; 13 total tools) and post-edit all-tool metadata (`8d33d6c60d59d0488a255409173e436b5ad3f221036dc2f12a69483cab41956a`, 10,277 bytes; seven total tools) produced normalized diff SHA-256 `3b70e28062dc82d9c24b03730f38e9e0d4902bf6ca8715dd1e860f369252fa76`: retained names, input schemas, and other client-visible metadata match, with only the approved `web_search.description` change.

FACT — In the same `.venv` with Python 3.12.4, FastMCP 3.4.7, and MCP 1.29.1, the real local package stdio MCP smoke completed initialization and `tools/list` in 0.589 seconds with exit 0, exactly the seven retained names, zero stderr bytes, no traceback or secret pattern, and no business-tool/provider-network call. The focused regression and complete existing suite each collected five tests and passed 5/5 with exit 0; `compileall` exited 0; and the residual planning-name scan of runtime, tests, README variants, and `pyproject.toml` returned no matches. Generated source bytecode from compilation was moved to system trash, and the worktree re-audit found no generated cache, bytecode, lock, egg-info, log, or configuration artifacts.

## DIAGNOSTIC_STATE

HYPOTHESIS — Removing the six planning definitions may reduce model context, latency, and tool-selection overhead, but no controlled before/after measurement has established those benefits. The independently observed 120-second remote aggregator timeout remains outside this change and is not fixed or explained by it.

DESIGN_REVIEW_OPTIONS：设计文档已完成。实现前选择：1）一次原生独立设计审查；2）Web Sol 与 Codex Sol 人工中转互评；3）先互评收敛、经用户确认更新最终文档后再执行一次原生独立设计审查；4）不审查。
