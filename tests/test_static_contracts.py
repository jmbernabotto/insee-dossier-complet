import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.py"
GITIGNORE_PATH = ROOT / ".gitignore"


class StaticContractsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_requests_calls_have_timeouts(self):
        missing = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "requests"
                and func.attr in {"get", "post"}
            ):
                continue
            if not any(keyword.arg == "timeout" for keyword in node.keywords):
                missing.append((node.lineno, func.attr))

        self.assertEqual(missing, [])

    def test_search_contains_uses_literal_matching(self):
        missing = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "contains"):
                continue

            regex_false = any(
                keyword.arg == "regex"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is False
                for keyword in node.keywords
            )
            if not regex_false:
                missing.append(node.lineno)

        self.assertEqual(missing, [])

    def test_indicator_menu_matches_available_handlers(self):
        displayed = self._displayed_indicators()
        handled = self._handled_indicators()
        special = {"Densité de population (hab/km²)", "Population municipale"}

        missing = [
            indicator
            for indicator in displayed
            if (
                indicator not in handled
                and indicator not in special
                and not indicator.startswith("Population municipale")
            )
        ]

        self.assertEqual(missing, [])

    def test_generated_artifacts_are_ignored(self):
        gitignore = GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()
        self.assertIn("node_modules/", gitignore)
        self.assertIn(".DS_Store", gitignore)
        self.assertIn("__pycache__/", gitignore)

    def _displayed_indicators(self):
        indicators = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "INDICATORS_CONFIG"
                for target in node.targets
            ):
                continue
            for value in node.value.values:
                if not isinstance(value, ast.List):
                    continue
                for item in value.elts:
                    if isinstance(item, ast.Constant) and isinstance(item.value, str):
                        indicators.append(item.value)
        return indicators

    def _handled_indicators(self):
        handled = set()
        for node in ast.walk(self.tree):
            if (
                isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Name)
                and node.left.id == "indicator_type"
            ):
                handled.update(
                    comp.value
                    for comp in node.comparators
                    if isinstance(comp, ast.Constant) and isinstance(comp.value, str)
                )

            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "mapping_rp"
                for target in node.targets
            ):
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            handled.update(
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
        return handled


if __name__ == "__main__":
    unittest.main()
