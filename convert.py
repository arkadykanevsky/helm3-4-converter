#!/usr/bin/env python3
"""
Helm chart migration helper: Helm 3 (chart apiVersion v1/v2) -> Helm 4 ready.

Important: Helm 4 installs existing apiVersion v2 charts unchanged. This tool:
  - Audits charts for Helm 4 compatibility issues
  - Migrates legacy apiVersion v1 charts to v2 (requirements.yaml -> Chart.yaml)
  - Optionally prepares experimental chart apiVersion v3 (Helm 4 + HELM_EXPERIMENTAL_CHART_V3)

Usage:
  python convert.py audit /path/to/chart
  python convert.py convert /path/to/chart [--target v2|v3] [--dry-run] [--in-place]
  python convert.py convert /path/to/chart --output /path/to/output-chart
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import semver
import yaml

CHART_YAML = "Chart.yaml"
REQUIREMENTS_YAML = "requirements.yaml"
REQUIREMENTS_LOCK = "requirements.lock"
TEMPLATES_DIR = "templates"

# Helm v3 chart API: `---` immediately followed by `{{-` relied on Helm's workaround.
V3_TEMPLATE_PATTERN = re.compile(r"(---\s*\n)(\s*)(\{\{-)", re.MULTILINE)


@dataclass
class Finding:
    level: str  # info | warn | error
    message: str
    path: str | None = None


@dataclass
class ChartContext:
    root: Path
    chart_yaml_path: Path
    metadata: dict[str, Any]
    findings: list[Finding] = field(default_factory=list)

    @property
    def api_version(self) -> str:
        return str(self.metadata.get("apiVersion", "")).strip()

    def add(self, level: str, message: str, path: str | None = None) -> None:
        self.findings.append(Finding(level, message, path))


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def dump_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)


def discover_charts(root: Path) -> list[Path]:
    charts = []
    chart_yaml = root / CHART_YAML
    if chart_yaml.is_file():
        charts.append(root)
    charts_dir = root / "charts"
    if charts_dir.is_dir():
        for child in sorted(charts_dir.iterdir()):
            if (child / CHART_YAML).is_file():
                charts.append(child)
    return charts


def strict_semver_ok(value: str) -> bool:
    try:
        semver.Version.parse(str(value))
        return True
    except ValueError:
        return False


def merge_requirements_into_chart(metadata: dict[str, Any], requirements_path: Path) -> dict[str, Any]:
    requirements = load_yaml(requirements_path)
    deps = requirements.get("dependencies") or []
    if not isinstance(deps, list):
        raise ValueError(f"{requirements_path}: dependencies must be a list")

    updated = deepcopy(metadata)
    existing = updated.get("dependencies") or []
    if existing and deps:
        raise ValueError(
            f"{requirements_path}: Chart.yaml already has dependencies; "
            "resolve manually before converting v1 chart"
        )
    if deps:
        updated["dependencies"] = deps
    return updated


def fix_v3_template_content(content: str) -> tuple[str, int]:
    """Replace `---` + `{{-` glue pattern Helm v3 chart API will no longer fix."""

    def repl(match: re.Match[str]) -> str:
        sep, ws, _open_tpl = match.groups()
        # Replace `{{-` with `{{` so YAML doc separators are not glued to trimmed templates.
        return sep + ws + "{{"

    new_content, count = V3_TEMPLATE_PATTERN.subn(repl, content)
    return new_content, count


def audit_chart(ctx: ChartContext, target: str) -> None:
    api = ctx.api_version
    if not api:
        ctx.add("error", "Chart.yaml missing apiVersion", str(ctx.chart_yaml_path))
        return

    if api not in {"v1", "v2", "v3"}:
        ctx.add("warn", f"Unknown chart apiVersion '{api}'", str(ctx.chart_yaml_path))

    if api == "v1":
        ctx.add(
            "warn",
            "apiVersion v1 is legacy; convert to v2 for Helm 3/4 best practice",
            str(ctx.chart_yaml_path),
        )
        req = ctx.root / REQUIREMENTS_YAML
        if not req.is_file():
            ctx.add(
                "info",
                "No requirements.yaml; v1 chart may use charts/ subdirs only",
                str(ctx.root),
            )

    if api == "v2":
        ctx.add(
            "info",
            "apiVersion v2 charts work on Helm 4 without structural changes",
            str(ctx.chart_yaml_path),
        )

    if api == "v3" and target == "v2":
        ctx.add("warn", "Chart already apiVersion v3; target v2 downgrade not supported")

    version = ctx.metadata.get("version")
    if version is not None and not strict_semver_ok(str(version)):
        msg = f"chart version '{version}' is not strict SemVer 2"
        if target == "v3" or api == "v3":
            ctx.add("error", msg + " (required for chart apiVersion v3)", str(ctx.chart_yaml_path))
        else:
            ctx.add("warn", msg + " (Helm 4 chart v3 will require strict SemVer)", str(ctx.chart_yaml_path))

    for dep in ctx.metadata.get("dependencies") or []:
        dep_version = dep.get("version")
        if dep_version is not None and not strict_semver_ok(str(dep_version)):
            dep_name = dep.get("name", "<unknown>")
            ctx.add(
                "warn",
                f"dependency '{dep_name}' version '{dep_version}' is not strict SemVer 2",
                str(ctx.chart_yaml_path),
            )

    req_path = ctx.root / REQUIREMENTS_YAML
    lock_path = ctx.root / REQUIREMENTS_LOCK
    if api in {"v2", "v3"} and req_path.is_file():
        ctx.add(
            "warn",
            f"{REQUIREMENTS_YAML} should not exist for apiVersion {api}; "
            "dependencies belong in Chart.yaml",
            str(req_path),
        )
    if lock_path.is_file() and api in {"v2", "v3"}:
        ctx.add("warn", f"{REQUIREMENTS_LOCK} is obsolete for apiVersion {api}", str(lock_path))

    templates = ctx.root / TEMPLATES_DIR
    if templates.is_dir():
        for tpl in sorted(templates.rglob("*")):
            if not tpl.is_file():
                continue
            text = tpl.read_text(encoding="utf-8")
            if V3_TEMPLATE_PATTERN.search(text):
                level = "error" if target == "v3" or api == "v3" else "warn"
                ctx.add(
                    level,
                    "Template uses `---` immediately followed by `{{-`; "
                    "chart apiVersion v3 requires fixing this pattern",
                    str(tpl.relative_to(ctx.root)),
                )


def convert_chart_metadata(ctx: ChartContext, target: str) -> dict[str, Any]:
    metadata = deepcopy(ctx.metadata)
    api = ctx.api_version
    requirements_path = ctx.root / REQUIREMENTS_YAML

    if target == "v2":
        if api == "v1":
            if requirements_path.is_file():
                metadata = merge_requirements_into_chart(metadata, requirements_path)
            metadata["apiVersion"] = "v2"
            if "type" not in metadata:
                metadata["type"] = "application"
        elif api == "v2":
            metadata = deepcopy(ctx.metadata)
        elif api == "v3":
            raise ValueError("Cannot downgrade chart apiVersion v3 to v2")
        else:
            raise ValueError(f"Unsupported source apiVersion '{api}'")

    elif target == "v3":
        if api == "v1":
            if requirements_path.is_file():
                metadata = merge_requirements_into_chart(metadata, requirements_path)
        elif api not in {"v1", "v2", "v3"}:
            raise ValueError(f"Unsupported source apiVersion '{api}'")

        metadata["apiVersion"] = "v3"
        if "type" not in metadata:
            metadata["type"] = "application"

        version = metadata.get("version")
        if version is None or not strict_semver_ok(str(version)):
            raise ValueError(
                f"chart version '{version}' must be strict SemVer 2 for apiVersion v3"
            )
        for dep in metadata.get("dependencies") or []:
            dep_version = dep.get("version")
            dep_name = dep.get("name", "<unknown>")
            if dep_version is None or not strict_semver_ok(str(dep_version)):
                raise ValueError(
                    f"dependency '{dep_name}' version '{dep_version}' "
                    "must be strict SemVer 2 for apiVersion v3"
                )
    else:
        raise ValueError(f"Unknown target apiVersion '{target}'")

    return metadata


def apply_template_fixes(chart_root: Path, dry_run: bool) -> list[str]:
    changed: list[str] = []
    templates = chart_root / TEMPLATES_DIR
    if not templates.is_dir():
        return changed

    for tpl in sorted(templates.rglob("*")):
        if not tpl.is_file():
            continue
        original = tpl.read_text(encoding="utf-8")
        updated, count = fix_v3_template_content(original)
        if count:
            rel = str(tpl.relative_to(chart_root))
            changed.append(f"{rel} ({count} pattern(s) fixed)")
            if not dry_run:
                tpl.write_text(updated, encoding="utf-8")
    return changed


def remove_obsolete_files(chart_root: Path, dry_run: bool) -> list[str]:
    removed: list[str] = []
    for name in (REQUIREMENTS_YAML, REQUIREMENTS_LOCK):
        path = chart_root / name
        if path.is_file():
            removed.append(name)
            if not dry_run:
                path.unlink()
    return removed


def load_context(chart_root: Path) -> ChartContext:
    chart_yaml = chart_root / CHART_YAML
    if not chart_yaml.is_file():
        raise FileNotFoundError(f"No {CHART_YAML} in {chart_root}")
    metadata = load_yaml(chart_yaml)
    if not isinstance(metadata, dict):
        raise ValueError(f"{chart_yaml} must contain a YAML mapping")
    return ChartContext(chart_root, chart_yaml, metadata)


def print_findings(chart_root: Path, findings: list[Finding]) -> None:
    if not findings:
        print(f"  {chart_root}: no findings")
        return
    print(f"  {chart_root}:")
    for f in findings:
        loc = f" [{f.path}]" if f.path else ""
        print(f"    {f.level.upper():5}{loc}: {f.message}")


def cmd_audit(chart_path: Path, target: str) -> int:
    exit_code = 0
    for chart_root in discover_charts(chart_path):
        ctx = load_context(chart_root)
        audit_chart(ctx, target)
        print_findings(chart_root, ctx.findings)
        if any(f.level == "error" for f in ctx.findings):
            exit_code = 1
        elif any(f.level == "warn" for f in ctx.findings) and exit_code == 0:
            exit_code = 0
    return exit_code


def cmd_convert(
    chart_path: Path,
    target: str,
    output: Path | None,
    in_place: bool,
    dry_run: bool,
    fix_templates: bool,
) -> int:
    if in_place and output is not None:
        print("error: use either --in-place or --output, not both", file=sys.stderr)
        return 2
    if not in_place and output is None and not dry_run:
        print("error: specify --in-place or --output (or use --dry-run)", file=sys.stderr)
        return 2

    src_root = chart_path
    if output is not None and not dry_run:
        if output.exists():
            print(f"error: output path already exists: {output}", file=sys.stderr)
            return 2
        shutil.copytree(src_root, output)
        dst_root = output
    else:
        dst_root = src_root

    print(f"Target chart apiVersion: {target}")
    if dry_run:
        print("DRY RUN — no files will be modified")

    had_errors = False
    for read_root in discover_charts(src_root):
        rel = read_root.relative_to(src_root)
        label = "." if str(rel) == "." else str(rel)
        write_root = dst_root / rel
        print(f"\n=== Chart: {label} ===")

        ctx = load_context(read_root)
        audit_chart(ctx, target)

        try:
            new_metadata = convert_chart_metadata(ctx, target)
        except ValueError as exc:
            print(f"  ERROR: {exc}")
            had_errors = True
            continue

        src_api = ctx.api_version
        dst_api = new_metadata.get("apiVersion")
        print(f"  Chart.yaml: apiVersion {src_api} -> {dst_api}")

        should_remove_req = src_api == "v1" or (
            target == "v3" and (read_root / REQUIREMENTS_YAML).is_file()
        )
        if should_remove_req:
            removed = remove_obsolete_files(read_root, dry_run=True)
            if removed:
                print(f"  Remove: {', '.join(removed)}")
                if not dry_run:
                    remove_obsolete_files(write_root, dry_run=False)

        template_changes: list[str] = []
        if fix_templates and target == "v3":
            template_changes = apply_template_fixes(read_root, dry_run=True)
            if template_changes:
                print("  Template fixes:")
                for line in template_changes:
                    print(f"    - {line}")
                if not dry_run:
                    apply_template_fixes(write_root, dry_run=False)

        if not dry_run:
            (write_root / CHART_YAML).write_text(dump_yaml(new_metadata), encoding="utf-8")

        post_ctx = ChartContext(write_root if not dry_run else read_root, read_root / CHART_YAML, new_metadata)
        audit_chart(post_ctx, target)
        print_findings(read_root, post_ctx.findings)

    if target == "v3":
        print(
            "\nNote: chart apiVersion v3 is experimental in Helm 4. "
            "Enable with HELM_EXPERIMENTAL_CHART_V3=1 and use a Helm 4.x client."
        )

    print(
        "\nReminder: Helm 4 breaking changes are mostly CLI/plugins (post-renderers, flags), "
        "not chart structure. Test with `helm lint` and `helm template` under Helm 4."
    )
    return 1 if had_errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit and convert Helm charts for Helm 4 compatibility",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    audit_p = sub.add_parser("audit", help="Audit chart(s) without modifying files")
    audit_p.add_argument("chart", type=Path, help="Path to chart directory")
    audit_p.add_argument(
        "--target",
        choices=["v2", "v3"],
        default="v2",
        help="Assess against target chart apiVersion (default: v2)",
    )

    convert_p = sub.add_parser("convert", help="Convert chart(s) toward Helm 4 readiness")
    convert_p.add_argument("chart", type=Path, help="Path to chart directory")
    convert_p.add_argument(
        "--target",
        choices=["v2", "v3"],
        default="v2",
        help="Target chart apiVersion (default: v2 = Helm 3/4 standard)",
    )
    convert_p.add_argument(
        "--output",
        type=Path,
        help="Write converted chart to a new directory",
    )
    convert_p.add_argument(
        "--in-place",
        action="store_true",
        help="Modify the chart directory in place",
    )
    convert_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned changes without writing files",
    )
    convert_p.add_argument(
        "--fix-templates",
        action="store_true",
        default=True,
        help="Fix ---/{{- template patterns when targeting v3 (default: on)",
    )
    convert_p.add_argument(
        "--no-fix-templates",
        action="store_false",
        dest="fix_templates",
        help="Skip template pattern fixes",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    chart = args.chart.resolve()

    if not chart.is_dir():
        print(f"error: chart path is not a directory: {chart}", file=sys.stderr)
        return 2
    if not (chart / CHART_YAML).is_file():
        print(f"error: no {CHART_YAML} in {chart}", file=sys.stderr)
        return 2

    if args.command == "audit":
        return cmd_audit(chart, args.target)
    if args.command == "convert":
        return cmd_convert(
            chart,
            target=args.target,
            output=args.output,
            in_place=args.in_place,
            dry_run=args.dry_run,
            fix_templates=args.fix_templates,
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
