# src/core/a11y_capture.py
"""
A11Y Tree capture and parsing utilities.

Captures the accessibility tree of a running application via pyatspi (AT-SPI2),
serializes it to XML, then parses and filters it to a readable TXT format
with button names and coordinates.

Usage:
    capture = A11YCapture()
    xml_path, txt_path = capture.capture_and_parse(
        app_name="gnome-calculator",
        output_dir=Path("data/exploration/task_runs/run_dhash_001"),
        suffix="initial",
    )
"""
from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional, Set, Tuple
from xml.dom import minidom

logger = logging.getLogger(__name__)

# Roles to include in TXT output — everything else is filtered out
DEFAULT_FILTER_ROLES: Set[str] = {
    "push button",
    "toggle button",
    "radio button",
    "check box",
    "label",
    "menu item",
    "entry",
    "text",
    "combo box",
}

# How the calculator may appear in the AT-SPI registry
CALCULATOR_NAME_HINTS = ("gnome-calculator", "calculator", "calc")


class A11YCapture:
    """
    Capture the accessibility tree of a running app and parse it.

    Typical flow:
        1. capture_to_xml()  — dump AT-SPI tree → XML file
        2. parse_xml_to_txt() — filter elements → TXT file
    Or use the convenience wrapper capture_and_parse().
    """

    def __init__(self, filter_roles: Optional[Set[str]] = None) -> None:
        self.filter_roles: Set[str] = filter_roles if filter_roles is not None else DEFAULT_FILTER_ROLES

    # ------------------------------------------------------------------
    # AT-SPI helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _import_pyatspi():
        """Import pyatspi or raise a clear error."""
        try:
            import pyatspi  # type: ignore
            return pyatspi
        except ImportError as exc:
            raise RuntimeError(
                "pyatspi is required for A11Y capture. "
                "Install it with: sudo apt-get install python3-pyatspi"
            ) from exc

    def _find_app_accessible(self, app_name: str, timeout: float = 8.0):
        """
        Scan the AT-SPI desktop for an app matching app_name.

        Tries partial, case-insensitive matching. Waits up to `timeout` seconds.
        Returns (app_accessible, pyatspi_module).
        """
        pyatspi = self._import_pyatspi()
        app_lower = app_name.lower()
        hints = [app_lower] + list(CALCULATOR_NAME_HINTS)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                desktop = pyatspi.Registry.getDesktop(0)
                for app in desktop:
                    if app is None:
                        continue
                    acc_name = (app.name or "").lower()
                    if any(h in acc_name or acc_name in h for h in hints):
                        logger.info("Found accessible app: '%s'", app.name)
                        return app, pyatspi
            except Exception as exc:
                logger.debug("AT-SPI lookup error (will retry): %s", exc)
            time.sleep(0.5)

        raise RuntimeError(
            f"Could not find accessible app matching '{app_name}' "
            f"in AT-SPI registry within {timeout:.0f}s. "
            "Make sure the app is running and AT-SPI bridge is active."
        )

    def _traverse(self, obj, parent_elem, pyatspi_mod) -> None:
        """Recursively walk the accessibility tree, appending XML elements."""
        try:
            role_name = obj.getRoleName() or "unknown"
            name = obj.name or ""
            description = obj.description or ""
        except Exception:
            return

        elem = ET.SubElement(parent_elem, "element")
        elem.set("role", role_name)
        elem.set("name", name)
        elem.set("description", description)

        try:
            comp = obj.queryComponent()
            ext = comp.getExtents(pyatspi_mod.DESKTOP_COORDS)
            elem.set("x", str(ext.x))
            elem.set("y", str(ext.y))
            elem.set("width", str(ext.width))
            elem.set("height", str(ext.height))
        except Exception:
            elem.set("x", "0")
            elem.set("y", "0")
            elem.set("width", "0")
            elem.set("height", "0")

        try:
            state_set = obj.getState()
            states = []
            if state_set.contains(pyatspi_mod.STATE_CHECKED):
                states.append("checked")
            if state_set.contains(pyatspi_mod.STATE_SELECTED):
                states.append("selected")
            if state_set.contains(pyatspi_mod.STATE_FOCUSED):
                states.append("focused")
            if state_set.contains(pyatspi_mod.STATE_SENSITIVE):
                states.append("sensitive")
            if states:
                elem.set("states", ",".join(states))
        except Exception:
            pass

        try:
            for i in range(obj.childCount):
                child = obj.getChildAtIndex(i)
                if child is not None:
                    self._traverse(child, elem, pyatspi_mod)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def capture_to_xml(
        self,
        app_name: str,
        output_path: Path,
        timeout: float = 8.0,
    ) -> Path:
        """
        Capture the accessibility tree of a running app and save to XML.

        Args:
            app_name:    App name to search for (case-insensitive, partial match).
            output_path: Destination XML file.
            timeout:     Seconds to wait for the app to appear in the AT-SPI registry.

        Returns:
            Path to the saved XML file.
        """
        app_acc, pyatspi = self._find_app_accessible(app_name, timeout)

        root = ET.Element("accessibility-tree")
        root.set("app", app_name)
        root.set("app_accessible_name", app_acc.name or "")
        root.set("captured_at", str(time.time()))

        self._traverse(app_acc, root, pyatspi)

        # Pretty-print XML (strip the <?xml ...?> declaration added by minidom)
        raw = ET.tostring(root, encoding="unicode")
        pretty = minidom.parseString(raw).toprettyxml(indent="  ")
        lines = pretty.splitlines()
        if lines and lines[0].startswith("<?xml"):
            pretty = "\n".join(lines[1:])

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(pretty, encoding="utf-8")
        logger.info("A11Y XML saved → %s", output_path)
        return output_path

    def parse_xml_to_txt(
        self,
        xml_path: Path,
        output_path: Path,
        filter_roles: Optional[Set[str]] = None,
    ) -> Path:
        """
        Parse the A11Y XML and save filtered elements to a tab-separated TXT.

        Output columns:
            tag  name  text  class  description  position (top-left x&y)  size (w&h)

        Args:
            xml_path:     XML file produced by capture_to_xml().
            output_path:  Destination TXT file.
            filter_roles: Roles to include (None → use instance default).

        Returns:
            Path to the saved TXT file.
        """
        roles = {r.lower() for r in (filter_roles if filter_roles is not None else self.filter_roles)}

        tree = ET.parse(xml_path)
        rows: List[Tuple[str, str, str, str, str, str, str]] = []

        for elem in tree.getroot().iter("element"):
            role = elem.get("role", "")
            if roles and role.lower() not in roles:
                continue

            name = elem.get("name", "")
            description = elem.get("description", "")
            x = elem.get("x", "0")
            y = elem.get("y", "0")
            w = elem.get("width", "0")
            h = elem.get("height", "0")

            # Skip invisible / unrendered elements.
            # INT32_MIN (-2147483648) in x or y means the element is registered
            # in the AT-SPI tree but not currently rendered on screen (hidden
            # menus, collapsed dropdowns, etc.). Size (1, 1) is the paired sentinel.
            xi, yi = int(x), int(y)
            if (int(w) == 0 and int(h) == 0) or xi < 0 or yi < 0:
                continue

            rows.append((
                role,
                name,
                name,       # text = same as name for accessibility elements
                "",         # class (CSS class — not applicable here)
                description,
                f"({x}, {y})",
                f"({w}, {h})",
            ))

        header = "tag\tname\ttext\tclass\tdescription\tposition (top-left x&y)\tsize (w&h)"
        body = "\n".join("\t".join(row) for row in rows)
        content = header + "\n" + body if rows else header

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        logger.info("A11Y TXT saved → %s  (%d elements)", output_path, len(rows))
        return output_path

    def find_button_by_name(
        self,
        xml_path: Path,
        name: str,
    ) -> Optional[Tuple[int, int, int, int]]:
        """
        Find a button-like element in the XML by (partial) name.

        Checks push-button, toggle-button, radio-button, menu-item roles.

        Returns:
            (x, y, width, height) of the first match, or None.
        """
        tree = ET.parse(xml_path)
        name_lower = name.lower()
        button_roles = ("push button", "toggle button", "radio button", "menu item", "check box")

        for elem in tree.getroot().iter("element"):
            elem_name = (elem.get("name") or "").lower()
            role = (elem.get("role") or "").lower()

            if role not in button_roles:
                continue
            if not elem_name:
                continue
            if name_lower not in elem_name and elem_name not in name_lower:
                continue

            x = int(elem.get("x", 0))
            y = int(elem.get("y", 0))
            w = int(elem.get("width", 0))
            h = int(elem.get("height", 0))

            if w > 0 and h > 0 and x >= 0 and y >= 0:
                logger.info(
                    "Found button '%s' (role=%s) at (%d, %d) size %dx%d",
                    elem.get("name"), elem.get("role"), x, y, w, h,
                )
                return x, y, w, h

        logger.warning("Button '%s' not found in A11Y tree: %s", name, xml_path)
        return None

    def find_unchecked_mode_button(
        self,
        xml_path: Path,
        candidates: List[str],
    ) -> Optional[Tuple[str, int, int, int, int]]:
        """
        Find a mode radio-button that is NOT currently checked/selected.

        Scans `candidates` in order and returns the first one whose state
        does NOT include "checked" or "selected" and has valid coordinates.

        Returns:
            (name, x, y, w, h) of the first unchecked candidate, or None.
        """
        tree = ET.parse(xml_path)
        button_roles = {"radio button", "toggle button", "push button"}

        # Build a lookup: name → (states, x, y, w, h) for all valid buttons
        button_map: dict = {}
        for elem in tree.getroot().iter("element"):
            name = (elem.get("name") or "").strip()
            if not name:
                continue
            role = (elem.get("role") or "").lower()
            if role not in button_roles:
                continue
            x = int(elem.get("x", 0))
            y = int(elem.get("y", 0))
            w = int(elem.get("width", 0))
            h = int(elem.get("height", 0))
            if w <= 0 or h <= 0 or x < 0 or y < 0:
                continue
            states = elem.get("states", "")
            button_map[name] = (states, x, y, w, h)

        # Find first candidate that is NOT currently active
        active_name = None
        for name, (states, *_) in button_map.items():
            if "checked" in states or "selected" in states:
                active_name = name

        logger.info("  Currently active mode button: %s", active_name)

        for candidate in candidates:
            if candidate == active_name:
                logger.info("  Skipping '%s' — already active", candidate)
                continue
            if candidate in button_map:
                states, x, y, w, h = button_map[candidate]
                logger.info("  Selected target mode: '%s' at (%d, %d)", candidate, x, y)
                return candidate, x, y, w, h

        logger.warning("  No unchecked mode button found among candidates: %s", candidates)
        return None

    def capture_and_parse(
        self,
        app_name: str,
        output_dir: Path,
        suffix: str = "",
        timeout: float = 8.0,
    ) -> Tuple[Path, Path]:
        """
        Convenience wrapper: capture A11Y tree and parse to TXT in one call.

        Args:
            app_name:   App name.
            output_dir: Directory for output files.
            suffix:     Optional filename suffix, e.g. "initial" or "after_mode_change".
            timeout:    Seconds to wait for the app.

        Returns:
            (xml_path, txt_path)
        """
        sfx = f"_{suffix}" if suffix else ""
        xml_path = output_dir / f"a11y_tree{sfx}.xml"
        txt_path = output_dir / f"a11y_buttons{sfx}.txt"

        self.capture_to_xml(app_name, xml_path, timeout)
        self.parse_xml_to_txt(xml_path, txt_path)

        return xml_path, txt_path
