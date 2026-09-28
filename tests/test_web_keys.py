"""The dashboard's key list must match what the Pico firmware can press."""

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def firmware_keys() -> set:
    tree = ast.parse((ROOT / "CIRCUITPY" / "code.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "KEY_MAP" for t in node.targets
        ):
            return {k.value for k in node.value.keys}
    raise AssertionError("KEY_MAP not found in code.py")


def dashboard_keys() -> set:
    src = (ROOT / "web" / "src" / "keys.ts").read_text(encoding="utf-8")
    body = src[src.index("PICO_KEYS"):]
    body = body[body.index("[") + 1:body.index("];")]
    keys = set()
    spread = re.search(r'\.\.\."([^"]*)"', body)
    if spread:
        keys.update(spread.group(1))
        body = body.replace(spread.group(0), "")
    for lit in re.findall(r'"((?:[^"\\]|\\.)*)"', body):
        keys.add(lit.encode().decode("unicode_escape"))
    return keys


class DashboardKeysTest(unittest.TestCase):
    def test_matches_firmware_key_map(self):
        self.assertEqual(dashboard_keys(), firmware_keys())


if __name__ == "__main__":
    unittest.main()
