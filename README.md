# helm-chart-convert

Audit and convert Helm charts for Helm 4 compatibility.

Most Helm 3 charts (`apiVersion: v2`) work on Helm 4 without changes. This tool helps with:

- Legacy `apiVersion: v1` → `v2` migration (`requirements.yaml` → `Chart.yaml`)
- Optional experimental `apiVersion: v3` preparation for Helm 4
- Compatibility audits (SemVer, template patterns, obsolete files)

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Usage

```bash
# Audit without modifying files
.venv/bin/python convert.py audit /path/to/chart
.venv/bin/python convert.py audit /path/to/chart --target v3

# Convert to a new directory
.venv/bin/python convert.py convert /path/to/chart --target v2 --output /path/to/chart-v2
.venv/bin/python convert.py convert /path/to/chart --target v3 --output /path/to/chart-v3

# Preview or apply in place
.venv/bin/python convert.py convert /path/to/chart --target v2 --dry-run
.venv/bin/python convert.py convert /path/to/chart --target v2 --in-place
```

## Targets

| Target | Purpose |
|--------|---------|
| `v2` | Standard Helm 3/4 chart format (default) |
| `v3` | Experimental chart API for Helm 4 (`HELM_EXPERIMENTAL_CHART_V3=1`) |

## Example

```bash
.venv/bin/python convert.py convert examples/sample-v1-chart --target v2 --output /tmp/chart-v2
.venv/bin/python convert.py convert /tmp/chart-v2 --target v3 --in-place
```

## Publish to GitHub

```bash
# One-time: authenticate (browser or token)
gh auth login
# or: export GH_TOKEN=<personal-access-token>

# Create public repo and push
./scripts/publish-to-github.sh
```

Optional environment variables: `REPO_NAME` (default: `helm-chart-convert`), `VISIBILITY` (`public` or `private`).

## Notes

Helm 4 breaking changes are mostly CLI and plugin related (post-renderers, renamed flags), not chart structure. Always validate with `helm lint` and `helm template` under Helm 4.
