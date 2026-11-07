"""Integration checks for the real shared amount-validation contract."""

from datetime import date
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from lotterytally import filters, validators


TODAY = date(2026, 11, 7)


class PackageImportsTests(unittest.TestCase):
    def test_filters_use_the_real_shared_amount_range_and_error_type(self):
        self.assertIs(filters.parse_amount_range, validators.parse_amount_range)
        self.assertIs(filters.ValidationError, validators.ValidationError)

    def test_valid_ranges_match_shared_rules_including_exact_large_amounts(self):
        for minimum, maximum in (
            ("", ""), ("0.01", ""), ("", "45.50"),
            ("30", "50"), ("45.5", "45.50"), (" 0.29 ", "1"),
            ("1234567890123456789012345678901234567890.12",
             "1234567890123456789012345678901234567890.12"),
        ):
            with self.subTest(minimum=minimum, maximum=maximum):
                expected = validators.parse_amount_range(minimum, maximum)
                criteria = filters.parse_filters({
                    "min_amount": minimum, "max_amount": maximum,
                }, TODAY)
                self.assertEqual(
                    (criteria["min_amount_cents"], criteria["max_amount_cents"]),
                    expected,
                )

    def test_invalid_ranges_preserve_shared_field_errors(self):
        for minimum, maximum in (
            ("0", ""), ("-1", "12.345"), ("1e2", "50"),
            ("４５", ""), ("1,000", "50"), ("50", "30"),
        ):
            with self.subTest(minimum=minimum, maximum=maximum):
                with self.assertRaises(validators.ValidationError) as shared_error:
                    validators.parse_amount_range(minimum, maximum)
                with self.assertRaises(filters.FilterValidationError) as error:
                    filters.parse_filters({
                        "min_amount": minimum, "max_amount": maximum,
                    }, TODAY)
                self.assertEqual(error.exception.errors, shared_error.exception.errors)

    def test_amount_errors_are_aggregated_with_other_filter_fields(self):
        with self.assertRaises(validators.ValidationError) as shared_error:
            validators.parse_amount_range("-1", "12.345")
        with self.assertRaises(filters.FilterValidationError) as error:
            filters.parse_filters({
                "min_amount": "-1", "max_amount": "12.345",
                "start_date": "2026-02-29", "type": "transfer", "sort": "random",
            }, TODAY)
        self.assertTrue({"start_date", "type", "sort"}.issubset(error.exception.errors))
        for field, message in shared_error.exception.errors.items():
            self.assertEqual(error.exception.errors[field], message)

    def test_missing_shared_validator_is_reported_instead_of_using_a_fallback(self):
        with tempfile.TemporaryDirectory(prefix="lotterytally-imports-") as folder:
            sandbox = Path(folder)
            package = sandbox / "lotterytally"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            shutil.copy2(Path(filters.__file__), package / "filters.py")
            (sandbox / "validators.py").write_text(
                'raise RuntimeError("A top-level validators module was imported")\n',
                encoding="utf-8",
            )
            script = (
                f"import sys\nsys.path.insert(0, {str(sandbox)!r})\n"
                "try:\n"
                "    import lotterytally.filters\n"
                "except ModuleNotFoundError as error:\n"
                "    print(error.name)\n"
                "else:\n"
                "    raise AssertionError('Missing shared validation was hidden')\n"
            )
            result = subprocess.run(
                # -S 跳过 site 初始化：pip install -e . 之类可编辑安装会把
                # 真实仓库注册成元路径 finder，优先级高于 sys.path，沙箱里
                # 缺失的 lotterytally.validators 会被真实模块顶替，报错
                # 检查就形同虚设。-S 让子进程只剩标准库和显式插入的沙箱。
                [sys.executable, "-I", "-S", "-B", "-c", script], cwd=sandbox,
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "lotterytally.validators")


if __name__ == "__main__":
    unittest.main()
