# Session Persistence — Design Spec

## Purpose

Allow an LLM agent using headless browser mode to preserve authentication state
(cookies, localStorage, sessionStorage, IndexedDB) across tool calls, so that
logged-in sessions survive individual browser launches.

## Approach

Use Playwright's built-in **persistent context** (`launch_persistent_context`),
which stores the full browser profile in a directory on disk and reuses it
across calls. Opt-in via an optional `profile` parameter. Two management
actions (`list_profiles`, `clear_profile`) give the user visibility and control.

## Profile Directory

Profiles live at `~/.config/shem/profiles/<name>/`, where `<name>` is
validated against `[a-zA-Z0-9_-]+` to prevent path traversal. Each directory is
a Playwright persistent context — cookies, localStorage, sessionStorage,
IndexedDB, and extension state are all preserved automatically.

```
~/.config/shem/
├── sump-config.json
└── profiles/
    ├── github-session/
    ├── admin-panel/
    └── scraper/
```

## Schema Changes

### New optional `profile` parameter

Added to the `schema.properties` of `browser.json`:

| Property | Type | Description | Applies to |
|----------|------|-------------|------------|
| `profile` | string | Profile name for persistent browser context (headless only, ignored in live-connect mode) | all actions |

Validation: `profile` must match `^[a-zA-Z0-9_-]+$`. Empty strings treated as
absent (no profile). Ignored entirely when `connect_url` is set.

### Two new actions

| Action | Parameters | Returns |
|--------|-----------|---------|
| `list_profiles` | none | `{"profiles": [{"name": str, "size_bytes": int, "warning": str|null}]}` |
| `clear_profile` | `profile` (required) | `{"cleared": profile}` or `{"error": "profile not found"}` |

`list_profiles` checks each profile directory size and sets `warning` to
`"profile exceeds 100 MB"` when `size_bytes > 100_000_000`. The agent can use
this signal to ask the user before proceeding.

`clear_profile` recursively deletes the profile directory. Returns an error if
the profile doesn't exist or the path escapes the profiles directory.

## Code Changes

### `connect_or_launch` (renamed to `get_page` or extended)

Current function creates a non-persistent context. Extend to:

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


def _pick_browser_type():
    bt = os.environ.get("SHEM_BROWSER_TYPE", "firefox").lower()
    if bt in ("chromium", "webkit"):
        return bt
    return "firefox"
```

Note: `launch_persistent_context` returns a context directly (not a browser),
and manages its own lifecycle — it does not need `browser.close()`.

### New handlers

```python
PROFILES_DIR = os.path.expanduser("~/.config/shem/profiles")

def _handle_list_profiles(page, args):
    # page is unused, but dispatch always provides one
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
```

### Dispatch updates

Add to `HANDLERS`:

```python
HANDLERS = {
    # ... existing ...
    "list_profiles": _handle_list_profiles,
    "clear_profile": _handle_clear_profile,
}
```

Add to `_validate`:

```python
"list_profiles": [],
"clear_profile": ["profile"],
```

Add optional profile to all other actions' validations (it's never required,
just checked for validity when present):

```python
# In run(), after _validate:
profile = args.get("profile")
if profile and not re.match(r"^[a-zA-Z0-9_-]+$", profile):
    return {"error": "invalid profile name (use [a-zA-Z0-9_-]+)"}
```

### Schema enum update

Add to `browser.json` action enum:

```json
"enum": [
    ...existing actions...,
    "list_profiles",
    "clear_profile"
]
```

## Security

- Profile names restricted to `[a-zA-Z0-9_-]+` — no `..`, no `/`, no path
  traversal
- Profiles stored in `~/.config/shem/profiles/` — same trust boundary as
  existing sump config
- No auto-loading: agent must explicitly pass `profile: "name"` every call
- Live-connect mode ignores `profile` entirely — the running browser owns its
  own state, no file I/O happens
- `clear_profile` validates the resolved path is within the profiles directory
  (belt-and-suspenders with the name regex)

## Testing

Add to embedded `test_source`:

- `list_profiles` with no profiles dir → `{"profiles": []}`
- `list_profiles` with profiles → returns list with size info
- `clear_profile` without profile name → error
- `clear_profile` with invalid profile name → error
- `clear_profile` with non-existent profile → error
- `profile` param validation rejects `../evil` → error
- `profile` param validation accepts `my-session_1` → no error
- `profile` param ignored when `connect_url` is set

## What's Not Included (YAGNI)

| Feature | Rationale |
|---------|-----------|
| Auto-cleanup / TTL | User manages with `list_profiles` + `clear_profile`. Add when profiles accumulate without user awareness. |
| Profile listing with last-used timestamps | `list_profiles` shows size and warning. Add timestamps if users can't tell old from new. |
| Encryption at rest | Profiles are user-owned files in `~/.config/shem/`. Same trust model as SSH keys, GPG, and every other local credential store. |
| Profile switching mid-session | Agent uses one `profile` param per call. Switching is just passing a different name next call. |

## Attribution

Spec written by Philip with Shem superpowers tooling.
