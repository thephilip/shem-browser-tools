# Session Persistence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add session persistence to the browser tool so headless browsing preserves cookies, localStorage, and auth state across tool calls.

**Architecture:** A single `profile` parameter on all actions triggers Playwright's `launch_persistent_context`, storing the full browser profile at `~/.config/shem/profiles/<name>/`. Two new actions (`list_profiles`, `clear_profile`) provide management. Profile name validated against `[a-zA-Z0-9_-]+` to prevent path traversal.

**Tech Stack:** Python 3, Playwright (sync API) with `launch_persistent_context`

---

### Task 1: Modify browser.py — persistence support

**Files:**
- Modify: `tools/browser.py`

This task refactors `connect_or_launch` to `get_page` with profile support, adds two new handlers, a `_dir_size` helper, a `_pick_browser_type` helper, and updates validation/dispatch.

- [ ] **Step 1: Add PROFILES_DIR constant and _pick_browser_type helper**

After `SUMP_CONFIG_PATH` definition, add:

```python
PROFILES_DIR = os.path.expanduser("~/.config/shem/profiles")


def _pick_browser_type():
    bt = os.environ.get("SHEM_BROWSER_TYPE", "firefox").lower()
    return bt if bt in ("chromium", "webkit") else "firefox"
```

- [ ] **Step 2: Refactor connect_or_launch to get_page with profile support**

Replace the existing `connect_or_launch` function:

```python
@contextmanager
def get_page(connect_url=None, profile=None):
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = None
    context = None
    try:
        browser_type = _pick_browser_type()
        engine = getattr(pw, browser_type)

        if connect_url:
            browser = pw.chromium.connect_over_cdp(connect_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
        elif profile:
            profile_dir = os.path.join(PROFILES_DIR, profile)
            os.makedirs(profile_dir, exist_ok=True)
            context = engine.launch_persistent_context(
                user_data_dir=profile_dir, headless=True
            )
            page = context.pages[0] if context.pages else context.new_page()
        else:
            browser = engine.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
        yield page
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        pw.stop()
```

- [ ] **Step 3: Add _dir_size helper and new action handlers**

Add after `_handle_switch_tab`:

```python
def _dir_size(path):
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _handle_list_profiles(page, args):
    if not os.path.isdir(PROFILES_DIR):
        return {"profiles": []}
    profiles = []
    for name in sorted(os.listdir(PROFILES_DIR)):
        path = os.path.join(PROFILES_DIR, name)
        if os.path.isdir(path):
            size = _dir_size(path)
            entry = {"name": name, "size_bytes": size}
            if size > 100_000_000:
                entry["warning"] = "profile exceeds 100 MB"
            profiles.append(entry)
    return {"profiles": profiles}


def _handle_clear_profile(page, args):
    name = args.get("profile")
    if not name or not re.match(r"^[a-zA-Z0-9_-]+$", name):
        return {"error": "invalid profile name"}
    path = os.path.join(PROFILES_DIR, name)
    if not os.path.isdir(path):
        return {"error": f"profile not found: {name}"}
    shutil.rmtree(path)
    return {"cleared": name}
```

- [ ] **Step 4: Update _validate with new actions**

In `_validate`, add to the `required` dict:

```python
"list_profiles": [],
"clear_profile": ["profile"],
```

- [ ] **Step 5: Update HANDLERS dict**

Add the new entries:

```python
"list_profiles": _handle_list_profiles,
"clear_profile": _handle_clear_profile,
```

- [ ] **Step 6: Update run() with profile validation and new get_page call**

In `run()`, replace the `connect_or_launch(connect_url)` call with `get_page(connect_url, profile)`. Add profile validation before the browser call. The full updated `run()` function:

```python
def run(args):
    action = args.get("action")
    if not action:
        return {"error": "missing 'action' field"}

    handler = HANDLERS.get(action)
    if not handler:
        return {"error": f"unknown action: {action}"}

    err = _validate(action, args)
    if err:
        return {"error": err}

    connect_url = args.pop("connect_url", None)
    profile = args.get("profile")
    if profile and not re.match(r"^[a-zA-Z0-9_-]+$", profile):
        return {"error": "invalid profile name (use [a-zA-Z0-9_-]+)"}

    container_warning = None
    if not connect_url and not shutil.which("podman") and not shutil.which("docker"):
        container_warning = "No container runtime detected (podman/docker). Running on host — Playwright must be installed manually."

    try:
        with get_page(connect_url, profile) as page:
            result = handler(page, args)

        if container_warning and "error" not in result:
            result["warn"] = container_warning

        return result
    except ImportError:
        return {"error": "playwright is not installed", "hint": "pip install playwright && playwright install"}
    except Exception as e:
        return {"error": str(e)}
```

- [ ] **Step 7: Run existing tests to make sure refactor didn't break anything**

```bash
cd /home/philip/Downloads/_project/shem-toolpacks/shem-browser-tools/tools && ln -sf browser.py tool.py && python -m pytest test_browser.py -v && rm -f tool.py
```

Expected: all 16 tests pass.

- [ ] **Step 8: Commit**

```bash
git add tools/browser.py
git commit -m "feat: add session persistence via persistent browser contexts"
```

---

### Task 2: Update browser.json — schema + test_source

**Files:**
- Modify: `tools/browser.json`

- [ ] **Step 1: Add profile param and new actions to schema**

Edit `schema.properties` to add the `profile` parameter:

```json
"profile": {
  "type": "string",
  "description": "Profile name for persistent browser context (headless only, ignored in live-connect mode)"
}
```

Add `"list_profiles"` and `"clear_profile"` to the `action` enum:

```json
"enum": [
  "navigate", "screenshot", "extract", "click", "hover", "scroll",
  "wait", "type", "evaluate", "pdf", "list_tabs", "switch_tab",
  "is_visible", "select",
  "list_profiles", "clear_profile"
]
```

- [ ] **Step 2: Append new test functions to test_source**

In the `test_source` string, add after the existing tests (before the closing quote):

```python
\ndef test_list_profiles_no_dir():\n    result = run({"action": "list_profiles"})\n    assert result == {"profiles": []}\n\ndef test_clear_profile_requires_name():\n    result = run({"action": "clear_profile"})\n    assert "profile" in result.get("error", "")\n\ndef test_clear_profile_invalid_name():\n    result = run({"action": "clear_profile", "profile": "../evil"})\n    assert "invalid" in result.get("error", "").lower()\n\ndef test_clear_profile_not_found():\n    result = run({"action": "clear_profile", "profile": "nonexistent"})\n    assert "not found" in result.get("error", "")\n\ndef test_profile_param_validation_rejects_traversal():\n    result = run({"action": "navigate", "profile": "../evil"})\n    assert "invalid profile" in result.get("error", "").lower()\n\ndef test_profile_param_accepts_valid_name():\n    result = run({"action": "navigate", "profile": "my-session_1"})\n    # will fail at missing url, but should not fail on profile name\n    assert "invalid profile" not in result.get("error", "").lower()
```

- [ ] **Step 3: Commit**

```bash
git add tools/browser.json
git commit -m "feat: update schema with profile param and profile management actions"
```

---

### Task 3: Update test_browser.py — sync with test_source

**Files:**
- Modify: `tools/test_browser.py`

- [ ] **Step 1: Add missing tests to test_browser.py**

Append to the end of the file:

```python
def test_is_visible_requires_selector():
    result = run({"action": "is_visible"})
    assert "selector" in result.get("error", "")

def test_select_requires_selector():
    result = run({"action": "select"})
    assert "selector" in result.get("error", "")

def test_select_requires_value_or_label():
    result = run({"action": "select", "selector": "#s"})
    assert "value" in result.get("error", "") or "label" in result.get("error", "")

def test_list_profiles_no_dir():
    result = run({"action": "list_profiles"})
    assert result == {"profiles": []}

def test_clear_profile_requires_name():
    result = run({"action": "clear_profile"})
    assert "profile" in result.get("error", "")

def test_clear_profile_invalid_name():
    result = run({"action": "clear_profile", "profile": "../evil"})
    assert "invalid" in result.get("error", "").lower()

def test_clear_profile_not_found():
    result = run({"action": "clear_profile", "profile": "nonexistent"})
    assert "not found" in result.get("error", "")

def test_profile_param_validation_rejects_traversal():
    result = run({"action": "navigate", "profile": "../evil"})
    assert "invalid profile" in result.get("error", "").lower()

def test_profile_param_accepts_valid_name():
    result = run({"action": "navigate", "profile": "my-session_1"})
    assert "invalid profile" not in result.get("error", "").lower()
```

- [ ] **Step 2: Run all tests**

```bash
cd /home/philip/Downloads/_project/shem-toolpacks/shem-browser-tools/tools && ln -sf browser.py tool.py && python -m pytest test_browser.py -v && rm -f tool.py
```

Expected: all 25 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tools/test_browser.py
git commit -m "test: add tests for persistence feature"
```

---

### Task 4: Update README — document session persistence

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add persistence section to README**

Add after the `Prompt Injection Defense` section:

```markdown
## Session Persistence

Headless browser sessions can preserve state (cookies, localStorage, auth sessions)
across calls using named **profiles**. Pass `profile` to any action:

```
browser_navigate: navigate, url: "https://github.com/login", profile: "github-session"
browser_navigate: navigate, url: "https://github.com/settings", profile: "github-session"
```

The second call reuses the same profile — you stay logged in.

Profiles live at `~/.config/shem/profiles/<name>/`. Manage them:

| Action | Parameters | Description |
|--------|-----------|-------------|
| `list_profiles` | none | List saved profiles with size; warns if >100 MB |
| `clear_profile` | `profile` | Delete a profile directory |

Live-connect mode (`connect_url`) ignores the `profile` parameter — the running
browser owns its own state.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document session persistence with profiles"
```

---

### Self-review checklist

- [ ] Task 1: `PROFILES_DIR` constant, `_pick_browser_type()`, `get_page()` with profile support, `_dir_size()`, `_handle_list_profiles`, `_handle_clear_profile`, validation updates, dispatch updates, all in `browser.py`
- [ ] Task 2: `profile` in `browser.json` schema, `list_profiles`/`clear_profile` in enum, new tests in `test_source`
- [ ] Task 3: `test_browser.py` synced with `test_source`
- [ ] Task 4: README documents profiles section
- [ ] All 25 tests pass
- [ ] `profile` param validated before browser connection — no path traversal
- [ ] `list_profiles` with no dir → `{"profiles": []}`
- [ ] `clear_profile` validates name format before deletion
- [ ] Live-connect mode ignores `profile` — `connect_url` branch checked first
- [ ] Size warning at 100 MB threshold in `list_profiles`
