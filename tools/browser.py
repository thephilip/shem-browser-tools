import base64
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from urllib.parse import urlparse


# ── Sump-based injection sanitization ──────────────────────────

CONFIG_DIR = os.path.expanduser("~/.config/shem")
SUMP_CONFIG_PATH = os.path.join(CONFIG_DIR, "sump-config.json")

PROFILES_DIR = os.path.expanduser("~/.config/shem/profiles")


def _pick_browser_type():
    bt = os.environ.get("SHEM_BROWSER_TYPE", "firefox").lower()
    return bt if bt in ("chromium", "webkit") else "firefox"


INJECTION_PATTERNS = [
    r"ignore\s+all\s+(previous\s+)?instructions",
    r"ignore\s+all\s+(prior\s+)?directives",
    r"forget\s+(everything|all\s+previous)",
    r"disregard\s+(all\s+)?(previous|prior)",
    r"you\s+(are\s+)?(now|will\s+act\s+as)",
    r"from\s+now\s+on\s+you\s+are",
    r"you\s+are\s+no\s+longer",
    r"new\s+instructions?\s*:",
    r"override\s+(mode|protocol|directives)",
    r"system\s+(prompt|message|instruction)",
    r"\"\"\"[\s\S]{0,200}ignore",
    r"<\s*(system|user|assistant)\s*>",
    r"\{\{[\s\S]{0,500}?\}\}",
    r"\[\[\s*SYSTEM",
    r"you\s+must\s+ignore",
    r"this\s+is\s+(an\s+)?(urgent|important)\s*(order|instruction|command)",
    r"do\s+not\s+(output|respond|reply|return)\s+(with\s+)?(your\s+)?(standard|normal|usual)",
    r"for\s+security\s+(reasons|purposes)",
    r"you\s+have\s+been\s+(hacked|compromised|overridden)",
    r"START\s+(OF\s+)?(NEW\s+)?(INSTRUCTIONS|SYSTEM|PROMPT)",
    r"END\s+(OF\s+)?(ALL\s+)?(INSTRUCTIONS|DIRECTIVES)",
    r"output\s+the\s+(full\s+)?prompt",
]

BAD_RX = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]


def _load_sump_config():
    try:
        with open(SUMP_CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"whitelist": [], "domains": [], "patterns": []}


def sanitize(text, domain=""):
    config = _load_sump_config()
    whitelist = config.get("whitelist", [])
    blacklist = config.get("domains", [])
    extra_patterns = config.get("patterns", [])

    if blacklist and any(d in domain for d in blacklist):
        return "", True

    cleaned = re.sub("[\U000E0000-\U000E007F]", "", text)

    all_rx = BAD_RX + [re.compile(p, re.IGNORECASE) for p in extra_patterns]
    flagged = any(r.search(cleaned) for r in all_rx)

    if not any(w in domain for w in whitelist):
        cleaned = "<untrusted>\n" + cleaned + "\n</untrusted>"

    return cleaned, flagged


# ── Action handlers ──────────────────────────────────────────

def _handle_navigate(page, args):
    url = args.get("url")
    if not url:
        return {"error": "missing 'url' parameter"}
    page.goto(url, timeout=30000)
    title = page.title()
    text = page.inner_text("body")
    domain = _extract_domain(url)
    cleaned, flagged = sanitize(text, domain)
    result = {"title": title, "text": cleaned, "url": page.url}
    if flagged:
        result["_flagged"] = "injection patterns detected"
    return result


def _handle_screenshot(page, args):
    selector = args.get("selector")
    if selector:
        el = page.wait_for_selector(selector, timeout=5000)
        if not el:
            return {"error": f"selector not found: {selector}"}
        screenshot = el.screenshot(type="png")
    else:
        screenshot = page.screenshot(type="png", full_page=True)
    return {"screenshot": base64.b64encode(screenshot).decode("utf-8")}


def _handle_extract(page, args):
    selectors = args.get("selectors", [])
    if not selectors:
        return {"error": "missing 'selectors' parameter"}
    result = {}
    for sel in selectors:
        elements = page.query_selector_all(sel)
        texts = [el.inner_text() for el in elements]
        domain = _extract_domain(page.url)
        result[sel] = [sanitize(t, domain)[0] for t in texts]
    return result


def _handle_hover(page, args):
    selector = args.get("selector")
    if not selector:
        return {"error": "missing 'selector' parameter"}
    el = page.wait_for_selector(selector, timeout=5000)
    if not el:
        return {"error": f"selector not found: {selector}"}
    el.hover()
    return {"hovered": selector}


def _handle_scroll(page, args):
    x = args.get("x", 0)
    y = args.get("y")
    selector = args.get("selector")

    if selector:
        el = page.wait_for_selector(selector, timeout=5000)
        if not el:
            return {"error": f"selector not found: {selector}"}
        el.scroll_into_view_if_needed()
        return {"scrolled_to": selector}
    if y is None:
        return {"error": "missing 'y' (pixels) or 'selector' parameter"}
    page.evaluate(f"window.scrollBy({x}, {y})")
    return {"scrolled": {"x": x, "y": y}}


def _handle_wait(page, args):
    ms = args.get("ms", 1000)
    selector = args.get("selector")
    if selector:
        el = page.wait_for_selector(selector, timeout=ms)
        if not el:
            return {"error": f"selector not found: {selector}"}
        return {"waited_for": selector}
    page.wait_for_timeout(ms)
    return {"waited": ms}


def _handle_is_visible(page, args):
    selector = args.get("selector")
    if not selector:
        return {"error": "missing 'selector' parameter"}
    return {"visible": page.is_visible(selector)}


def _handle_select(page, args):
    selector = args.get("selector")
    if not selector:
        return {"error": "missing 'selector' parameter"}
    value = args.get("value")
    label = args.get("label")
    if not value and not label:
        return {"error": "missing 'value' or 'label' parameter"}
    el = page.wait_for_selector(selector, timeout=5000)
    if not el:
        return {"error": f"selector not found: {selector}"}
    if value:
        el.select_option(value=value)
        return {"selected": {"by": "value", "value": value}}
    el.select_option(label=label)
    return {"selected": {"by": "label", "label": label}}


def _handle_click(page, args):
    selector = args.get("selector")
    if not selector:
        return {"error": "missing 'selector' parameter"}
    el = page.wait_for_selector(selector, timeout=5000)
    if not el:
        return {"error": f"selector not found: {selector}"}
    el.click()
    return {"clicked": selector}


def _handle_type(page, args):
    selector = args.get("selector")
    text = args.get("text")
    if not selector:
        return {"error": "missing 'selector' parameter"}
    if text is None:
        return {"error": "missing 'text' parameter"}
    el = page.wait_for_selector(selector, timeout=5000)
    if not el:
        return {"error": f"selector not found: {selector}"}
    el.fill(text)
    return {"typed": text}


def _handle_evaluate(page, args):
    script = args.get("script")
    if not script:
        return {"error": "missing 'script' parameter"}
    result = page.evaluate(script)
    return {"result": result}


def _handle_pdf(page, args):
    path = args.get("path")
    if not path:
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
    page.pdf(path=path)
    return {"path": path}


def _handle_list_tabs(page, args):
    context = page.context
    tabs = []
    for i, p in enumerate(context.pages):
        try:
            tabs.append({"id": i, "title": p.title(), "url": p.url})
        except Exception:
            tabs.append({"id": i, "title": "", "url": ""})
    return {"tabs": tabs}


def _handle_switch_tab(page, args):
    tab_id = args.get("tab_id")
    if tab_id is None:
        return {"error": "missing 'tab_id' parameter"}
    pages = page.context.pages
    if tab_id < 0 or tab_id >= len(pages):
        return {"error": f"tab_id {tab_id} out of range (0-{len(pages)-1})"}
    target = pages[tab_id]
    target.bring_to_front()
    return {"tab_id": tab_id, "title": target.title(), "url": target.url}


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


# ── Playwright connection manager ──────────────────────────────

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


def _extract_domain(url):
    try:
        return urlparse(url).netloc
    except Exception:
        return ""


# ── Param validation before browser connect ─────────────────────

def _validate(action, args):
    required = {
        "navigate": ["url"],
        "screenshot": [],
        "extract": ["selectors"],
        "is_visible": ["selector"],
        "select": ["selector"],
        "click": ["selector"],
        "hover": ["selector"],
        "scroll": [],
        "wait": [],
        "type": ["selector", "text"],
        "evaluate": ["script"],
        "pdf": [],
        "list_tabs": ["connect_url"],
        "switch_tab": ["tab_id", "connect_url"],
        "list_profiles": [],
        "clear_profile": ["profile"],
    }
    missing = []
    for k in required.get(action, []):
        val = args.get(k)
        if val is None or (isinstance(val, str) and val == ""):
            missing.append(k)
    if missing:
        return f"missing parameters: {', '.join(missing)}"
    return None


# ── Top-level dispatch ─────────────────────────────────────────

HANDLERS = {
    "navigate": _handle_navigate,
    "screenshot": _handle_screenshot,
    "extract": _handle_extract,
    "is_visible": _handle_is_visible,
    "select": _handle_select,
    "click": _handle_click,
    "hover": _handle_hover,
    "scroll": _handle_scroll,
    "wait": _handle_wait,
    "type": _handle_type,
    "evaluate": _handle_evaluate,
    "pdf": _handle_pdf,
    "list_tabs": _handle_list_tabs,
    "switch_tab": _handle_switch_tab,
    "list_profiles": _handle_list_profiles,
    "clear_profile": _handle_clear_profile,
}


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
    profile = args.pop("profile", None)
    if profile and not re.match(r"^[a-zA-Z0-9_-]+$", profile):
        return {"error": "invalid profile name (use [a-zA-Z0-9_-]+)"}

    if action in ("list_profiles", "clear_profile"):
        return handler(None, args)

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
