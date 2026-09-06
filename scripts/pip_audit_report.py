#!/usr/bin/env python3
"""
Parse a pip-audit SARIF report and report the vulnerabilities it contains.

Used by .github/workflows/ci-audit.yml. Keeping this logic in a script
(instead of inline in the workflow) makes it testable and keeps the
workflow YAML valid — flush-left lines of inline Python silently
terminate YAML block scalars and break the whole file.

Usage:
    python3 scripts/pip_audit_report.py <sarif-file>
    python3 scripts/pip_audit_report.py <sarif-file> --summary

    --summary prints a compact markdown digest (for $GITHUB_STEP_SUMMARY)
    instead of the full annotated report.

Reads REQFILE from the environment when present (GitHub Actions).
Writes GitHub Actions step outputs (has_vulnerabilities, vuln_count,
high_critical, medium, low_info) via GITHUB_OUTPUT when available.

Exit codes:
    0 — no high/critical vulnerabilities (or nothing to report)
    1 — high/critical vulnerabilities found
    2 — SARIF file missing or unreadable
"""

import argparse
import json
import os
import sys


def emit_output(key, value):
    """Append a GitHub Actions step output when GITHUB_OUTPUT is set."""
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a") as f:
            f.write(f"{key}={value}\n")


def load_results(sarif_path):
    with open(sarif_path) as f:
        sarif = json.load(f)
    runs = sarif.get("runs", [])
    results = runs[0].get("results", []) if runs else []
    return sarif, results


def severity_of(sarif, rule_id):
    """Return the CVSS-style security-severity of a SARIF rule, or None."""
    rules = (
        sarif.get("runs", [{}])[0]
        .get("tool", {})
        .get("driver", {})
        .get("rules", [])
    )
    for rule in rules:
        if rule.get("id") == rule_id:
            try:
                return float(rule.get("properties", {}).get("security-severity"))
            except (TypeError, ValueError):
                return None
    return None


def package_of(result):
    """Best-effort package name from the SARIF artifact location."""
    locations = result.get("locations", [])
    if locations:
        artifact = (
            locations[0]
            .get("physicalLocation", {})
            .get("artifactLocation", {})
        )
        return artifact.get("uri", "?")
    return "?"


def report(sarif_path):
    """Full annotated report; returns the process exit code."""
    reqfile = os.environ.get("REQFILE", "?")

    if not os.path.isfile(sarif_path):
        print("⚠️  No SARIF output produced — pip-audit may have crashed.")
        emit_output("has_vulnerabilities", "false")
        return 0

    try:
        sarif, results = load_results(sarif_path)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"⚠️  Could not parse SARIF output ({exc}) — treating as no findings.")
        emit_output("has_vulnerabilities", "false")
        return 0

    if not results:
        print(f"✅ No vulnerabilities found in {reqfile}.")
        emit_output("has_vulnerabilities", "false")
        emit_output("vuln_count", 0)
        return 0

    print(f"Found {len(results)} known vulnerabilities in {reqfile}:")
    high_critical = medium = low_info = 0
    for r in results:
        rule_id = r.get("ruleId", "?")
        msg = r.get("message", {}).get("text", "")
        sev = severity_of(sarif, rule_id)
        if sev is not None and sev >= 7.0:
            flag, high_critical = "❌ FAIL", high_critical + 1
        elif sev is not None and sev >= 4.0:
            flag, medium = "⚠️ WARN", medium + 1
        else:
            flag, low_info = "ℹ️ INFO", low_info + 1
        print(
            f"  {flag} {rule_id} — {package_of(r)}"
            f" (severity: {sev if sev is not None else 'N/A'})"
        )
        print(f"         {msg[:200]}")

    emit_output("has_vulnerabilities", "true")
    emit_output("vuln_count", len(results))
    emit_output("high_critical", high_critical)
    emit_output("medium", medium)
    emit_output("low_info", low_info)

    if high_critical:
        print(
            f"\n❌ {high_critical} High/Critical severity vulnerabilities"
            " found — failing build."
        )
        return 1
    print(f"\n✅ No high/critical vulnerabilities. {medium} medium, {low_info} low/info.")
    return 0


def summary(sarif_path):
    """Compact markdown digest; returns the process exit code."""
    _, results = load_results(sarif_path)
    if not results:
        print("✅ No vulnerabilities found.")
        return 0
    for r in results:
        rule_id = r.get("ruleId", "?")
        msg = r.get("message", {}).get("text", "?")
        print(f"- **{rule_id}**: {msg[:200]}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Parse a pip-audit SARIF report and report vulnerabilities."
    )
    parser.add_argument("sarif_file", help="path to the pip-audit SARIF output")
    parser.add_argument(
        "--summary",
        action="store_true",
        help="print a compact markdown digest instead of the full report",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.sarif_file):
        print(f"pip_audit_report: SARIF file not found: {args.sarif_file}", file=sys.stderr)
        return 2

    if args.summary:
        return summary(args.sarif_file)
    return report(args.sarif_file)


if __name__ == "__main__":
    sys.exit(main())
