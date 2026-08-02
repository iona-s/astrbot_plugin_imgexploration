# AGENTS.md

## Scope

This file applies to the `astrbot_plugin_imgexploration` repository.

The plugin supports image searches from command messages, replied-to images,
and follow-up images submitted during a command wait. It also captures remote
image URLs for session context and exposes optional LLM tools.

Keep changes minimal, compatible with existing adapters, and suitable for
public maintenance. This directory is an independent Git repository even when
checked out under an AstrBot installation. Do not run parent-repository setup,
formatting, release, or commit commands unless the task explicitly includes
that repository.

## Working Rules

- Prefer existing plugin APIs and patterns over new abstractions.
- Keep fixes focused. Separate capture, command interaction, session behavior,
  provider, persistence, and framework changes unless the task requires them
  together.
- Do not modify AstrBot framework or adapter files for a plugin-level fix.
- Treat adapter-specific event data as optional and untrusted.
- Update tests and user documentation when behavior or configuration changes.
- During behavior-preserving refactors, retain useful comments and docstrings;
  edit them only when ownership, names, or behavior make the original wording
  inaccurate.

## Repository Map

- `main.py`: plugin lifecycle, message listener, command flow, and LLM tools.
- `core/image_context.py`: image records, session isolation, retention, and
  retrieval.
- `core/image_sources.py`: component, raw-event, and reply image source
  resolution.
- `core/image_wait.py`: command wait isolation, timeout, cancellation, and
  lifecycle coordination.
- `core/service.py`: search-strategy orchestration and result aggregation.
- `core/result_sender.py`: result formatting, platform delivery, retries, and
  fallback behavior.
- `core/strategy.py` and `core/providers/*_strategy.py`: provider interface and
  implementations.
- `core/utils.py`: image resolution, uploads, downloads, and shared HTTP
  resources.
- `_conf_schema.json`: plugin configuration schema.
- `metadata.yaml`: plugin metadata.
- `tests/test_plugin_imports.py`: plugin entry-point and core-layout import
  smoke checks.
- `tests/test_image_capture.py`: component and raw-event capture behavior.
- `tests/test_image_sources.py`: source validation, ordering, raw extraction,
  and reply fallback.
- `tests/test_command_search.py`: immediate command and reply searches.
- `tests/test_result_sender.py`: result components, retries, and delivery
  fallback.
- `tests/test_image_wait.py`: wait configuration and coordinator state machine.
- `tests/test_image_wait_flow.py`: plugin wait flow, lifecycle, and termination.
- `tests/test_logging.py`: image URL log levels and diagnostic wording.

For capture work, start with `on_message()` in `main.py`. For command behavior,
start with `search_image_cmd()` and `_run_command_search()`. Change
`core/image_context.py` only when the task requires different context or
session behavior. For result delivery, start with
`core/result_sender.py::send_search_results()`. For command-wait state and
lifecycle, start with `core/image_wait.py::ImageWaitCoordinator`.

## Image Source Contract

Command searches resolve sources in this order:

1. The first image attached to the command message.
2. The first image in the replied-to message.
3. The next image submitted through an active command wait.

Within an AstrBot `Image`, validate `url` and `file` independently. Preserve
candidate order, prefer HTTP(S) candidates, and try non-HTTP values through the
existing image resolver only when the relevant flow supports uploads.

The message listener captures context URLs in this order:

1. HTTP(S) `Image.url`.
2. HTTP(S) `Image.file`.
3. HTTP(S) `data.url` from guarded raw OneBot image segments.

Apply these rules:

- Keep the generic AstrBot component path primary for non-OneBot adapters.
- Deduplicate identical candidate strings within one event.
- Do not claim content-level or cross-event deduplication.
- Do not persist local temporary paths in image context unless that contract is
  deliberately extended.
- Missing or malformed raw data must not break generic component extraction.
- Reply chains, forwarded nodes, and file-token resolution are separate
  features and require explicit tests.

Access `event.message_obj.raw_message` defensively. It may be absent, an object,
or a mapping. Inspect only mapping-like image segments with mapping-like `data`
and a valid HTTP(S) `data.url`.

Store event metadata through supported APIs:

```python
message_id = str(getattr(event.message_obj, "message_id", "") or "")
sender_id = str(event.get_sender_id() or "")
```

Missing metadata must not prevent image capture.

## Command And Wait Contract

- Validate requested strategies before starting a search or creating a wait.
- Keep `_run_command_search()` as the shared execution path for immediate and
  waited images.
- Send one user-visible search acknowledgement before conversion or provider
  network requests.
- `_run_command_search()` returns `None` after sending successful results and a
  user-facing string for terminal errors.
- A wait is isolated by `(unified_msg_origin, sender_id)` and retains the
  validated strategy selection.
- An existing active wait rejects another wait command for the same key.
- Only the next non-command image for the same key atomically consumes a wait.
  Text, other senders, and other sessions do not consume it.
- A command message must not consume an existing wait in `on_message()`;
  otherwise an attached command image can be searched twice.
- Timeouts resolve the waiting command through its Future. Plugin termination
  clears and resolves all wait states.
- Merely having an active wait must not stop text, unrelated, command, or late
  image events.
- Capture a successfully consumed image into plugin context first, run its
  search inside the consuming listener, and then stop further event
  propagation.
- Command-flow changes must not alter LLM-tool behavior unless explicitly in
  scope.

## Sessions And Privacy

Image context supports its configured `session` and `global` isolation modes.
Command waits always remain isolated by conversation and sender. Do not change
either privacy boundary as a side effect of an unrelated fix.

When session behavior changes, verify at least:

- the initiating sender in the same conversation;
- another member in the same group;
- the same sender in another conversation;
- plugin termination and expired state cleanup.

## Logging

- Keep routine capture logs at debug level.
- Keep search triggers, selected strategies, result counts, and elapsed time at
  info level without including complete or truncated target image URLs.
- Complete image URLs, including their query strings, may be logged at debug
  level when they materially help diagnose image capture or search-source
  issues.
- Prefer counts, source labels, session IDs, message IDs, sender IDs, and image
  IDs over raw event dumps.
- Never separately log base64 or data-URI image content, API keys, cookies,
  credentials, bearer tokens, authorization headers, complete private raw
  events, or private message content.
- Before sharing logs in an issue, Discussion, or other public channel, redact
  complete image URLs and unrelated user identifiers.
- Keep warnings concise and avoid platform access parameters.

## Verification

Run checks in proportion to the changed behavior:

- Capture or raw-event extraction: `tests/test_image_capture.py`.
- Source validation, candidate ordering, raw parsing, or reply fallback:
  `tests/test_image_sources.py`.
- Immediate commands, reply lookup, source selection, or result sending:
  `tests/test_command_search.py`.
- Result formatting, merged-forward retry, or delivery fallback:
  `tests/test_result_sender.py`.
- Wait creation, timeout, isolation, cleanup, or concurrency:
  `tests/test_image_wait.py` and `tests/test_image_wait_flow.py`.
- Shared service or provider changes: run all affected tests and add focused
  provider coverage where practical.

Tests should call production methods and use asynchronous mocks rather than
copying implementation logic. For Python changes, run the focused tests and,
before handoff, the complete branch-coverage report and hooks:

```text
python -m pytest --cov=astrbot_plugin_imgexploration --cov-branch --cov-report=term-missing
pre-commit run --all-files
```

Coverage is review evidence only. Do not add a fail-under threshold or commit
generated coverage reports unless the project policy changes explicitly.

Review hook auto-fixes before proceeding. For documentation-only changes,
running the configured hooks against the changed files is sufficient.

Run live AstrBot checks only when the required services and chat access are
available. Before claiming a live fix, test after a complete AstrBot process
restart. Record the AstrBot, adapter, Python, plugin, operating-system,
container, and session settings that materially affect the result. Clearly
state which live checks were not run.

## Completion And Contributions

A change is ready when its behavior is covered at the appropriate boundary,
generic adapter behavior is preserved, logs follow the level and disclosure
rules above, and unrelated command, context, provider, persistence, and
framework behavior is unchanged.

Commit message subjects should preferably use English, but Chinese is also
allowed. They must use this exact format:

```text
<type>: <summary>
```

Use a lowercase Conventional Commit type such as `fix`, `feat`, `docs`, `test`,
`refactor`, or `chore`, followed by one ASCII colon and one space. Do not add a
scope. For example:

```text
fix: 修复图片来源解析
```

Prefer one concern per commit, and split tests only when that improves
reviewability.
