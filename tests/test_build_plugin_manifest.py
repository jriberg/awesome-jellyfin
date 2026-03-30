import importlib.util
from pathlib import Path
import sys
import unittest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_plugin_manifest.py"
SPEC = importlib.util.spec_from_file_location("build_plugin_manifest", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ReadmeParsingTests(unittest.TestCase):
    def test_extract_repos_only_from_target_sections(self):
        markdown = """
## 🧩 Plugins
- [Plugin A](https://github.com/OwnerA/RepoA)

#### 🏷️ Metadata Providers
- [Meta B](https://github.com/OwnerB/RepoB/tree/main/src) - Works with [External](https://github.com/Other/IgnoreMe)

## 👾 Other
- [Tool C](https://github.com/OwnerC/RepoC)
"""
        repos = MODULE.extract_repos_from_readme(markdown)
        self.assertEqual(repos, ["OwnerA/RepoA", "OwnerB/RepoB"])

    def test_normalize_repo_url_filters_non_github(self):
        self.assertEqual(
            MODULE.normalize_repo_url("https://github.com/foo/bar/"),
            "foo/bar",
        )
        self.assertIsNone(MODULE.normalize_repo_url("https://gitlab.com/foo/bar"))


class CandidateAndManifestTests(unittest.TestCase):
    def test_manifest_candidates_are_prioritized(self):
        tree = [
            {"type": "blob", "path": "src/other.json"},
            {"type": "blob", "path": "nested/manifest.json"},
            {"type": "blob", "path": "manifest.json"},
            {"type": "blob", "path": "docs/plugin-manifest.json"},
        ]
        candidates = MODULE.find_manifest_candidates(tree)
        self.assertEqual(
            candidates,
            ["manifest.json", "nested/manifest.json", "docs/plugin-manifest.json"],
        )

    def test_extract_plugin_records_supports_array_and_object(self):
        manifest_array = [
            {
                "guid": "plugin.guid.1",
                "name": "Plugin One",
                "versions": [{"version": "1.0.0.0"}],
            }
        ]
        manifest_object = {
            "plugins": [
                {
                    "Guid": "plugin.guid.2",
                    "Name": "Plugin Two",
                    "Versions": [{"Version": "2.0.0.0"}],
                }
            ]
        }

        from_array = MODULE.extract_plugin_records(manifest_array)
        from_object = MODULE.extract_plugin_records(manifest_object)

        self.assertEqual(len(from_array), 1)
        self.assertEqual(len(from_object), 1)


class MergeTests(unittest.TestCase):
    def test_merge_prefers_higher_version(self):
        lower = {
            "guid": "plugin.guid.1",
            "name": "Plugin",
            "versions": [{"version": "1.2.0.0"}],
        }
        higher = {
            "guid": "plugin.guid.1",
            "name": "Plugin",
            "versions": [{"version": "1.3.0.0"}],
        }

        merged, sources = MODULE.merge_plugins(
            [
                ("repo/one", lower),
                ("repo/two", higher),
            ]
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(MODULE.max_plugin_version(merged[0]), "1.3.0.0")
        self.assertEqual(sources["plugin.guid.1"], "repo/two")


class CliTests(unittest.TestCase):
    def test_parse_args_insecure_flag(self):
        args = MODULE.parse_args(["--insecure-skip-tls-verify"])
        self.assertTrue(args.insecure_skip_tls_verify)


if __name__ == "__main__":
    unittest.main()
