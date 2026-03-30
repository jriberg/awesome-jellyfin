#!/usr/bin/env python3
"""Build a unified Jellyfin plugin manifest from README links."""

from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import json
import os
import re
import ssl
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib import error, parse, request

TARGET_SECTIONS = {"plugins", "metadata providers"}
GITHUB_HOSTS = {"github.com", "www.github.com"}
PLUGIN_GUID_KEYS = ("guid", "Guid", "id", "Id")
PLUGIN_NAME_KEYS = ("name", "Name")
PLUGIN_VERSIONS_KEYS = ("versions", "Versions")
PLUGIN_COLLECTION_KEYS = ("plugins", "Plugins", "items", "Items")
COMMON_MANIFEST_PATHS = (
    "manifest.json",
    "repository/manifest.json",
    "metadata/stable/manifest.json",
    "jellyfin-manifest.json",
    "plugin-manifest.json",
    "plugins/manifest.json",
)


@dataclass
class SourceResult:
    repo: str
    default_branch: str
    manifest_path: str
    raw_url: str
    plugin_count: int
    truncated_tree: bool
    discovery_method: str


class GitHubClient:
    def __init__(
        self, token: str | None = None, timeout: int = 20, insecure_tls: bool = False
    ) -> None:
        self._timeout = timeout
        self._ssl_context: ssl.SSLContext | None = None
        if insecure_tls:
            self._ssl_context = ssl._create_unverified_context()
        self._headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "awesome-jellyfin-manifest-builder",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    def _api_get_json(self, url: str) -> Any:
        req = request.Request(url, headers=self._headers)
        try:
            with self._open_url(req) as resp:
                data = resp.read().decode("utf-8")
                return json.loads(data)
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API error {exc.code} for {url}: {body}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Network error for {url}: {exc.reason}") from exc

    def get_default_branch(self, repo: str) -> str:
        data = self._api_get_json(f"https://api.github.com/repos/{repo}")
        default_branch = data.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch:
            raise RuntimeError(f"Missing default branch for {repo}")
        return default_branch

    def get_tree(self, repo: str, branch: str) -> tuple[list[dict[str, Any]], bool]:
        branch_ref = parse.quote(branch, safe="")
        data = self._api_get_json(
            f"https://api.github.com/repos/{repo}/git/trees/{branch_ref}?recursive=1"
        )
        tree = data.get("tree")
        if not isinstance(tree, list):
            raise RuntimeError(f"Missing tree entries for {repo}@{branch}")
        truncated = bool(data.get("truncated", False))
        tree_entries = [entry for entry in tree if isinstance(entry, dict)]
        return tree_entries, truncated

    def get_file_text(self, repo: str, path: str, branch: str) -> str:
        encoded_path = "/".join(parse.quote(part, safe="") for part in path.split("/"))
        url = f"https://api.github.com/repos/{repo}/contents/{encoded_path}?ref={parse.quote(branch, safe='')}"
        data = self._api_get_json(url)

        if isinstance(data, dict):
            content = data.get("content")
            encoding = data.get("encoding")
            if isinstance(content, str) and encoding == "base64":
                decoded = base64.b64decode(content, validate=False)
                return decoded.decode("utf-8", errors="replace")

            download_url = data.get("download_url")
            if isinstance(download_url, str) and download_url:
                return self._raw_get_text(download_url)

        raise RuntimeError(f"Unable to load file content for {repo}:{path}")

    def get_raw_head_file_text(self, repo: str, path: str) -> str:
        encoded_path = "/".join(parse.quote(part, safe="") for part in path.split("/"))
        url = f"https://raw.githubusercontent.com/{repo}/HEAD/{encoded_path}"
        return self._raw_get_text(url)

    def _raw_get_text(self, url: str) -> str:
        req = request.Request(url, headers={"User-Agent": self._headers["User-Agent"]})
        try:
            with self._open_url(req) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Download error {exc.code} for {url}: {body}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Network error for {url}: {exc.reason}") from exc

    def _open_url(self, req: request.Request):
        if self._ssl_context is None:
            return request.urlopen(req, timeout=self._timeout)
        return request.urlopen(req, timeout=self._timeout, context=self._ssl_context)


def normalize_heading(heading: str) -> str:
    text = re.sub(r"`[^`]*`", "", heading)
    text = re.sub(r"\[[^\]]*\]\([^\)]*\)", "", text)
    text = re.sub(r"[^\w\s-]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def extract_repos_from_readme(markdown: str) -> list[str]:
    heading_re = re.compile(r"^(#{2,6})\s+(.*)$")
    primary_list_link_re = re.compile(r"^- \[[^\]]+\]\((https?://[^\s)]+)\)")

    stack: list[tuple[int, str]] = []
    repos: list[str] = []

    for line in markdown.splitlines():
        heading_match = heading_re.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            heading_name = normalize_heading(heading_match.group(2))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading_name))

        active = any(name in TARGET_SECTIONS for _, name in stack)
        if not active:
            continue

        stripped = line.strip()
        primary_match = primary_list_link_re.match(stripped)
        if not primary_match:
            continue

        repo = normalize_repo_url(primary_match.group(1))
        if repo:
            repos.append(repo)

    unique_sorted = sorted(set(repos), key=lambda value: value.lower())
    return unique_sorted


def normalize_repo_url(url: str) -> str | None:
    parsed = parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None

    host = parsed.netloc.lower()
    if host not in GITHUB_HOSTS:
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None

    owner = parts[0]
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]

    allowed = re.compile(r"^[A-Za-z0-9_.-]+$")
    if not allowed.match(owner) or not allowed.match(repo):
        return None

    return f"{owner}/{repo}"


def find_manifest_candidates(tree_entries: Iterable[dict[str, Any]]) -> list[str]:
    candidates: list[tuple[int, int, str]] = []
    for entry in tree_entries:
        if entry.get("type") != "blob":
            continue

        path = entry.get("path")
        if not isinstance(path, str):
            continue

        path_lower = path.lower()
        if not path_lower.endswith(".json"):
            continue

        if "/node_modules/" in path_lower:
            continue

        basename = path_lower.rsplit("/", 1)[-1]
        if basename == "manifest.json":
            priority = 0
        elif "manifest" in basename:
            priority = 1
        elif "jellyfin" in basename and "plugin" in basename:
            priority = 2
        else:
            continue

        depth = path.count("/")
        candidates.append((priority, depth, path))

    candidates.sort(key=lambda item: (item[0], item[1], item[2].lower()))
    return [path for _, _, path in candidates]


def get_field(obj: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in obj:
            return obj[key]
    lower_map = {key.lower(): value for key, value in obj.items()}
    for key in keys:
        lowered = key.lower()
        if lowered in lower_map:
            return lower_map[lowered]
    return None


def extract_plugin_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        candidates = data
    elif isinstance(data, dict):
        collection = get_field(data, PLUGIN_COLLECTION_KEYS)
        if isinstance(collection, list):
            candidates = collection
        else:
            candidates = [data]
    else:
        return []

    plugins: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue

        guid = get_field(item, PLUGIN_GUID_KEYS)
        name = get_field(item, PLUGIN_NAME_KEYS)
        versions = get_field(item, PLUGIN_VERSIONS_KEYS)

        if not isinstance(guid, str) or not guid.strip():
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(versions, list):
            continue

        plugins.append(copy.deepcopy(item))

    return plugins


def compare_versions(a: str, b: str) -> int:
    a_core, a_suffix = split_version(a)
    b_core, b_suffix = split_version(b)

    core_cmp = compare_numeric_tuples(a_core, b_core)
    if core_cmp != 0:
        return core_cmp

    if a_suffix == b_suffix:
        return 0
    if not a_suffix and b_suffix:
        return 1
    if a_suffix and not b_suffix:
        return -1
    return 1 if a_suffix > b_suffix else -1


def split_version(value: str) -> tuple[tuple[int, ...], str]:
    text = value.strip()
    match = re.match(r"^[vV]?([0-9]+(?:\.[0-9]+)*)(.*)$", text)
    if not match:
        return (), text.lower()

    numeric_part = tuple(int(piece) for piece in match.group(1).split("."))
    suffix = match.group(2).strip().lower()

    trimmed = list(numeric_part)
    while trimmed and trimmed[-1] == 0:
        trimmed.pop()
    return tuple(trimmed), suffix


def compare_numeric_tuples(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    max_len = max(len(a), len(b))
    for idx in range(max_len):
        av = a[idx] if idx < len(a) else 0
        bv = b[idx] if idx < len(b) else 0
        if av > bv:
            return 1
        if av < bv:
            return -1
    return 0


def max_plugin_version(plugin: dict[str, Any]) -> str:
    versions = get_field(plugin, PLUGIN_VERSIONS_KEYS)
    if not isinstance(versions, list):
        return ""

    best = ""
    for entry in versions:
        candidate = ""
        if isinstance(entry, str):
            candidate = entry
        elif isinstance(entry, dict):
            raw = get_field(entry, ("version", "Version"))
            if isinstance(raw, str):
                candidate = raw

        if not candidate:
            continue
        if not best or compare_versions(candidate, best) > 0:
            best = candidate

    return best


def plugin_guid(plugin: dict[str, Any]) -> str:
    guid = get_field(plugin, PLUGIN_GUID_KEYS)
    if isinstance(guid, str):
        return guid
    return ""


def plugin_name(plugin: dict[str, Any]) -> str:
    name = get_field(plugin, PLUGIN_NAME_KEYS)
    if isinstance(name, str):
        return name
    return ""


def merge_plugins(discovered: list[tuple[str, dict[str, Any]]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    merged: dict[str, dict[str, Any]] = {}
    source_by_guid: dict[str, str] = {}

    for source_repo, plugin in discovered:
        guid = plugin_guid(plugin).strip()
        if not guid:
            continue

        key = guid.lower()
        existing = merged.get(key)
        if existing is None:
            merged[key] = plugin
            source_by_guid[key] = source_repo
            continue

        current_best = max_plugin_version(existing)
        candidate_best = max_plugin_version(plugin)
        cmp_result = compare_versions(candidate_best, current_best)

        replace = False
        if cmp_result > 0:
            replace = True
        elif cmp_result == 0:
            current_versions = get_field(existing, PLUGIN_VERSIONS_KEYS)
            candidate_versions = get_field(plugin, PLUGIN_VERSIONS_KEYS)
            current_len = len(current_versions) if isinstance(current_versions, list) else 0
            candidate_len = len(candidate_versions) if isinstance(candidate_versions, list) else 0
            if candidate_len > current_len:
                replace = True

        if replace:
            merged[key] = plugin
            source_by_guid[key] = source_repo

    merged_list = sorted(merged.values(), key=lambda item: plugin_name(item).lower())
    return merged_list, source_by_guid


def load_plugins_from_manifest_text(path: str, text: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], f"{path}: invalid JSON ({exc})"

    plugins = extract_plugin_records(data)
    if not plugins:
        return [], f"{path}: JSON loaded but no plugin records"
    return plugins, None


def discover_manifest_for_repo(
    client: GitHubClient, repo: str, allow_api_fallback: bool
) -> tuple[SourceResult, list[dict[str, Any]]]:
    candidate_errors: list[str] = []

    for candidate_path in COMMON_MANIFEST_PATHS:
        try:
            text = client.get_raw_head_file_text(repo, candidate_path)
            plugins, manifest_error = load_plugins_from_manifest_text(candidate_path, text)
            if manifest_error:
                candidate_errors.append(manifest_error)
                continue

            source = SourceResult(
                repo=repo,
                default_branch="HEAD",
                manifest_path=candidate_path,
                raw_url=f"https://raw.githubusercontent.com/{repo}/HEAD/{candidate_path}",
                plugin_count=len(plugins),
                truncated_tree=False,
                discovery_method="raw-head",
            )
            return source, plugins
        except Exception as exc:  # noqa: BLE001
            candidate_errors.append(f"{candidate_path}: {exc}")

    if not allow_api_fallback:
        joined_errors = " | ".join(candidate_errors)
        raise RuntimeError(f"No valid plugin manifest in common paths. Tried: {joined_errors}")

    default_branch = client.get_default_branch(repo)
    tree_entries, truncated_tree = client.get_tree(repo, default_branch)
    candidates = find_manifest_candidates(tree_entries)

    if not candidates:
        raise RuntimeError("No manifest-like JSON files found in repository tree")

    for candidate_path in candidates:
        try:
            text = client.get_file_text(repo, candidate_path, default_branch)
            plugins, manifest_error = load_plugins_from_manifest_text(candidate_path, text)
            if manifest_error:
                candidate_errors.append(manifest_error)
                continue

            source = SourceResult(
                repo=repo,
                default_branch=default_branch,
                manifest_path=candidate_path,
                raw_url=f"https://raw.githubusercontent.com/{repo}/{default_branch}/{candidate_path}",
                plugin_count=len(plugins),
                truncated_tree=truncated_tree,
                discovery_method="github-api-tree",
            )
            return source, plugins
        except Exception as exc:  # noqa: BLE001
            candidate_errors.append(f"{candidate_path}: {exc}")

    joined_errors = " | ".join(candidate_errors)
    raise RuntimeError(f"No valid plugin manifest found. Tried: {joined_errors}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readme", default="README.md", help="Path to README markdown file")
    parser.add_argument(
        "--out",
        default="manifests/manifest.json",
        help="Output path for merged plugin manifest",
    )
    parser.add_argument(
        "--sources-out",
        default="manifests/manifest-sources.json",
        help="Output path for source report",
    )
    parser.add_argument(
        "--failures-out",
        default="manifests/manifest-failures.json",
        help="Output path for failure report",
    )
    parser.add_argument(
        "--repos-out",
        default="manifests/readme-repos.json",
        help="Output path for extracted repository links from README sections",
    )
    parser.add_argument(
        "--github-token-env",
        default="GITHUB_TOKEN",
        help="Environment variable name for optional GitHub token",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any repo fails manifest discovery",
    )
    parser.add_argument(
        "--allow-unauthenticated-api-fallback",
        action="store_true",
        help="Allow GitHub API fallback without token after common raw path lookup",
    )
    parser.add_argument(
        "--insecure-skip-tls-verify",
        action="store_true",
        help="Skip TLS certificate verification for HTTPS requests (not recommended)",
    )
    return parser.parse_args(argv)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    readme_path = Path(args.readme)
    if not readme_path.is_file():
        print(f"README file not found: {readme_path}", file=sys.stderr)
        return 1

    readme_text = readme_path.read_text(encoding="utf-8")
    repos = extract_repos_from_readme(readme_text)
    if not repos:
        print("No GitHub repositories found in target README sections", file=sys.stderr)
        return 1

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    token = None
    token_env_name = args.github_token_env
    if token_env_name:
        token = os.environ.get(token_env_name)

    client = GitHubClient(token=token, insecure_tls=args.insecure_skip_tls_verify)
    allow_api_fallback = bool(token) or bool(args.allow_unauthenticated_api_fallback)

    discovered_sources: list[SourceResult] = []
    failures: list[dict[str, str]] = []
    plugin_records: list[tuple[str, dict[str, Any]]] = []

    for repo in repos:
        try:
            source, plugins = discover_manifest_for_repo(client, repo, allow_api_fallback)
            discovered_sources.append(source)
            for plugin in plugins:
                plugin_records.append((repo, plugin))
            print(
                f"[ok] {repo}: {source.manifest_path} ({len(plugins)} plugins, {source.discovery_method})"
            )
        except Exception as exc:  # noqa: BLE001
            failures.append({"repo": repo, "error": str(exc)})
            print(f"[fail] {repo}: {exc}", file=sys.stderr)

    merged_plugins, selected_sources = merge_plugins(plugin_records)

    repos_payload = {
        "generatedAtUtc": now,
        "readmePath": str(readme_path),
        "sections": sorted(TARGET_SECTIONS),
        "repos": repos,
    }

    source_payload = {
        "generatedAtUtc": now,
        "readmePath": str(readme_path),
        "sections": sorted(TARGET_SECTIONS),
        "apiFallbackEnabled": allow_api_fallback,
        "stats": {
            "reposDiscovered": len(repos),
            "reposWithManifest": len(discovered_sources),
            "reposFailed": len(failures),
            "pluginRecordsBeforeMerge": len(plugin_records),
            "pluginsAfterMerge": len(merged_plugins),
        },
        "sources": [
            {
                "repo": source.repo,
                "defaultBranch": source.default_branch,
                "manifestPath": source.manifest_path,
                "rawUrl": source.raw_url,
                "pluginCount": source.plugin_count,
                "truncatedTree": source.truncated_tree,
                "discoveryMethod": source.discovery_method,
            }
            for source in sorted(discovered_sources, key=lambda item: item.repo.lower())
        ],
        "selectedSourceByGuid": selected_sources,
    }

    failure_payload = {
        "generatedAtUtc": now,
        "stats": {
            "reposDiscovered": len(repos),
            "reposFailed": len(failures),
        },
        "failures": sorted(failures, key=lambda item: item["repo"].lower()),
    }

    write_json(Path(args.out), merged_plugins)
    write_json(Path(args.sources_out), source_payload)
    write_json(Path(args.failures_out), failure_payload)
    write_json(Path(args.repos_out), repos_payload)

    if args.strict and failures:
        print(
            f"Strict mode enabled and {len(failures)} repositories failed manifest discovery",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
