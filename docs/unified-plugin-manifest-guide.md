# Unified Plugin Manifest Guide (Junior-Friendly)

This guide explains:

- what the script does
- what you need before running it
- what each output file means
- what to do when it fails
- what to ask an agent to do for you

If you are new to this repo, start at **Quick Start**.

## What Problem This Solves

Without automation, you need to:

1. open each plugin repo from `README.md`
2. find that repo's `manifest.json` path manually
3. add repository URLs one by one in Jellyfin

This repo includes a script that does most of that work:

- reads plugin repo links from `README.md`
- tries to find plugin manifests in those repos
- merges all discovered plugin entries into one file: `manifests/manifest.json`
- logs what worked and what failed

## What This Script Does (Step by Step)

Running `python3 scripts/build_plugin_manifest.py` does this:

1. Reads only these sections in `README.md`:
   - `## 🧩 Plugins`
   - `#### 🏷️ Metadata Providers`
2. Extracts the main GitHub repo link from each bullet item.
3. Tries common manifest paths first using `raw.githubusercontent.com`:
   - `manifest.json`
   - `repository/manifest.json`
   - `metadata/stable/manifest.json`
   - `jellyfin-manifest.json`
   - `plugin-manifest.json`
   - `plugins/manifest.json`
4. Validates JSON shape to confirm it actually looks like a Jellyfin plugin manifest.
5. Merges plugins from all valid manifests into one catalog.
6. Handles duplicates by `guid` and keeps the best version.
7. Writes output files under `manifests/`.

## Requirements

Required:

- Python 3.9+ (3.10+ recommended)
- internet access to GitHub
- repository checked out locally

Optional but recommended:

- `GITHUB_TOKEN` environment variable for higher GitHub API limits

Only needed in special environments:

- `--insecure-skip-tls-verify` when your Python runtime has TLS certificate issues

## Quick Start

From repo root:

```bash
python3 scripts/build_plugin_manifest.py
```

This generates:

- `manifests/manifest.json`
- `manifests/manifest-sources.json`
- `manifests/manifest-failures.json`
- `manifests/readme-repos.json`

## Output Files Explained

### `manifests/manifest.json`

The merged plugin catalog. This is the main output for Jellyfin repository usage.

### `manifests/manifest-sources.json`

Audit report for successful discoveries:

- which repos had manifests
- which path was used
- counts and run statistics

Use this when you want to understand where each manifest entry came from.

### `manifests/manifest-failures.json`

Failure report for repos that did not produce a valid manifest.

Use this when you want to improve coverage or fix broken repos.

### `manifests/readme-repos.json`

The exact list of repos extracted from README plugin sections.

Use this to verify the input scope and detect README parsing issues.

## Common Commands

### Normal run

```bash
python3 scripts/build_plugin_manifest.py
```

### Strict mode (fail command if any repo fails)

```bash
python3 scripts/build_plugin_manifest.py --strict
```

### Use token for better API limits

```bash
export GITHUB_TOKEN=your_token_here
python3 scripts/build_plugin_manifest.py --github-token-env GITHUB_TOKEN
```

### Force unauthenticated API fallback

```bash
python3 scripts/build_plugin_manifest.py --allow-unauthenticated-api-fallback
```

Note: this may hit GitHub rate limits quickly.

### If TLS/certificate verification fails in your environment

```bash
python3 scripts/build_plugin_manifest.py --insecure-skip-tls-verify
```

Use this only when necessary.

## How To Read Success vs Failure

When running the script, console logs are:

- `[ok] ...` for successful repo manifest discovery
- `[fail] ...` for failed repos with reason

Even if some repos fail, default behavior is **best effort**:

- command exits successfully
- success + failure are both documented in output files

If you need hard failure, use `--strict`.

## Troubleshooting

### "No valid plugin manifest in common paths"

Meaning: the repo exists, but no expected manifest file path was found.

What to do:

1. check `manifest-failures.json` entry for that repo
2. inspect the repo manually
3. run with token and API fallback if needed

### "API rate limit exceeded" or abuse detection

Meaning: too many GitHub API calls without proper auth.

What to do:

1. set `GITHUB_TOKEN`
2. avoid repeated unauthenticated fallback runs
3. rerun after cooldown if abuse limit was triggered

### TLS / certificate errors

Meaning: local Python certificate chain is not trusted in your environment.

What to do:

1. fix local certificate setup (preferred)
2. temporary workaround: `--insecure-skip-tls-verify`

## Using an Agent: What to Ask

If you use a coding agent (Codex, etc.), here are prompts you can copy.

### 1) Run generation now

```text
In this repo, run the unified manifest generator and summarize:
- number of repos discovered
- number of repos with manifests
- number of failed repos
- number of merged plugins
Then show top 10 failed repos with short reasons.
```

### 2) Regenerate with token and strict mode

```text
Use GITHUB_TOKEN from environment, run the manifest generator in strict mode,
and tell me exactly which repos are still failing.
```

### 3) Improve coverage

```text
Read manifests/manifest-failures.json and update the script so it can find
more real-world manifest paths, then rerun and show before/after stats.
```

### 4) Prepare CI later

```text
Create a GitHub Actions workflow that runs the manifest generator on schedule,
commits updated manifests when changed, and fails on malformed output JSON.
```

## What Must Be True For This To Work

At minimum, all of this is required:

- You run from repo root.
- `README.md` still contains the plugin sections used by the script.
- GitHub repos are reachable from your machine/network.
- Discovered JSON actually contains plugin-like data.

If these are not true, output may be partial or empty.

## Recommended Team Workflow

1. Run script.
2. Check `manifest-sources.json` stats.
3. Check `manifest-failures.json`.
4. Decide if current coverage is acceptable.
5. Commit updated files when appropriate.

This gives a repeatable update process now, and a clean path to full CI automation later.
