import importlib.util
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "generate_plugins_json", Path(__file__).resolve().parents[1] / "scripts/generate_plugins_json.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GeneratedBundleCleanupTests(unittest.TestCase):
    def test_equivalent_readme_aliases_share_one_bundle_identity(self):
        with tempfile.TemporaryDirectory() as scratch:
            readme = Path(scratch) / "README.md"
            readme.write_text(
                "## Community Plugins\\n"
                "- [image-gen](https://github.com/redfox-data/redfox-agent-plugins) "
                "- First display label.\\n"
                "- [Image Gen](https://github.com/REDFOX-DATA/redfox-agent-plugins) "
                "- Same upstream bundle with alternate casing and spacing.\\n"
                "- [transcript-extractor](https://github.com/redfox-data/redfox-agent-plugins) "
                "- Independently named source plugin.\\n",
                encoding="utf-8",
            )
            entries = MODULE.parse_plugins(readme)

        self.assertEqual([entry["name"] for entry in entries], [
            "image-gen", "transcript-extractor",
        ])
        self.assertTrue(all(entry.get("_mirror_subpath") == "true" for entry in entries))

    def test_branch_qualified_readme_entry_uses_source_branch_metadata(self):
        with tempfile.TemporaryDirectory() as scratch:
            readme = Path(scratch) / "README.md"
            readme.write_text(
                "## Community Plugins\n"
                "- [WTFM](https://github.com/SupportPages-io/wtfm/tree/codex-plugin) "
                "- A plugin from a non-default source branch.\n",
                encoding="utf-8",
            )

            plugins = MODULE.parse_plugins(readme)

        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0]["owner"], "SupportPages-io")
        self.assertEqual(plugins[0]["repo"], "wtfm")
        self.assertEqual(plugins[0]["source_ref"], "codex-plugin")
        self.assertEqual(
            plugins[0]["install_url"],
            "https://raw.githubusercontent.com/SupportPages-io/wtfm/"
            "codex-plugin/.codex-plugin/plugin.json",
        )

    def test_branch_archive_and_manifest_urls_use_the_linked_ref(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return b"archive"

        with (
            patch.object(MODULE.urllib.request, "urlopen", return_value=Response()) as urlopen,
            patch.object(MODULE.zipfile, "ZipFile", return_value=object()),
        ):
            MODULE.fetch_repo_archive("SupportPages-io", "wtfm", "feat+plugin")

        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://github.com/SupportPages-io/wtfm/archive/refs/heads/feat%2Bplugin.zip",
        )
        self.assertEqual(
            MODULE.build_raw_manifest_url(
                {"owner": "SupportPages-io", "repo": "wtfm", "source_ref": "feat+plugin"},
                "",
            ),
            "https://raw.githubusercontent.com/SupportPages-io/wtfm/feat%2Bplugin/"
            ".codex-plugin/plugin.json",
        )

    def test_removes_retired_identity_and_preserves_canonical_bundle(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch) / "plugins"
            legacy = root / "samuelbushi/uizze"
            canonical = root / "uizze/uizze"
            failed_fetch = root / "other/listed"
            for bundle in (legacy, canonical, failed_fetch):
                bundle.mkdir(parents=True)
                (bundle / "SKILL.md").write_text(bundle.as_posix())
            canonical_bytes = (canonical / "SKILL.md").read_bytes()
            listed = [{"owner": "uizze", "repo": "uizze"}, {"owner": "other", "repo": "listed"}]
            self.assertEqual(MODULE.prune_unlisted_bundles(root, listed), ["samuelbushi/uizze"])
            self.assertFalse(legacy.exists())
            self.assertEqual((canonical / "SKILL.md").read_bytes(), canonical_bytes)
            self.assertTrue((failed_fetch / "SKILL.md").is_file())
            self.assertEqual(MODULE.prune_unlisted_bundles(root, listed), [])

    def test_unlisted_and_local_link_bundles_are_preserved(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch) / "plugins"
            identities = ("samuelbushi/uizze", "uizze/uizze", "mturac/changelog-forge", "other/unlisted")
            for identity in identities:
                bundle = root / identity
                bundle.mkdir(parents=True)
                (bundle / "SKILL.md").write_text(identity)
            readme = Path(scratch) / "README.md"
            readme.write_text("## Community Plugins\n"
                              "- [UIZZE](https://github.com/uizze/uizze) - Canonical.\n"
                              "- [Changelog Forge](./plugins/mturac/changelog-forge) - Local.\n")
            listed = MODULE.parse_plugins(readme)
            self.assertEqual(MODULE.prune_unlisted_bundles(root, listed), ["samuelbushi/uizze"])
            for identity in identities[1:]:
                self.assertEqual((root / identity / "SKILL.md").read_text(), identity)

    def test_local_link_retains_even_an_explicit_retirement(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch) / "plugins"
            legacy = root / "samuelbushi/uizze"
            legacy.mkdir(parents=True)
            readme = Path(scratch) / "README.md"
            readme.write_text("## Community Plugins\n"
                              "- [Legacy](./plugins/SamuelBushi/UIZZE) - Still listed.\n")
            self.assertEqual(MODULE.prune_unlisted_bundles(root, MODULE.parse_plugins(readme)), [])
            self.assertTrue(legacy.is_dir())

    def test_local_link_is_never_sent_to_upstream_mirroring(self):
        with tempfile.TemporaryDirectory() as scratch:
            base = Path(scratch)
            readme = base / "README.md"
            readme.write_text("## Community Plugins\n"
                              "- [Local](./plugins/local/bundle) - Retain only.\n")
            with patch.multiple(MODULE, README=readme, PLUGINS_ROOT=base / "plugins",
                                OUTPUT=base / "plugins.json", MARKETPLACE_OUTPUT=base / "marketplace.json"), \
                    patch.object(MODULE, "mirror_plugin_bundle") as mirror:
                MODULE.main()
                mirror.assert_not_called()

    def test_partial_parse_aborts_before_any_deletion(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch) / "plugins"
            legacy = root / "samuelbushi/uizze"
            legacy.mkdir(parents=True)
            (legacy / "SKILL.md").write_text("keep")
            readme = Path(scratch) / "README.md"
            readme.write_text("## Community Plugins\n"
                              "- [UIZZE](https://github.com/uizze/uizze) - Canonical.\n"
                              "- [Unknown](https://example.com/plugin) - Unsupported.\n")
            with self.assertRaisesRegex(ValueError, "Unrecognized Community Plugins entry"):
                MODULE.prune_unlisted_bundles(root, MODULE.parse_plugins(readme))
            self.assertEqual((legacy / "SKILL.md").read_text(), "keep")

    def test_refuses_symlink_without_touching_external_files(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch) / "plugins"
            outside = Path(scratch) / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "keep.txt").write_text("keep")
            (root / "owner").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                MODULE.prune_unlisted_bundles(root, [{"owner": "uizze", "repo": "uizze"}])
            self.assertEqual((outside / "keep.txt").read_text(), "keep")

    def test_empty_or_failed_source_parse_never_prunes_existing_bundles(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch) / "plugins"
            bundle = root / "uizze/uizze"
            bundle.mkdir(parents=True)
            (bundle / "SKILL.md").write_text("preserve")
            readme = Path(scratch) / "README.md"
            readme.write_text("# Catalog\n## Community Plugins\nNo recognized plugin entries.\n")
            parsed = MODULE.parse_plugins(readme)
            self.assertEqual(parsed, [])
            with self.assertRaisesRegex(ValueError, "empty parsed README"):
                MODULE.prune_unlisted_bundles(root, parsed)
            self.assertEqual((bundle / "SKILL.md").read_text(), "preserve")

    def test_preserves_listed_bundle_across_owner_and_repository_display_case(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch) / "plugins"
            bundle = root / "UIZZE/UiZzE"
            bundle.mkdir(parents=True)
            (bundle / "SKILL.md").write_text("canonical")
            listed = [{"owner": "uizze", "repo": "uizze"}]
            self.assertEqual(MODULE.prune_unlisted_bundles(root, listed), [])
            self.assertEqual((bundle / "SKILL.md").read_text(), "canonical")

    def test_dangling_root_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch) / "plugins"
            root.symlink_to(Path(scratch) / "missing", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "root must not be a symlink"):
                MODULE.prune_unlisted_bundles(root, [{"owner": "uizze", "repo": "uizze"}])
            self.assertTrue(root.is_symlink())

    def test_later_invalid_owner_or_repo_aborts_before_any_retirement(self):
        for invalid_owner in (False, True):
            with self.subTest(invalid_owner=invalid_owner), tempfile.TemporaryDirectory() as scratch:
                root = Path(scratch) / "plugins"
                retired = root / "samuelbushi/uizze"
                retired.mkdir(parents=True)
                (retired / "SKILL.md").write_text("preserve-until-preflight-completes")
                invalid = root / "z"
                if not invalid_owner:
                    invalid.mkdir()
                    invalid = invalid / "linked"
                invalid.symlink_to(Path(scratch) / "missing", target_is_directory=True)
                with self.assertRaises(ValueError):
                    MODULE.prune_unlisted_bundles(root, [{"owner": "uizze", "repo": "uizze"}])
                self.assertEqual((retired / "SKILL.md").read_text(), "preserve-until-preflight-completes")


class BranchAwareSourceTests(unittest.TestCase):
    def test_parse_plugins_uses_source_ref_from_repository_link(self):
        with tempfile.TemporaryDirectory() as scratch:
            readme = Path(scratch) / "README.md"
            readme.write_text(
                "## Community Plugins\n"
                "- [WTFM](https://github.com/SupportPages-io/wtfm/tree/codex-plugin) - Published bundle.\n"
            )

            plugin = MODULE.parse_plugins(readme)[0]

            self.assertEqual(plugin["owner"], "SupportPages-io")
            self.assertEqual(plugin["repo"], "wtfm")
            self.assertEqual(plugin["source_ref"], "codex-plugin")
            self.assertEqual(
                plugin["install_url"],
                "https://raw.githubusercontent.com/SupportPages-io/wtfm/codex-plugin/.codex-plugin/plugin.json",
            )

    def test_fetch_and_manifest_urls_keep_the_selected_branch(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"archive"

        with patch.object(MODULE.urllib.request, "urlopen", return_value=Response()) as urlopen, \
                patch.object(MODULE.zipfile, "ZipFile", return_value="archive"):
            self.assertEqual(MODULE.fetch_repo_archive("SupportPages-io", "wtfm", "codex-plugin"), "archive")

        self.assertEqual(
            urlopen.call_args.args[0].full_url,
            "https://github.com/SupportPages-io/wtfm/archive/refs/heads/codex-plugin.zip",
        )
        self.assertEqual(
            MODULE.build_raw_manifest_url(
                {"owner": "SupportPages-io", "repo": "wtfm", "source_ref": "codex-plugin"},
                "",
            ),
            "https://raw.githubusercontent.com/SupportPages-io/wtfm/codex-plugin/.codex-plugin/plugin.json",
        )


class MultiBundleRootResolutionTests(unittest.TestCase):
    def archive_for(self, plugin_names):
        import io
        import json
        import zipfile

        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as output:
            for directory, manifest_name in plugin_names:
                output.writestr(
                    f"demo-main/plugins/{directory}/.codex-plugin/plugin.json",
                    json.dumps({"name": manifest_name, "interface": {
                        "displayName": manifest_name.title(),
                    }}),
                )
        data.seek(0)
        return zipfile.ZipFile(data)

    def test_named_shared_repo_entries_resolve_only_unique_manifest(self):
        with self.archive_for([("alpha", "alpha"), ("beta", "beta")]) as archive:
            names = set(archive.namelist())
            plugin = {"name": "beta", "_mirror_subpath": "true"}
            self.assertEqual(
                MODULE.resolve_plugin_root(archive, names, plugin).as_posix(),
                "demo-main/plugins/beta",
            )
            with self.assertRaisesRegex(ValueError, "No uniquely matching nested manifest"):
                MODULE.resolve_plugin_root(
                    archive, names,
                    {"name": "wrong bundle", "_mirror_subpath": "true"},
                )
            # Keep legacy single-entry umbrella behavior without silently
            # publishing a different bundle for each new same-repo listing.
            self.assertEqual(
                MODULE.resolve_plugin_root(
                    archive, names, {"name": "umbrella project"},
                ).as_posix(),
                "demo-main/plugins/alpha",
            )

    def test_unrelated_corrupt_manifest_does_not_block_named_bundle(self):
        import zipfile

        with self.archive_for([("alpha", "alpha"), ("beta", "beta")]) as archive:
            real_read = archive.read

            def corrupt_other(name, *args, **kwargs):
                if str(name).endswith("/plugins/beta/.codex-plugin/plugin.json"):
                    raise zipfile.BadZipFile("unrelated corrupt manifest")
                return real_read(name, *args, **kwargs)

            with patch.object(archive, "read", side_effect=corrupt_other):
                selected = MODULE.resolve_plugin_root(
                    archive, set(archive.namelist()),
                    {"name": "alpha", "_mirror_subpath": "true"},
                )
                self.assertEqual(selected.as_posix(), "demo-main/plugins/alpha")
                with self.assertRaisesRegex(ValueError, "No uniquely matching nested manifest"):
                    MODULE.resolve_plugin_root(
                        archive, set(archive.namelist()),
                        {"name": "beta", "_mirror_subpath": "true"},
                    )

    def test_short_description_is_not_a_bundle_identifier(self):
        import json

        with self.archive_for([("alpha", "alpha"), ("beta", "beta")]) as archive:
            real_read = archive.read

            def overlapping_prose(name, *args, **kwargs):
                if str(name).endswith("/plugins/beta/.codex-plugin/plugin.json"):
                    return json.dumps({"name": "beta", "interface": {
                        "displayName": "Beta", "shortDescription": "alpha",
                    }}).encode("utf-8")
                return real_read(name, *args, **kwargs)

            with patch.object(archive, "read", side_effect=overlapping_prose):
                selected = MODULE.resolve_plugin_root(
                    archive, set(archive.namelist()),
                    {"name": "alpha", "_mirror_subpath": "true"},
                )
            self.assertEqual(selected.as_posix(), "demo-main/plugins/alpha")

    def test_single_nested_manifest_must_match_each_shared_repo_listing(self):
        with self.archive_for([("alpha", "alpha")]) as archive:
            names = set(archive.namelist())
            self.assertEqual(
                MODULE.resolve_plugin_root(
                    archive, names,
                    {"name": "alpha", "_mirror_subpath": "true"},
                ).as_posix(),
                "demo-main/plugins/alpha",
            )
            with self.assertRaisesRegex(ValueError, "No uniquely matching nested manifest"):
                MODULE.resolve_plugin_root(
                    archive, names,
                    {"name": "unrelated", "_mirror_subpath": "true"},
                )
            self.assertEqual(
                MODULE.resolve_plugin_root(
                    archive, names, {"name": "legacy umbrella"},
                ).as_posix(),
                "demo-main/plugins/alpha",
            )

    def test_ambiguous_shared_repo_manifest_name_fails_closed(self):
        with self.archive_for([("alpha", "shared"), ("beta", "shared")]) as archive:
            with self.assertRaisesRegex(ValueError, "No uniquely matching nested manifest"):
                MODULE.resolve_plugin_root(
                    archive, set(archive.namelist()),
                    {"name": "shared", "_mirror_subpath": "true"},
                )


if __name__ == "__main__":
    unittest.main()
