#!/usr/bin/env python3
"""Playwright automation tools for deterministic browser copilot workflows."""

from __future__ import annotations

import asyncio
from datetime import datetime
import importlib
import os
import pathlib
import re
from typing import Any, Dict, List, Optional, Tuple

from tools.registry import registry, tool_error, tool_result

_playwright = None
_browser = None
_context = None
_page = None


def _ensure_page() -> Optional[str]:
    if _page is None:
        return tool_error("Browser not launched. Call browser_launch first.")
    return None


def _sanitize_filename(filename: Optional[str]) -> str:
    if not filename:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"screenshot-{ts}.png"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", filename).strip("-")
    if not safe:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe = f"screenshot-{ts}.png"
    if not safe.lower().endswith(".png"):
        safe = f"{safe}.png"
    return safe


def _first_class(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    parts = value.strip().split()
    return parts[0] if parts else ""


async def browser_launch(headless: bool = False, base_url: Optional[str] = None) -> str:
    global _playwright, _browser, _context, _page
    try:
        if _page is not None:
            return tool_result(
                {
                    "status": "launched",
                    "url": _page.url,
                }
            )

        async_playwright = importlib.import_module("playwright.async_api").async_playwright

        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(headless=bool(headless))
        _context = await _browser.new_context()
        _page = await _context.new_page()

        if base_url:
            await _page.goto(base_url)
            await _page.wait_for_load_state("domcontentloaded")

        return tool_result({"status": "launched", "url": _page.url})
    except Exception as e:
        return tool_error(str(e))


async def page_navigate(url: str) -> str:
    try:
        page_error = _ensure_page()
        if page_error:
            return page_error

        await _page.goto(url)
        await _page.wait_for_load_state("domcontentloaded")
        title = await _page.title()
        return tool_result({"status": "navigated", "url": url, "title": title})
    except Exception as e:
        return tool_error(str(e))


async def dom_extract(scope: Optional[str] = None) -> str:
    try:
        page_error = _ensure_page()
        if page_error:
            return page_error

        elements = await _page.evaluate(
            """
            ({ scope }) => {
              const root = scope ? document.querySelector(scope) : document;
              if (!root) {
                return [];
              }

              const out = [];
              const seen = new Set();
              const pushEl = (el, tag) => {
                if (!el || seen.has(el)) return;
                seen.add(el);
                const text = (el.innerText || el.textContent || "").trim();
                out.push({
                  tag,
                  text,
                  id: el.getAttribute("id") || "",
                  role: el.getAttribute("role") || "",
                  "aria-label": el.getAttribute("aria-label") || "",
                  "data-testid": el.getAttribute("data-testid") || "",
                  placeholder: el.getAttribute("placeholder") || "",
                  class: (el.getAttribute("class") || "").trim(),
                  name: el.getAttribute("name") || "",
                  href: el.getAttribute("href") || "",
                  type: el.getAttribute("type") || "",
                  "data-cy": el.getAttribute("data-cy") || "",
                  "data-qa": el.getAttribute("data-qa") || "",
                  "data-test": el.getAttribute("data-test") || "",
                  options: tag === "select"
                    ? Array.from(el.querySelectorAll("option")).slice(0, 10).map(o => ({
                        value: o.value || "",
                        text: (o.textContent || "").trim(),
                      }))
                    : [],
                });
              };

              root.querySelectorAll("input").forEach((el) => pushEl(el, "input"));
              root.querySelectorAll("button").forEach((el) => pushEl(el, "button"));
              root.querySelectorAll("a").forEach((el) => pushEl(el, "a"));
              root.querySelectorAll("select").forEach((el) => pushEl(el, "select"));
              root.querySelectorAll("textarea").forEach((el) => pushEl(el, "textarea"));
              return out.slice(0, 50);
            }
            """,
            {"scope": scope},
        )

        normalized: List[Dict[str, Any]] = []
        for el in elements or []:
            normalized.append(
                {
                    "tag": el.get("tag", ""),
                    "text": el.get("text", ""),
                    "id": el.get("id", ""),
                    "role": el.get("role", ""),
                    "aria-label": el.get("aria-label", ""),
                    "data-testid": el.get("data-testid", ""),
                    "placeholder": el.get("placeholder", ""),
                    "class": _first_class(el.get("class", "")),
                    "name": el.get("name", ""),
                    "href": el.get("href", ""),
                    "type": el.get("type", ""),
                    "options": el.get("options", []),
                    "data-cy": el.get("data-cy", ""),
                    "data-qa": el.get("data-qa", ""),
                    "data-test": el.get("data-test", ""),
                }
            )

        return tool_result({"elements": normalized, "count": len(normalized), "url": _page.url})
    except Exception as e:
        return tool_error(str(e))


def _str_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _build_css_selector(element_data: Dict[str, Any]) -> Optional[str]:
    tag = _str_value(element_data.get("tag"))
    cls = _str_value(element_data.get("class"))
    if not tag:
        return None
    if cls:
        cls = re.sub(r"[^A-Za-z0-9_-]", "", cls)
        if cls:
            return f"{tag}.{cls}"
    return tag


def _build_relative_xpath(element_data: Dict[str, Any]) -> Optional[str]:
    tag = _str_value(element_data.get("tag")) or "*"
    parent_tag = _str_value(element_data.get("parent_tag"))
    parent_id = _str_value(element_data.get("parent_id"))
    if parent_id:
        return f"//*[@id={repr(parent_id)}]//{tag}"
    if parent_tag:
        return f"//{parent_tag}//{tag}"
    text = _str_value(element_data.get("text"))
    if text:
        escaped = text.replace('"', '\\"')
        return f"//{tag}[normalize-space()=\"{escaped}\"]"
    return f"//{tag}"


async def _uniqueness_check(locator, locator_text: str) -> Tuple[int, str]:
    count = await locator.count()
    if count <= 1:
        return count, locator_text
    try:
        narrowed = locator.first
        first_count = await narrowed.count()
        if first_count == 1:
            return 1, f"{locator_text} >> nth=0"
    except Exception:
        pass
    return count, locator_text


async def locator_find(element_data: Dict[str, Any]) -> str:
    try:
        page_error = _ensure_page()
        if page_error:
            return page_error

        if not isinstance(element_data, dict):
            return tool_error("element_data must be an object")

        tried: List[str] = []
        data_testid = _str_value(element_data.get("data_testid") or element_data.get("data-testid"))
        data_cy = _str_value(element_data.get("data_cy") or element_data.get("data-cy"))
        data_qa = _str_value(element_data.get("data_qa") or element_data.get("data-qa"))
        data_test = _str_value(element_data.get("data_test") or element_data.get("data-test"))
        aria_label = _str_value(element_data.get("aria_label") or element_data.get("aria-label"))
        role = _str_value(element_data.get("role"))
        text = _str_value(element_data.get("text"))
        element_id = _str_value(element_data.get("id"))
        placeholder = _str_value(element_data.get("placeholder"))

        async def _try(name: str, locator_factory, locator_str: str, stable: bool) -> Optional[str]:
            tried.append(name)
            try:
                locator = locator_factory()
                count, resolved_locator = await _uniqueness_check(locator, locator_str)
                if count == 1:
                    payload: Dict[str, Any] = {
                        "found": True,
                        "locator": resolved_locator,
                        "strategy": name,
                        "stable": stable,
                        "tried": tried,
                    }
                    if not stable:
                        payload["warning"] = "fragile locator"
                    return tool_result(payload)
            except Exception:
                return None
            return None

        if data_testid:
            selector = f'[data-testid="{data_testid}"]'
            found = await _try("data-testid", lambda: _page.locator(selector), selector, True)
            if found:
                return found

        for attr_name, attr_value in (("data-cy", data_cy), ("data-qa", data_qa), ("data-test", data_test)):
            if attr_value:
                selector = f'[{attr_name}="{attr_value}"]'
                found = await _try(attr_name, lambda s=selector: _page.locator(s), selector, True)
                if found:
                    return found

        if aria_label:
            found = await _try(
                "aria-label",
                lambda: _page.get_by_label(aria_label),
                f'get_by_label("{aria_label}")',
                True,
            )
            if found:
                return found

        if role and text:
            found = await _try(
                "role+name",
                lambda: _page.get_by_role(role, name=text),
                f'get_by_role("{role}", name="{text}")',
                True,
            )
            if found:
                return found

        if element_id:
            selector = f"#{element_id}"
            found = await _try("id", lambda: _page.locator(selector), selector, True)
            if found:
                return found

        if placeholder:
            found = await _try(
                "placeholder",
                lambda: _page.get_by_placeholder(placeholder),
                f'get_by_placeholder("{placeholder}")',
                True,
            )
            if found:
                return found

        href = _str_value(
            element_data.get("href", "")
        )
        if href and href.startswith("/"):
            selector = f'a[href="{href}"]'
            found = await _try(
                "href",
                lambda s=selector: _page.locator(s),
                selector,
                True,
            )
            if found:
                return found

        if text:
            found = await _try(
                "exact-text",
                lambda: _page.get_by_text(text, exact=True),
                f'get_by_text("{text}", exact=True)',
                True,
            )
            if found:
                return found

        if text:
            found = await _try(
                "partial-text",
                lambda: _page.get_by_text(text, exact=False),
                f'get_by_text("{text}", exact=False)',
                True,
            )
            if found:
                return found

        if text and element_data.get("parent_tag"):
            parent_tag = _str_value(
                element_data.get("parent_tag", "")
            )
            scoped = (
                f'{parent_tag} >> '
                f'text="{text}"'
            )
            found = await _try(
                "parent-scoped-text",
                lambda s=scoped: _page.locator(s),
                scoped,
                False,
            )
            if found:
                return found

        css_selector = _build_css_selector(element_data)
        if css_selector:
            found = await _try("css", lambda: _page.locator(css_selector), css_selector, False)
            if found:
                return found

        xpath_selector = _build_relative_xpath(element_data)
        if xpath_selector:
            found = await _try("xpath-relative", lambda: _page.locator(f"xpath={xpath_selector}"), f"xpath={xpath_selector}", False)
            if found:
                return found

        return tool_result(
            {
                "found": False,
                "locator": "",
                "strategy": "",
                "stable": False,
                "warning": "no unique locator found",
                "tried": tried,
            }
        )
    except Exception as e:
        return tool_error(str(e))


def _resolve_locator(locator_str: str):
    """
    Convert a locator string returned by locator_find into a real Playwright locator.

    Handles:
      get_by_label("...")
      get_by_role("...", name="...")
      get_by_placeholder("...")
      get_by_text("...", exact=True/False)
      get_by_test_id("...")
      Any CSS/XPath string (pass directly)
    """
    s = locator_str.strip()

    m = re.match(r'^get_by_label\("(.+?)"\)$', s)
    if m:
        return _page.get_by_label(m.group(1))

    m = re.match(r'^get_by_placeholder\("(.+?)"\)$', s)
    if m:
        return _page.get_by_placeholder(m.group(1))

    m = re.match(r'^get_by_text\("(.+?)",\s*exact=(True|False)\)$', s)
    if m:
        exact = m.group(2) == "True"
        return _page.get_by_text(m.group(1), exact=exact)

    m = re.match(r'^get_by_text\("(.+?)"\)$', s)
    if m:
        return _page.get_by_text(m.group(1))

    m = re.match(r'^get_by_role\("(.+?)",\s*name="(.+?)"\)$', s)
    if m:
        return _page.get_by_role(m.group(1), name=m.group(2))

    m = re.match(r'^get_by_role\("(.+?)"\)$', s)
    if m:
        return _page.get_by_role(m.group(1))

    m = re.match(r'^get_by_test_id\("(.+?)"\)$', s)
    if m:
        return _page.get_by_test_id(m.group(1))

    m = re.match(r'^(.+?)\s*>>\s*nth=(\d+)$', s)
    if m:
        base = _resolve_locator(m.group(1))
        return base.nth(int(m.group(2)))

    return _page.locator(s)


async def locator_validate(locator: str) -> str:
    try:
        page_error = _ensure_page()
        if page_error:
            return page_error

        count = await _resolve_locator(locator).count()
        if count == 1:
            message = "valid"
            valid = True
        elif count == 0:
            message = "not found"
            valid = False
        else:
            message = f"not unique - found {count} elements"
            valid = False
        return tool_result({"valid": valid, "count": count, "locator": locator, "message": message})
    except Exception as e:
        return tool_error(str(e))


async def action_click(locator: str, timeout: int = 30000) -> str:
    try:
        page_error = _ensure_page()
        if page_error:
            return page_error

        await _resolve_locator(locator).click(timeout=int(timeout))
        return tool_result({"success": True})
    except Exception as e:
        return tool_result({"success": False, "error": str(e)})


async def action_fill(locator: str, value: str, timeout: int = 30000) -> str:
    try:
        page_error = _ensure_page()
        if page_error:
            return page_error

        await _resolve_locator(locator).fill(value, timeout=int(timeout))
        return tool_result({"success": True})
    except Exception as e:
        return tool_result({"success": False, "error": str(e)})


async def action_assert(
    locator: str,
    assertion: str,
    expected_value: Optional[str] = None,
    timeout: int = 5000,
) -> str:
    try:
        page_error = _ensure_page()
        if page_error:
            return page_error

        expect = importlib.import_module("playwright.async_api").expect

        target = _resolve_locator(locator)
        assertion = _str_value(assertion).lower()

        if assertion == "visible":
            await expect(target).to_be_visible(timeout=int(timeout))
        elif assertion == "hidden":
            await expect(target).to_be_hidden(timeout=int(timeout))
        elif assertion == "enabled":
            await expect(target).to_be_enabled(timeout=int(timeout))
        elif assertion == "disabled":
            await expect(target).to_be_disabled(timeout=int(timeout))
        elif assertion == "has_text":
            if expected_value is None:
                return tool_error("expected_value is required for has_text")
            await expect(target).to_have_text(expected_value, timeout=int(timeout))
        elif assertion == "has_value":
            if expected_value is None:
                return tool_error("expected_value is required for has_value")
            await expect(target).to_have_value(expected_value, timeout=int(timeout))
        elif assertion == "checked":
            await expect(target).to_be_checked(timeout=int(timeout))
        else:
            return tool_error(
                "assertion must be one of: visible, hidden, enabled, disabled, has_text, has_value, checked"
            )

        return tool_result({"passed": True, "assertion": assertion})
    except Exception as e:
        return tool_result({"passed": False, "assertion": assertion, "error": str(e)})


async def screenshot_take(filename: Optional[str] = None, full_page: bool = False) -> str:
    try:
        page_error = _ensure_page()
        if page_error:
            return page_error

        safe_filename = _sanitize_filename(filename)
        directory = os.path.join(".hermes", "screenshots")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, safe_filename)

        await _page.screenshot(path=path, full_page=bool(full_page))
        return tool_result({"path": path, "success": True})
    except Exception as e:
        return tool_result({"path": "", "success": False, "error": str(e)})


async def browser_get_state() -> str:
    try:
        if _page is None:
            return tool_result(
                {
                    "url": "",
                    "title": "",
                    "is_loading": False,
                    "browser_open": False,
                }
            )

        title = await _page.title()
        ready_state = await _page.evaluate("document.readyState")
        return tool_result(
            {
                "url": _page.url,
                "title": title,
                "is_loading": ready_state != "complete",
                "browser_open": True,
            }
        )
    except Exception as e:
        return tool_error(str(e))


async def inject_overlay() -> str:
    try:
        err = _ensure_page()
        if err:
            return err

        overlay_dir = (
            pathlib.Path(__file__).parent.parent
            / "overlay"
        )
        js_path  = overlay_dir / "overlay.js"
        css_path = overlay_dir / "overlay.css"

        if not js_path.exists():
            return tool_error(
                f"overlay.js not found at {js_path}"
            )
        if not css_path.exists():
            return tool_error(
                f"overlay.css not found at {css_path}"
            )

        js_content  = js_path.read_text(encoding="utf-8")
        css_content = css_path.read_text(encoding="utf-8")

        # Build the combined script.
        # CSS injected first then JS overlay.
        css_repr = repr(css_content)
        combined = f"""(function() {{
  if (window.__hermesOverlayLoaded) return;
  var _s = document.createElement('style');
  _s.id = 'hermes-overlay-styles';
  _s.textContent = {css_repr};
  (document.head ||
   document.documentElement).appendChild(_s);
}})();
{js_content}
"""

        # Inject into current page immediately.
        await _page.evaluate(combined)

        # Re-inject after every navigation.
        # page.on("load") fires after page loads —
        # at that point document.body always exists
        # and readyState is complete or interactive
        # so init() in overlay.js runs immediately.
        async def _reinject():
            try:
                await _page.evaluate(combined)
            except Exception:
                pass

        # Remove any previous listener first
        # to avoid stacking multiple listeners
        # across multiple inject_overlay() calls.
        try:
            _page.remove_listener("load", _page.__hermes_load_cb)
        except Exception:
            pass

        async def _load_cb():
            await _reinject()

        _page.__hermes_load_cb = _load_cb
        _page.on("load", lambda: asyncio.ensure_future(
            _reinject()
        ))

        return tool_result({
            "status": "injected",
            "message": (
                "Overlay injected. "
                "Re-injects on every navigation."
            )
        })
    except Exception as e:
        return tool_error(str(e))


BROWSER_LAUNCH_SCHEMA = {
    "name": "browser_launch",
    "description": "Launch a Playwright Chromium browser instance. Browser stays open for entire session.",
    "parameters": {
        "type": "object",
        "properties": {
            "headless": {"type": "boolean", "default": False},
            "base_url": {"type": "string"},
        },
        "required": [],
    },
}

PAGE_NAVIGATE_SCHEMA = {
    "name": "page_navigate",
    "description": "Navigate browser to a URL.",
    "parameters": {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    },
}

DOM_EXTRACT_SCHEMA = {
    "name": "dom_extract",
    "description": "Extract only interactive elements from current page. Never dumps full DOM.",
    "parameters": {
        "type": "object",
        "properties": {"scope": {"type": "string"}},
        "required": [],
    },
}

LOCATOR_FIND_SCHEMA = {
    "name": "locator_find",
    "description": "Find the best stable locator for an element using priority waterfall.",
    "parameters": {
        "type": "object",
        "properties": {
            "element_data": {
                "type": "object",
                "description": "Element details including tag/text/id/role/aria_label/data_testid/placeholder/class and optional parent metadata.",
            }
        },
        "required": ["element_data"],
    },
}

LOCATOR_VALIDATE_SCHEMA = {
    "name": "locator_validate",
    "description": "Validate a locator string resolves to exactly 1 element on current page.",
    "parameters": {
        "type": "object",
        "properties": {"locator": {"type": "string"}},
        "required": ["locator"],
    },
}

ACTION_CLICK_SCHEMA = {
    "name": "action_click",
    "description": "Click an element by locator.",
    "parameters": {
        "type": "object",
        "properties": {
            "locator": {"type": "string"},
            "timeout": {"type": "integer", "default": 30000},
        },
        "required": ["locator"],
    },
}

ACTION_FILL_SCHEMA = {
    "name": "action_fill",
    "description": "Fill an input element with value.",
    "parameters": {
        "type": "object",
        "properties": {
            "locator": {"type": "string"},
            "value": {"type": "string"},
            "timeout": {"type": "integer", "default": 30000},
        },
        "required": ["locator", "value"],
    },
}

ACTION_ASSERT_SCHEMA = {
    "name": "action_assert",
    "description": "Assert element state on current page.",
    "parameters": {
        "type": "object",
        "properties": {
            "locator": {"type": "string"},
            "assertion": {
                "type": "string",
                "enum": ["visible", "hidden", "enabled", "disabled", "has_text", "has_value", "checked"],
            },
            "expected_value": {"type": "string"},
            "timeout": {"type": "integer", "default": 5000},
        },
        "required": ["locator", "assertion"],
    },
}

SCREENSHOT_TAKE_SCHEMA = {
    "name": "screenshot_take",
    "description": "Take screenshot of current page.",
    "parameters": {
        "type": "object",
        "properties": {
            "filename": {"type": "string"},
            "full_page": {"type": "boolean", "default": False},
        },
        "required": [],
    },
}

BROWSER_GET_STATE_SCHEMA = {
    "name": "browser_get_state",
    "description": "Get current browser state.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

INJECT_OVERLAY_SCHEMA = {
    "name": "inject_overlay",
    "description": "Inject the Hermes overlay UI panel into the current browser page.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

registry.register(
    name="browser_launch",
    toolset="playwright-automation",
    schema=BROWSER_LAUNCH_SCHEMA,
    handler=lambda args, **kw: browser_launch(
        headless=bool(args.get("headless", False)),
        base_url=args.get("base_url"),
    ),
    is_async=True,
    emoji="🚀",
)

registry.register(
    name="page_navigate",
    toolset="playwright-automation",
    schema=PAGE_NAVIGATE_SCHEMA,
    handler=lambda args, **kw: page_navigate(url=args.get("url", "")),
    is_async=True,
    emoji="🌐",
)

registry.register(
    name="dom_extract",
    toolset="playwright-automation",
    schema=DOM_EXTRACT_SCHEMA,
    handler=lambda args, **kw: dom_extract(scope=args.get("scope")),
    is_async=True,
    emoji="🧩",
)

registry.register(
    name="locator_find",
    toolset="playwright-automation",
    schema=LOCATOR_FIND_SCHEMA,
    handler=lambda args, **kw: locator_find(element_data=args.get("element_data", {})),
    is_async=True,
    emoji="🎯",
)

registry.register(
    name="locator_validate",
    toolset="playwright-automation",
    schema=LOCATOR_VALIDATE_SCHEMA,
    handler=lambda args, **kw: locator_validate(locator=args.get("locator", "")),
    is_async=True,
    emoji="✅",
)

registry.register(
    name="action_click",
    toolset="playwright-automation",
    schema=ACTION_CLICK_SCHEMA,
    handler=lambda args, **kw: action_click(
        locator=args.get("locator", ""),
        timeout=args.get("timeout", 30000),
    ),
    is_async=True,
    emoji="👆",
)

registry.register(
    name="action_fill",
    toolset="playwright-automation",
    schema=ACTION_FILL_SCHEMA,
    handler=lambda args, **kw: action_fill(
        locator=args.get("locator", ""),
        value=args.get("value", ""),
        timeout=args.get("timeout", 30000),
    ),
    is_async=True,
    emoji="⌨️",
)

registry.register(
    name="action_assert",
    toolset="playwright-automation",
    schema=ACTION_ASSERT_SCHEMA,
    handler=lambda args, **kw: action_assert(
        locator=args.get("locator", ""),
        assertion=args.get("assertion", ""),
        expected_value=args.get("expected_value"),
        timeout=args.get("timeout", 5000),
    ),
    is_async=True,
    emoji="🧪",
)

registry.register(
    name="screenshot_take",
    toolset="playwright-automation",
    schema=SCREENSHOT_TAKE_SCHEMA,
    handler=lambda args, **kw: screenshot_take(
        filename=args.get("filename"),
        full_page=bool(args.get("full_page", False)),
    ),
    is_async=True,
    emoji="📸",
)

registry.register(
    name="browser_get_state",
    toolset="playwright-automation",
    schema=BROWSER_GET_STATE_SCHEMA,
    handler=lambda args, **kw: browser_get_state(),
    is_async=True,
    emoji="🧭",
)

registry.register(
    name="inject_overlay",
    toolset="playwright-automation",
    schema=INJECT_OVERLAY_SCHEMA,
    handler=lambda args, **kw: inject_overlay(),
    is_async=True,
    emoji="🖥️",
)
