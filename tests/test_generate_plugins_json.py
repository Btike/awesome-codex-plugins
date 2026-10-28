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
    def test_mirror_refuses_symlinked_parent_before_deleting_old_bundle(self):
        import io
        import zipfile

        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch) / "plugins"
            root.mkdir()
            external = Path(scratch) / "outside"
            destination = external / "shared"
            destination.mkdir(parents=True)
            sentinel = destination / "keep.txt"
            sentinel.write_text("outside must survive", encoding="utf-8")
            (root / "maker").symlink_to(external, target_is_directory=True)

            archive_data = io.BytesIO()
            with zipfile.ZipFile(archive_data, "w") as creator:
                creator.writestr(
                    "shared-main/.codex-plugin/plugin.json",
                    '{"name":"demo","interface":{}}',
                )
            archive_data.seek(0)
            with (
                zipfile.ZipFile(archive_data) as archive,
                patch.object(MODULE, "fetch_repo_archive", return_value=archive),
                patch.object(MODULE, "PLUGINS_ROOT", root),
            ):
                with self.assertRaisesRegex(ValueError, "symlink"):
                    MODULE.mirror_plugin_bundle(
                        {"name": "demo", "owner": "maker", "repo": "shared"}
                    )
            self.assertEqual(
                sentinel.read_text(encoding="utf-8"), "outside must survive"
            )

    def test_mirror_destination_rejects_dangling_root_and_traversal(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch) / "plugins"
            root.symlink_to(Path(scratch) / "missing", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "root must not be a symlink"):
                MODULE.require_safe_mirror_destination(root, root / "maker" / "repo")
            root.unlink()
            root.mkdir()
            with self.assertRaisesRegex(ValueError, "traversal"):
                MODULE.require_safe_mirror_destination(
                    root, root / "maker" / ".." / ".." / "outside"
                )
            with self.assertRaisesRegex(ValueError, "drive syntax"):
                MODULE.require_safe_mirror_destination(
                    root, root / "maker:stream" / "repo"
                )

    def test_equivalent_readme_aliases_share_one_bundle_identity(self):
        with tempfile.TemporaryDirectory() as scratch:
            readme = Path(scratch) / "README.md"
            readme.write_text(
                "## Community Plugins\n"
                "- [image-gen](https://github.com/redfox-data/redfox-agent-plugins) "
                "- First display label.\n"
                "- [Image Gen](https://github.com/REDFOX-DATA/redfox-agent-plugins) "
                "- Same upstream bundle with alternate casing and spacing.\n"
                "- [transcript-extractor](https://github.com/redfox-data/redfox-agent-plugins) "
                "- Independently named source plugin.\n",
                encoding="utf-8",
            )
            entries = MODULE.parse_plugins(readme)

        self.assertEqual([entry["name"] for entry in entries], [
            "image-gen", "transcript-extractor",
        ])
        self.assertTrue(all(entry.get("_mirror_subpath") == "true" for entry in entries))

    def test_multi_bundle_repo_count_ignores_github_casing(self):
        with tempfile.TemporaryDirectory() as scratch:
            readme = Path(scratch) / "README.md"
            readme.write_text(
                "## Community Plugins\n"
                "- [image-gen](https://github.com/redfox-data/redfox-agent-plugins) "
                "- Image generation.\n"
                "- [transcript-extractor](https://github.com/REDFOX-DATA/REDFOX-AGENT-PLUGINS) "
                "- Transcript extraction.\n",
                encoding="utf-8",
            )
            entries = MODULE.parse_plugins(readme)

        self.assertEqual(len(entries), 2)
        self.assertTrue(all(entry.get("_mirror_subpath") == "true" for entry in entries))
        self.assertEqual({(entry["owner"], entry["repo"]) for entry in entries}, {
            ("redfox-data", "redfox-agent-plugins"),
        })

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

    def test_failed_sibling_bundle_does_not_remove_valid_public_record(self):
        # Multi-plugin sources can have one corrupt nested manifest. Only
        # that bundle should disappear from the generated public catalog.
        import json

        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            readme = root / "README.md"
            readme.write_text(
                "## Community Plugins\n"
                "- [Alpha](https://github.com/example/shared) - Works.\n"
                "- [Beta](https://github.com/example/shared) - Broken.\n",
                encoding="utf-8",
            )

            def mirror_one(plugin):
                if plugin["name"] == "Beta":
                    raise ValueError("corrupt nested manifest")
                return (
                    {"name": "alpha", "interface": {}},
                    "./plugins/example/shared/plugins/alpha",
                    "plugins/alpha",
                )

            with (
                patch.multiple(
                    MODULE, README=readme, PLUGINS_ROOT=root / "plugins",
                    OUTPUT=root / "plugins.json",
                    MARKETPLACE_OUTPUT=root / "marketplace.json",
                ),
                patch.object(MODULE, "mirror_plugin_bundle", side_effect=mirror_one),
            ):
                MODULE.main()
            public = json.loads((root / "plugins.json").read_text())
            marketplace = json.loads((root / "marketplace.json").read_text())

        self.assertEqual(public["total"], 1)
        self.assertEqual([p["name"] for p in public["plugins"]], ["Alpha"])
        self.assertEqual(
            [p["displayName"] for p in marketplace["plugins"]], ["Alpha"]
        )

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

    def test_casefold_bundle_name_matches_validator(self):
        # The validator uses casefold. The generated mirror must select the
        # same source manifest for a Unicode label such as "Straße".
        with self.archive_for([("strasse", "STRASSE"), ("other", "other")]) as archive:
            selected = MODULE.resolve_plugin_root(
                archive, set(archive.namelist()),
                {"name": "Straße", "_mirror_subpath": "true"},
            )
            self.assertEqual(selected.as_posix(), "demo-main/plugins/strasse")

    def test_nonlatin_manifest_name_resolves_without_alias_fallback(self):
        # Chinese and accented Latin source identities must not normalize to
        # an empty ASCII-only key or select an unrelated nested bundle.
        with self.archive_for([
            ("video", "视频下载"), ("eclair", "Éclair"), ("other", "other"),
        ]) as archive:
            names = set(archive.namelist())
            for display, directory in (("视频下载", "video"), ("éCLAIR", "eclair")):
                with self.subTest(display=display):
                    selected = MODULE.resolve_plugin_root(
                        archive, names,
                        {"name": display, "_mirror_subpath": "true"},
                    )
                    self.assertEqual(
                        selected.as_posix(), f"demo-main/plugins/{directory}",
                    )

    def test_archive_traversal_name_cannot_resolve_as_nested_plugin(self):
        # A crafted ZIP member must not become a candidate manifest even if
        # its declared name matches the README. Never select a path with '..'.
        import io
        import json
        import zipfile
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as output:
            output.writestr(
                "demo-main/../outside/.codex-plugin/plugin.json",
                json.dumps({"name": "outside"}),
            )
            output.writestr(
                "demo-main/plugins/safe/.codex-plugin/plugin.json",
                json.dumps({"name": "safe"}),
            )
        payload.seek(0)
        with zipfile.ZipFile(payload) as archive:
            names = set(archive.namelist())
            roots = MODULE.find_plugin_roots(names)
            self.assertEqual([root.as_posix() for root in roots], [
                "demo-main/plugins/safe",
            ])
            with self.assertRaisesRegex(ValueError, "No uniquely matching nested manifest"):
                MODULE.resolve_plugin_root(
                    archive, names, {"name": "outside", "_mirror_subpath": "true"},
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

    def test_non_ascii_only_labels_cannot_share_one_manifest_identity(self):
        # Both distinct source names normalize to the empty ASCII key.
        # Empty matches cannot authorize new mirrored bundle identities.
        with self.archive_for([("alpha", "🚀")]) as archive:
            names = set(archive.namelist())
            with self.assertRaisesRegex(ValueError, "No uniquely matching nested manifest"):
                MODULE.resolve_plugin_root(
                    archive, names,
                    {"name": "🔒", "_mirror_subpath": "true"},
                )

    def test_ambiguous_shared_repo_manifest_name_fails_closed(self):
        with self.archive_for([("alpha", "shared"), ("beta", "shared")]) as archive:
            with self.assertRaisesRegex(ValueError, "No uniquely matching nested manifest"):
                MODULE.resolve_plugin_root(
                    archive, set(archive.namelist()),
                    {"name": "shared", "_mirror_subpath": "true"},
                )



class UntrustedArchivePathTests(unittest.TestCase):
    def test_manifest_paths_reject_escape_and_absolute_locations(self):
        for unsafe in (
            "../secrets.json",
            "skills/../../secrets.json",
            "/tmp/external",
            r"..\\outside",
            "./skills/../../../outside",
            "skills/evil\\..\\..\\outside",
            "skills/\x00bad",
            "C:/Windows/evil.txt",
            "C:relative-stream",
            "assets/icon.svg:stream",
            r"\\\\server\\share\\file",
        ):
            with self.subTest(path=repr(unsafe)):
                self.assertEqual(MODULE.normalize_relative_path(unsafe), "")
        self.assertEqual(
            MODULE.normalize_relative_path("./skills/hooks"), "skills/hooks"
        )
        self.assertEqual(
            MODULE.normalize_relative_path(".mcp.json"), ".mcp.json"
        )

    def test_untrusted_zip_member_never_selects_parent_write(self):
        from pathlib import PurePosixPath

        root = PurePosixPath("demo-main/plugins/alpha")
        members = {
            "demo-main/plugins/alpha/skills/hook.py",
            "demo-main/plugins/alpha/skills/../../private.txt",
            "demo-main/plugins/alpha/skills/sub/../../../other.txt",
        }
        selected = set()
        MODULE.add_recursive_selection(selected, members, root, "skills")
        self.assertEqual(selected, {"skills/hook.py"})

    def test_archive_manifest_root_cannot_contain_parent_components(self):
        names = {
            "demo-main/plugins/alpha/.codex-plugin/plugin.json",
            "demo-main/plugins/../outside/.codex-plugin/plugin.json",
            "/tmp/rogue/.codex-plugin/plugin.json",
        }
        self.assertEqual(
            [p.as_posix() for p in MODULE.find_plugin_roots(names)],
            ["demo-main/plugins/alpha"],
        )



class MirroredIconContainmentTests(unittest.TestCase):
    def test_good_nested_icon_survives_root_relative_path(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root) / "plugins" / "nested"
            (base / "icons").mkdir(parents=True)
            (base / "icons" / "logo.svg").write_text("<svg/>")
            self.assertEqual(
                MODULE.verified_mirrored_icon_path(
                    "./icons/logo.svg", base, "./plugins/example/repo/plugins/nested"
                ),
                "./plugins/example/repo/plugins/nested/icons/logo.svg",
            )

    def test_manifest_icon_cannot_escape_bundle(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            base = workspace / "plugin"
            base.mkdir()
            (workspace / "outside.png").write_bytes(b"outside")
            (base / "icons").mkdir()
            (base / "icons" / "valid.svg").write_text("<svg/>")
            (base / "icons" / "escape.svg").symlink_to(workspace / "outside.png")
            for reference in (
                "../outside.png",
                "/tmp/absolute.png",
                "icons/../..//outside.png",
                "icons/escape.svg",
                "[TODO: icons/valid.svg]",
                "icons",
                "icons/\x00bad.svg",
            ):
                with self.subTest(reference=repr(reference)):
                    self.assertIsNone(
                        MODULE.verified_mirrored_icon_path(
                            reference, base, "./plugins/example/repo"
                        )
                    )
            self.assertEqual(
                MODULE.verified_mirrored_icon_path(
                    "icons/valid.svg", base, "./plugins/example/repo"
                ),
                "./plugins/example/repo/icons/valid.svg",
            )


if __name__ == "__main__":
    unittest.main()
