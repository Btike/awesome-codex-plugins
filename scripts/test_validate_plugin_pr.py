from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("validate-plugin-pr.py")
SPEC = importlib.util.spec_from_file_location("validate_plugin_pr", MODULE_PATH)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


class ReadmeEntryRegexTests(unittest.TestCase):
    def parse_new_entries(self, diff: str, base_readme: str, current_readme: str):
        with (
            patch.object(VALIDATOR, "git", side_effect=(diff, base_readme)),
            patch.object(Path, "read_text", return_value=current_readme),
        ):
            return VALIDATOR.get_new_readme_entries("origin/main")

    def test_repository_urls_preserve_owner_repo_and_description_groups(self) -> None:
        for suffix in (
            "",
            "/",
            "/tree/main",
            "/tree/feat+plugin",
            "/tree/release%2Fv1",
            "?tab=readme-ov-file#setup",
        ):
            with self.subTest(suffix=suffix):
                url = "https://github.com/example/plugin" + suffix
                entry = f"- [Example]({url}) - A useful plugin."

                match = VALIDATOR.README_ENTRY_RE.fullmatch(entry)

                self.assertIsNotNone(match)
                assert match is not None
                self.assertEqual(
                    match.groups(),
                    (
                        "Example",
                        url,
                        "example",
                        "plugin",
                        "main" if suffix == "/tree/main" else (
                            "feat+plugin" if suffix == "/tree/feat+plugin" else (
                                "release%2Fv1" if suffix == "/tree/release%2Fv1" else None
                            )
                        ),
                        "A useful plugin.",
                    ),
                )

    def test_trailing_slash_before_query_is_not_a_new_repository(self) -> None:
        diff = "\n".join(
            (
                "diff --git a/README.md b/README.md",
                "- [Example](https://github.com/example/plugin?tab=readme) - Existing entry.",
                "+- [Example](https://github.com/example/plugin/?tab=readme) - Existing entry.",
            )
        )
        base_readme = (
            "## Community Plugins\n"
            "- [Example](https://github.com/example/plugin?tab=readme) - Existing entry."
        )
        current_readme = (
            "## Community Plugins\n"
            "- [Example](https://github.com/example/plugin/?tab=readme) - Existing entry."
        )

        entries = self.parse_new_entries(diff, base_readme, current_readme)

        self.assertEqual(entries, [])

    def test_seen_urls_are_normalized_before_deduplication(self) -> None:
        diff = "\n".join(
            (
                "diff --git a/README.md b/README.md",
                "+- [Example](https://github.com/example/plugin?tab=readme) - First entry.",
                "+- [Example](https://github.com/example/plugin/?tab=readme) - Duplicate entry.",
            )
        )

        current_readme = (
            "## Community Plugins\n"
            "### Development & Workflow\n"
            "- [Example](https://github.com/example/plugin?tab=readme) - First entry.\n"
            "- [Example](https://github.com/example/plugin/?tab=readme) - Duplicate entry."
        )
        entries = self.parse_new_entries(diff, "", current_readme)

        self.assertEqual(len(entries), 1)

    def test_tree_ref_and_subpath_case_are_preserved(self) -> None:
        base_readme = (
            "## Community Plugins\n"
            "- [Example](https://github.com/example/plugin/tree/Main/Plugins/Foo) "
            "- Existing entry."
        )
        diff = "\n".join(
            (
                "diff --git a/README.md b/README.md",
                "- " + base_readme,
                "+- [Example](https://github.com/example/plugin/tree/main/plugins/foo) "
                "- Updated entry.",
            )
        )

        current_readme = (
            "## Community Plugins\n"
            "- [Example](https://github.com/example/plugin/tree/main/plugins/foo) "
            "- Updated entry."
        )
        entries = self.parse_new_entries(diff, base_readme, current_readme)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source_ref"], "main/plugins/foo")

    def test_branch_case_changes_are_not_normalized_away(self) -> None:
        base_readme = (
            "## Community Plugins\n"
            "- [Example](https://github.com/example/plugin/tree/Main) - Existing.\n"
        )
        current_readme = base_readme.replace("/tree/Main", "/tree/main")
        diff = "\n".join(
            (
                "diff --git a/README.md b/README.md",
                "- " + base_readme.splitlines()[-1],
                "+" + current_readme.splitlines()[-1],
            )
        )

        entries = self.parse_new_entries(diff, base_readme, current_readme)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source_ref"], "main")

    def test_branch_ref_is_passed_to_remote_manifest_validation(self) -> None:
        with (
            patch.object(VALIDATOR, "fetch_repo_archive") as fetch,
            patch.object(VALIDATOR, "find_plugin_root", return_value=None),
        ):
            fetch.return_value = object()
            errors, _warnings = VALIDATOR.validate_remote_repo(
                "example", "plugin", "feat+plugin"
            )

        fetch.assert_called_once_with("example", "plugin", "feat+plugin")
        self.assertTrue(any("does not contain" in error for error in errors))

    def test_related_project_links_are_not_plugin_submissions(self) -> None:
        diff = "\n".join(
            (
                "diff --git a/README.md b/README.md",
                "+- [Koto](https://github.com/mehrdadxzaker/Koto) - Related project.",
            )
        )
        current_readme = (
            "## Community Plugins\n"
            "### Development & Workflow\n"
            "- [Existing](https://github.com/example/plugin) - Plugin.\n"
            "## Related Projects\n"
            "- [Koto](https://github.com/mehrdadxzaker/Koto) - Related project."
        )

        entries = self.parse_new_entries(diff, "", current_readme)

        self.assertEqual(entries, [])



    def test_distinct_named_plugins_can_share_one_repo(self) -> None:
        repo_url = "https://github.com/redfox-data/redfox-agent-plugins"
        umbrella = f"- [Redfox Agent Plugins]({repo_url}) - Umbrella."
        individual = [
            f"- [account-video-downloader]({repo_url}) - Account videos.",
            f"- [image-gen]({repo_url}) - Image generation.",
        ]
        diff = "\n".join(
            ["diff --git a/README.md b/README.md", "-" + umbrella]
            + ["+" + line for line in individual]
        )
        base = "## Community Plugins\n### Development & Workflow\n" + umbrella
        head = "## Community Plugins\n### Development & Workflow\n" + "\n".join(individual)
        entries = self.parse_new_entries(diff, base, head)
        self.assertEqual(
            [entry["display_name"] for entry in entries],
            ["account-video-downloader", "image-gen"],
        )
        self.assertEqual({entry["url"] for entry in entries}, {repo_url})


class MultiPluginManifestTests(unittest.TestCase):
    """Select the named bundle and fail closed for ambiguous or unknown names."""

    def build_archive(self) -> zipfile.ZipFile:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as output:
            for name, suffix in (("alpha", ".png"), ("beta", ".txt")):
                manifest = {
                    "name": name,
                    "version": "1.0.0",
                    "description": "Example",
                    "repository": "https://github.com/maker/demo",
                    "license": "MIT",
                    "interface": {
                        "displayName": name.title(),
                        "shortDescription": "Example",
                        "composerIcon": f"./assets/icon{suffix}",
                    },
                }
                root = f"demo-main/plugins/{name}"
                output.writestr(f"{root}/.codex-plugin/plugin.json", json.dumps(manifest))
                if name == "alpha":
                    output.writestr(f"{root}/assets/icon{suffix}", b"test")
        return zipfile.ZipFile(io.BytesIO(buffer.getvalue()))

    def test_validation_reaches_named_nested_manifest(self) -> None:
        with self.build_archive() as archive:
            with patch.object(VALIDATOR, "fetch_repo_archive", return_value=archive):
                good, _warnings = VALIDATOR.validate_remote_repo(
                    "maker", "demo", display_name="Alpha"
                )
                bad, _warnings = VALIDATOR.validate_remote_repo(
                    "maker", "demo", display_name="beta"
                )
        self.assertEqual(good, [])
        self.assertTrue(
            any("Icon file not found" in error for error in bad), bad
        )

    def test_one_source_manifest_cannot_validate_two_distinct_names(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as output:
            root = "demo-main/plugins/alpha"
            output.writestr(f"{root}/.codex-plugin/plugin.json", json.dumps({
                "name": "alpha", "version": "1.0.0",
                "description": "Example",
                "repository": "https://github.com/maker/demo",
                "license": "MIT",
                "interface": {
                    "displayName": "Alpha", "shortDescription": "Example",
                    "composerIcon": "./assets/icon.png",
                },
            }))
            output.writestr(f"{root}/assets/icon.png", b"image")
        with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
            with patch.object(VALIDATOR, "fetch_repo_archive", return_value=archive):
                good, _ = VALIDATOR.validate_remote_repo(
                    "maker", "demo", display_name="Alpha", require_named_match=True
                )
                bad, _ = VALIDATOR.validate_remote_repo(
                    "maker", "demo", display_name="Another Plugin", require_named_match=True
                )
                legacy, _ = VALIDATOR.validate_remote_repo(
                    "maker", "demo", display_name="Legacy Branding"
                )
        self.assertEqual(good, [])
        self.assertTrue(any("uniquely matching manifest" in x for x in bad), bad)
        self.assertEqual(legacy, [])

    def test_corrupt_unrelated_nested_manifest_does_not_crash_selection(self) -> None:
        # A named valid bundle may share its repository archive with a
        # corrupt unrelated manifest. Both ZIP integrity failures are handled.
        for failure in (zipfile.BadZipFile("bad CRC"), EOFError("truncated")):
            with self.subTest(failure=type(failure).__name__):
                with self.build_archive() as archive:
                    actual_read = archive.read

                    def guarded_read(name, *args, **kwargs):
                        if str(name).endswith(
                            "/plugins/beta/.codex-plugin/plugin.json"
                        ):
                            raise failure
                        return actual_read(name, *args, **kwargs)

                    with patch.object(
                        VALIDATOR, "fetch_repo_archive", return_value=archive
                    ), patch.object(archive, "read", side_effect=guarded_read):
                        errors, warnings = VALIDATOR.validate_remote_repo(
                            "maker", "demo", display_name="Alpha"
                        )
                self.assertEqual(errors, [])
                self.assertEqual(warnings, [])

    def test_unknown_nested_manifest_name_fails_closed(self) -> None:
        with self.build_archive() as archive:
            with patch.object(VALIDATOR, "fetch_repo_archive", return_value=archive):
                errors, _warnings = VALIDATOR.validate_remote_repo(
                    "maker", "demo", display_name="unknown"
                )
        self.assertTrue(
            any("uniquely matching manifest" in error for error in errors), errors
        )


class ExistingMirrorSourceValidationTests(unittest.TestCase):
    def test_second_named_plugin_is_not_skipped_by_existing_umbrella_mirror(self) -> None:
        import contextlib
        import io as output_io
        import tempfile

        repo_url = "https://github.com/example/multi-plugin"
        entry = {
            "display_name": "Beta",
            "url": repo_url,
            "owner": "example",
            "repo": "multi-plugin",
            "description": "Second separately named plugin.",
            "source_ref": None,
        }
        with tempfile.TemporaryDirectory() as folder:
            plugin_root = Path(folder) / "example" / "multi-plugin"
            manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text('{"name": "alpha"}', encoding="utf-8")
            for names, expected_calls in (
                ({"Alpha", "Beta"}, 1),
                ({"Beta"}, 0),
            ):
                with self.subTest(names=sorted(names)):
                    keys = {(repo_url, name) for name in names}
                    with (
                        patch.object(VALIDATOR, "git", return_value="valid-base"),
                        patch.object(VALIDATOR, "get_changed_plugin_dirs", return_value=[]),
                        patch.object(VALIDATOR, "get_new_readme_entries", return_value=[entry]),
                        patch.object(VALIDATOR, "community_plugin_keys", return_value=keys),
                        patch.object(VALIDATOR, "PLUGINS_DIR", Path(folder)),
                        patch.object(Path, "read_text", return_value="## Community Plugins"),
                        patch.object(VALIDATOR, "validate_remote_repo", return_value=([], [])) as validate,
                        patch.object(sys, "argv", ["validate-plugin-pr.py", "--base-ref", "origin/main"]),
                        contextlib.redirect_stdout(output_io.StringIO()),
                    ):
                        with self.assertRaises(SystemExit) as finished:
                            VALIDATOR.main()
                    self.assertEqual(finished.exception.code, 0)
                    self.assertEqual(validate.call_count, expected_calls)
                    if expected_calls:
                        validate.assert_called_once_with(
                            "example", "multi-plugin", None, "Beta",
                            require_named_match=True,
                        )


if __name__ == "__main__":
    unittest.main()
