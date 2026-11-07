"""Regression checks for package-relative shared-validator imports."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


FILTERS_SOURCE = Path(__file__).resolve().parents[1] / "lotterytally" / "filters.py"


class PackageImportsTests(unittest.TestCase):
    def run_isolated_import(self, script, package_validator=None,
                            top_level_validator=None):
        """Import a copy of the real module with isolated temporary dependencies."""
        with tempfile.TemporaryDirectory(prefix="lotterytally-imports-") as folder:
            sandbox = Path(folder)
            package = sandbox / "lotterytally"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            shutil.copy2(FILTERS_SOURCE, package / "filters.py")
            if package_validator is not None:
                (package / "validators.py").write_text(
                    textwrap.dedent(package_validator), encoding="utf-8")
            if top_level_validator is not None:
                (sandbox / "validators.py").write_text(
                    textwrap.dedent(top_level_validator), encoding="utf-8")

            # -I excludes user-site packages and PYTHONPATH; insert only this fixture.
            bootstrap = f"import sys\nsys.path.insert(0, {str(sandbox)!r})\n"
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c",
                 bootstrap + textwrap.dedent(script)],
                cwd=sandbox, capture_output=True, text=True, encoding="utf-8",
                timeout=30,
            )
            self.assertEqual(
                result.returncode, 0,
                f"Isolated import failed:\n{result.stdout}\n{result.stderr}",
            )
            return json.loads(result.stdout)

    def test_missing_package_validator_uses_fallback_and_ignores_top_level_module(self):
        result = self.run_isolated_import(
            """
            import json
            from datetime import date
            from lotterytally.filters import parse_filters

            criteria = parse_filters({"min_amount": "0.29"}, date(2026, 11, 7))
            print(json.dumps({"cents": criteria["min_amount_cents"]}))
            """,
            top_level_validator="""
            raise RuntimeError("The top-level validators module must not be imported")
            """,
        )
        self.assertEqual(result, {"cents": 29})

    def test_package_validator_is_called_for_amount_conversion(self):
        result = self.run_isolated_import(
            """
            import json
            from datetime import date
            from lotterytally import validators
            from lotterytally.filters import parse_filters

            criteria = parse_filters({"min_amount": "45.50"}, date(2026, 11, 7))
            print(json.dumps({
                "cents": criteria["min_amount_cents"],
                "calls": validators.calls,
            }))
            """,
            package_validator="""
            calls = []

            def parse_amount_cents(text):
                calls.append(text)
                return 4550
            """,
        )
        self.assertEqual(result, {"cents": 4550, "calls": ["45.50"]})

    def test_missing_dependency_inside_package_validator_is_not_hidden(self):
        missing_dependency = "lotterytally_test_missing_dependency_674c990f"
        result = self.run_isolated_import(
            """
            import json

            try:
                import lotterytally.filters
            except ModuleNotFoundError as exc:
                print(json.dumps({"missing": exc.name}))
            else:
                raise AssertionError("A shared-validator dependency error was hidden")
            """,
            package_validator=f"import {missing_dependency}\n",
        )
        self.assertEqual(result, {"missing": missing_dependency})


if __name__ == "__main__":
    unittest.main()
