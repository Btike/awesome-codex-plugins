"""Remote source icons must enforce the same constraints as local plugin bundles."""

import importlib.util
import io
import json
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate-plugin-pr.py"
SPEC = importlib.util.spec_from_file_location("validate_plugin_pr", SCRIPT)
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class RemoteIconValidationTests(unittest.TestCase):
    @staticmethod
    def source_archive(icon_path: str, icon_bytes: bytes) -> zipfile.ZipFile:
        manifest = {
            "name": "example",
            "version": "1.0.0",
            "description": "Example plugin",
            "repository": "https://github.com/maker/example",
            "license": "MIT",
            "interface": {
                "displayName": "Example",
                "shortDescription": "Example plugin",
                "composerIcon": "./" + icon_path,
            },
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "example-main/.codex-plugin/plugin.json", json.dumps(manifest)
            )
            archive.writestr("example-main/" + icon_path, icon_bytes)
        return zipfile.ZipFile(io.BytesIO(buffer.getvalue()))

    def validate(self, path: str, content: bytes) -> list[str]:
        archive = self.source_archive(path, content)
        with archive, patch.object(
            validator, "fetch_repo_archive", return_value=archive
        ):
            errors, warnings = validator.validate_remote_repo("maker", "example")
        self.assertEqual(warnings, [])
        return errors

    def test_rejects_unsupported_extension(self):
        errors = self.validate("assets/icon.txt", b"not an image")
        self.assertTrue(any("unsupported format" in error for error in errors), errors)

    def test_rejects_embedded_base64_svg(self):
        errors = self.validate(
            "assets/icon.svg",
            b'<svg><image href="data:image/png;base64,AAA="/></svg>',
        )
        self.assertIn("SVG icon contains embedded base64 raster data.", errors)

    def test_rejects_placeholder_svg(self):
        errors = self.validate("assets/icon.svg", b"<svg><!-- TODO --></svg>")
        self.assertIn("SVG icon contains TODO/PLACEHOLDER text.", errors)

    def test_accepts_valid_small_png(self):
        errors = self.validate("assets/icon.png", b"\\x89PNG\\r\\n\\x1a\\n")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
