# Shem Browser Tools

A [Shem](https://github.com/thephilip/shem) tool pack that gives LLM agents browser access — navigate, screenshot, extract text, click, type, run JavaScript, save PDFs, and manage tabs.

## Install

```bash
shem-install https://github.com/YOUR_USER/shem-browser-tools
```

Or from a local path:

```bash
shem-install file:///path/to/shem-browser-tools
```

## Usage

A single `browser` tool dispatches by `action`:

```
browser_navigate: navigate to a URL and return sanitized page text
  action: "navigate", url: "https://example.com"

browser_screenshot: capture a screenshot (selector optional, defaults to full page)
  action: "screenshot", selector: "#main"          (optional)

browser_extract: extract text from CSS selectors
  action: "extract", selectors: ["h1", ".content"]

browser_click: click an element
  action: "click", selector: "#submit-btn"

browser_type: type text into an input
  action: "type", selector: "#search", text: "hello"

browser_evaluate: run JavaScript in the page
  action: "evaluate", script: "document.title"

browser_pdf: save page as PDF
  action: "pdf", path: "/tmp/page.pdf"             (optional, defaults to temp)

browser_list_tabs: list open tabs (live-connect mode only)
  action: "list_tabs", connect_url: "ws://..."

browser_switch_tab: switch to a tab (live-connect mode only)
  action: "switch_tab", tab_id: 2, connect_url: "ws://..."
```

All actions accept an optional `connect_url` parameter. When provided, the tool connects to a running browser via Playwright's CDP WebSocket endpoint. When omitted, it launches a headless Firefox (configurable via `SHEM_BROWSER_TYPE=chromium` or `webkit`).

## Live-connect mode

To attach to your running Zen/Firefox or Chrome:

1. Start your browser with remote debugging:
   - **Firefox/Zen:** `firefox --remote-debugging-port 9222`
   - **Chrome:** `google-chrome --remote-debugging-port=9222`
2. Pass the WebSocket URL as `connect_url`:
   ```
   browser_list_tabs: list_tabs, connect_url: "ws://127.0.0.1:9222/..."
   ```

## Requirements

- **Python 3** with `playwright` installed: `pip install playwright && playwright install`
- **Podman or Docker** (recommended) for sandboxed execution. Falls back to host with a warning if neither is detected.

## Prompt Injection Defense

This pack integrates sanitization logic from [Sump](https://github.com/thephilip/sump) (Apache 2.0). All page text returned to the LLM is:

1. Stripped of invisible Unicode tag characters (`U+E0000–U+E007F`)
2. Scanned against known prompt injection patterns
3. Wrapped in `<untrusted>...</untrusted>` tags for non-whitelisted domains

Configure via `~/.config/shem/sump-config.json`:

```json
{
  "whitelist": ["docs.example.com"],
  "domains": ["pastebin.com"],
  "patterns": ["forget everything"]
}
```

## License

Apache 2.0 — see LICENSE and NOTICE.
