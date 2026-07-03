# Shem Browser Tools — Design Spec

## Purpose

A Shem tool pack that gives LLM agents the ability to navigate, extract, screenshot, and interact with web pages — both in isolated headless browsers and connected to the user's running browser session.

## Approach

**Hybrid (Approach C).** Tools default to launching their own headless browser (standalone mode). Every tool accepts an optional `connect_url` parameter — when provided, it attaches to the user's running browser via remote debugging (Chrome DevTools Protocol or Firefox BiDi). This allows agents to either work autonomously or see what the user sees.

## Language & Runtime

Python, container-sandboxed via Shem's Python tool runner. Dependency: `playwright` (installed at pack install time or detected at runtime).

## Tool Surface

A single `browser` tool with an `action` parameter dispatches to internal handler functions. Keeps all shared code (Playwright connection, injection sanitization) in one file — no duplication, single maintenance point.

| Action | Parameters | Returns |
|--------|-----------|---------|
| `navigate` | `url` (required), `connect_url` (optional) | `title`, `text` (sanitized), `url` (final) |
| `screenshot` | `selector` (optional, default full page), `connect_url` | `screenshot` (base64 PNG) |
| `extract` | `selectors` (list), `connect_url` | `{selector: text[]}` |
| `click` | `selector`, `connect_url` | `{"clicked": selector}` |
| `type` | `selector`, `text`, `connect_url` | `{"typed": text}` |
| `evaluate` | `script`, `connect_url` | `result` (JSON) |
| `pdf` | `path` (optional, default temp), `connect_url` | `path` |
| `list_tabs` | `connect_url` (required) | `{tabs: [{id, title, url}]}` |
| `switch_tab` | `tab_id`, `connect_url` (required) | `{tab_id, title, url}` |

## Prompt Injection Defense

Ported from [Sump](https://github.com/thephilip/sump) (Apache 2.0). Every tool that returns page content runs it through `sump.sanitize()`:

1. **Invisible Unicode stripping** — removes `\u{E0000}–\u{E007F}` tag characters
2. **Injection pattern scan** — 23+ regex patterns from Sump's blacklist
3. **Untrusted wrapping** — content from non-whitelisted domains wrapped in `<untrusted>...</untrusted>`
4. **Local config** — whitelist/blacklist at `~/.config/shem/sump-config.json`

If patterns match, `[FLAGGED: injection patterns detected]` is appended to the output.

## Architecture

```
shem-browser-tools/
├── pack.json              # manifest: "browser-tools", lists tool IDs
├── README.md              # usage, install, attribution
├── LICENSE                # Apache 2.0
├── NOTICE                 # Apache 2.0 attribution for Sump
└── tools/
    ├── browser.json       # tool manifest (single tool, 9 actions)
    └── browser.py         # Python source — dispatch + shared logic
```

### `browser.py` internals

The single file contains all logic, organized as:

```
# 1. Shared utilities
#    - connect_or_launch(connect_url) -> context manager yielding Playwright Page
#    - sanitize(text, domain) -> (cleaned_text, flagged)
#    - load_sump_config() -> dict for whitelist/blacklist

# 2. Action handlers (one function per action)
#    _navigate(page, url, ...)  -> {title, text, url}
#    _screenshot(page, ...)     -> {screenshot}
#    _extract(page, selectors)  -> {selector: [text]}
#    _click(page, selector)     -> {clicked}
#    _type(page, selector, text) -> {typed}
#    _evaluate(page, script)    -> {result}
#    _pdf(page, ...)            -> {path}
#    _list_tabs(page)           -> {tabs: [...]}
#    _switch_tab(page, tab_id)  -> {tab_id, title, url}

# 3. top-level run(args: dict)
#    dispatches to the right handler based on args["action"]
```

Pattern set seeded from Sump's `sump-blacklist.json`. Config file at `~/.config/shem/sump-config.json` with shape: `{domains: string[], patterns: string[], whitelist: string[]}`.

## Error Handling

- Browser launch failure → `{"error": "...", "hint": "Install Playwright: pip install playwright && playwright install"}`
- No container runtime → `{"warn": "No container runtime detected (podman/docker). Running on host — Playwright must be installed manually.", "hint": "pip install playwright && playwright install"}`
- Navigation timeout → `{"error": "timeout after 30s", "url": url}`
- Selector not found → `{"error": "selector not found", "selector": selector}`
- Connection refused (live mode) → `{"error": "cannot connect", "hint": "Start browser with --remote-debugging-port=9222"}`
- Injection flagged → output still returned, with `[FLAGGED]` suffix, LLM decides how to handle

## Browser Support

| Browser | Standalone mode | Live-connect mode |
|---------|----------------|-------------------|
| Firefox (Zen) | Playwright Firefox | Remote debugging on `localhost:9222` |
| Chrome/Chromium | Playwright Chromium | CDP on `localhost:9222` |
| Edge | Playwright Chromium | CDP on `localhost:9222` |

Standalone default: Firefox (matches Zen). Configurable via env var `SHEM_BROWSER_TYPE`.

## Attribution

The sanitization logic in `browser.py` is adapted from [Sump](https://github.com/thephilip/sump) (Apache 2.0). See `LICENSE` and `NOTICE` files for full copyright notice.

## Constraints & Open Issues

### Playwright availability
Shem's Python tool runner defaults to `python:3.12-slim` with `--network=none` when a container runtime is present. Playwright is not available in that image. Resolution options (in order of practicality):

1. **Host fallback** — If no container runtime is detected, Shem runs the tool directly on the host. User installs Playwright once: `pip install playwright && playwright install`. This is the expected path for most users.
2. **Custom container image** — User builds an image with Playwright pre-installed and configures Shem to use it (future enhancement).

### test_source limitations
The graduation gate runs each tool's `test_source` in an isolated executor — no display, no network, no browser. The test must validate:
- All imports resolve (playwright is available)
- Dispatch handles all 9 actions and returns proper error shapes for invalid args
- Sanitization logic works correctly on sample text

The test cannot launch a real browser. This is acceptable — the gate validates code integrity, not end-to-end behavior.

### Connect URL format
`connect_url` accepts a Playwright WebSocket endpoint. The format differs by browser:

| Browser | Flag to start | `connect_url` format |
|---------|--------------|---------------------|
| Chrome/Edge | `--remote-debugging-port=9222` | `ws://127.0.0.1:9222/devtools/browser/<id>` |
| Firefox/Zen | `--remote-debugging-port 9222` | `ws://127.0.0.1:9222/...` (Firefox CDP path) |

The tool will attempt Playwright's standard `browser.connect()` — if the URL is wrong, it returns a clear error. Future: support `http://` URLs for Chrome's HTTP endpoint (auto-resolve WebSocket URL).

## Anti-features (YAGNI)

- No form-filling heuristics (click + type covers it)
- No HAR/network recording
- No multi-page spidering
- No visual diffing
- No cookie/profile management
