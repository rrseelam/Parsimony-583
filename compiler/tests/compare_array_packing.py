#!/usr/bin/env python3

import argparse
import csv
import json
import math
import os
from pathlib import Path
import re
import shlex
import statistics
import subprocess
import sys
from typing import Dict, List


AVG_RE = re.compile(r"array_packing_perf avg_us:\s*([0-9]+(?:\.[0-9]+)?)")
TOTAL_RE = re.compile(r"array_packing_perf total_us:\s*([0-9]+(?:\.[0-9]+)?)")
CHECKSUM_RE = re.compile(r"array_packing_perf checksum:\s*([0-9]+)")
SCATTER_GATHER_RE = re.compile(r"scatter/gather emitted")


def run_command(cmd: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def require_ok(result: subprocess.CompletedProcess, context: str) -> str:
    combined = result.stdout + result.stderr
    if result.returncode != 0:
        sys.stderr.write(f"{context} failed with exit code {result.returncode}\n")
        sys.stderr.write(combined)
        raise SystemExit(result.returncode)
    return combined


def parse_metrics(output: str) -> Dict[str, float]:
    avg_match = AVG_RE.search(output)
    total_match = TOTAL_RE.search(output)
    checksum_match = CHECKSUM_RE.search(output)
    if not (avg_match and total_match and checksum_match):
        raise ValueError("Could not parse benchmark metrics from output:\n" + output)
    return {
        "avg_us": float(avg_match.group(1)),
        "total_us": float(total_match.group(1)),
        "checksum": int(checksum_match.group(1)),
    }


def format_us(value: float) -> str:
    return f"{value:.4f} us"


def percentile(values: List[float], p: float) -> float:
    if not values:
        raise ValueError("percentile() requires at least one value")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * p
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return ordered[lower]
    weight = pos - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def compile_variant(
    *,
    variant_name: str,
    source: Path,
    tests_dir: Path,
    parsimony_cmd: str,
    include_dir: Path,
    bin_dir: Path,
    tmp_root: Path,
    extra_psv_args: str,
    extra_compile_args: List[str],
) -> Dict[str, object]:
    out_bin = bin_dir / f"{source.stem}_{variant_name}"
    tmp_dir = tmp_root / variant_name
    tmp_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        parsimony_cmd,
        "-O3",
        "-march=native",
        "-mprefer-vector-width=512",
        f"-I{include_dir}",
        str(source.name),
        "-o",
        str(out_bin),
        f"--Xpsv={extra_psv_args}",
        f"--Xtmp={tmp_dir}",
    ] + extra_compile_args

    result = run_command(cmd, tests_dir)
    combined = require_ok(result, f"compile ({variant_name})")
    return {
        "binary": out_bin,
        "compile_output": combined,
        "scatter_gather_warnings": len(SCATTER_GATHER_RE.findall(combined)),
        "command": cmd,
    }


def run_variant(
    *,
    variant_name: str,
    binary: Path,
    tests_dir: Path,
    warmup_runs: int,
    measured_runs: int,
) -> Dict[str, object]:
    warmups: List[Dict[str, float]] = []
    runs: List[Dict[str, float]] = []

    for _ in range(warmup_runs):
        result = run_command([str(binary)], tests_dir)
        combined = require_ok(result, f"warmup run ({variant_name})")
        warmups.append(parse_metrics(combined))

    for idx in range(measured_runs):
        result = run_command([str(binary)], tests_dir)
        combined = require_ok(result, f"measured run {idx + 1} ({variant_name})")
        metrics = parse_metrics(combined)
        metrics["run_index"] = idx + 1
        runs.append(metrics)

    checksums = {int(run["checksum"]) for run in runs}
    if len(checksums) != 1:
        raise ValueError(
            f"Checksum mismatch across measured runs for {variant_name}: {sorted(checksums)}"
        )

    avg_values = [float(run["avg_us"]) for run in runs]
    summary = {
        "variant": variant_name,
        "warmup_runs": warmups,
        "runs": runs,
        "checksum": int(runs[0]["checksum"]),
        "median_avg_us": statistics.median(avg_values),
        "mean_avg_us": statistics.mean(avg_values),
        "min_avg_us": min(avg_values),
        "max_avg_us": max(avg_values),
        "p10_avg_us": percentile(avg_values, 0.10),
        "p90_avg_us": percentile(avg_values, 0.90),
        "stdev_avg_us": statistics.stdev(avg_values) if len(avg_values) > 1 else 0.0,
    }
    return summary


def write_csv(output_path: Path, baseline: Dict[str, object], packed: Dict[str, object]) -> None:
    with output_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["variant", "run_index", "avg_us", "total_us", "checksum"])
        for variant in (baseline, packed):
            for run in variant["runs"]:
                writer.writerow(
                    [
                        variant["variant"],
                        run["run_index"],
                        f"{run['avg_us']:.4f}",
                        f"{run['total_us']:.2f}",
                        run["checksum"],
                    ]
                )


def svg_text(x: float, y: float, text: str, size: int = 14, anchor: str = "middle") -> str:
    safe = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
        f'font-family="Arial, sans-serif" text-anchor="{anchor}">{safe}</text>'
    )


def generate_svg(
    output_path: Path,
    baseline: Dict[str, object],
    packed: Dict[str, object],
    improvement_pct: float,
    speedup: float,
) -> None:
    width = 1100
    height = 620
    margin_left = 90
    margin_right = 40
    margin_top = 80
    margin_bottom = 100
    chart_width = width - margin_left - margin_right
    chart_height = height - margin_top - margin_bottom

    baseline_runs = [float(run["avg_us"]) for run in baseline["runs"]]
    packed_runs = [float(run["avg_us"]) for run in packed["runs"]]
    y_max = max(baseline_runs + packed_runs) * 1.15
    y_max = max(y_max, 1.0)

    group_count = len(baseline_runs)
    group_width = chart_width / max(group_count, 1)
    bar_width = min(34.0, group_width * 0.28)

    def y_to_px(value: float) -> float:
        return margin_top + chart_height - (value / y_max) * chart_height

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        svg_text(width / 2, 34, "Array Packing Performance Comparison", 24),
        svg_text(
            width / 2,
            58,
            "Lower avg_us is better. Bars show repeated runs of the same benchmark.",
            14,
        ),
    ]

    for tick_idx in range(6):
        tick_value = y_max * tick_idx / 5.0
        y = y_to_px(tick_value)
        parts.append(
            f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}" '
            'stroke="#dddddd" stroke-width="1"/>'
        )
        parts.append(svg_text(margin_left - 12, y + 5, f"{tick_value:.0f}", 12, "end"))

    parts.append(
        f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + chart_height}" '
        'stroke="#333333" stroke-width="2"/>'
    )
    parts.append(
        f'<line x1="{margin_left}" y1="{margin_top + chart_height}" x2="{width - margin_right}" '
        f'y2="{margin_top + chart_height}" stroke="#333333" stroke-width="2"/>'
    )

    for idx, (base_val, packed_val) in enumerate(zip(baseline_runs, packed_runs), start=1):
        center_x = margin_left + (idx - 0.5) * group_width
        base_x = center_x - bar_width - 5
        packed_x = center_x + 5

        base_y = y_to_px(base_val)
        packed_y = y_to_px(packed_val)

        parts.append(
            f'<rect x="{base_x:.1f}" y="{base_y:.1f}" width="{bar_width:.1f}" '
            f'height="{margin_top + chart_height - base_y:.1f}" fill="#9aa0a6"/>'
        )
        parts.append(
            f'<rect x="{packed_x:.1f}" y="{packed_y:.1f}" width="{bar_width:.1f}" '
            f'height="{margin_top + chart_height - packed_y:.1f}" fill="#4285f4"/>'
        )
        parts.append(svg_text(center_x, margin_top + chart_height + 24, f"Run {idx}", 12))

    legend_x = width - margin_right - 210
    legend_y = margin_top - 14
    parts.append(f'<rect x="{legend_x}" y="{legend_y}" width="14" height="14" fill="#9aa0a6"/>')
    parts.append(svg_text(legend_x + 65, legend_y + 12, "Packing disabled", 13))
    parts.append(f'<rect x="{legend_x + 145}" y="{legend_y}" width="14" height="14" fill="#4285f4"/>')
    parts.append(svg_text(legend_x + 195, legend_y + 12, "Packing enabled", 13))

    summary_x = width - margin_right - 305
    summary_y = margin_top + 18
    summary_w = 285
    summary_h = 120
    parts.append(
        f'<rect x="{summary_x}" y="{summary_y}" width="{summary_w}" height="{summary_h}" '
        'fill="#f7f7f7" stroke="#cccccc"/>'
    )
    parts.append(svg_text(summary_x + summary_w / 2, summary_y + 24, "Median Summary", 16))
    parts.append(
        svg_text(
            summary_x + 16,
            summary_y + 50,
            f"Disabled: {baseline['median_avg_us']:.4f} us",
            13,
            "start",
        )
    )
    parts.append(
        svg_text(
            summary_x + 16,
            summary_y + 72,
            f"Enabled:  {packed['median_avg_us']:.4f} us",
            13,
            "start",
        )
    )
    parts.append(
        svg_text(
            summary_x + 16,
            summary_y + 94,
            f"Speedup:  {speedup:.3f}x",
            13,
            "start",
        )
    )
    parts.append(
        svg_text(
            summary_x + 16,
            summary_y + 116,
            f"Improvement: {improvement_pct:.2f}%",
            13,
            "start",
        )
    )

    parts.append(svg_text(28, margin_top + chart_height / 2, "avg_us", 14, "middle"))
    parts.append(svg_text(width / 2, height - 22, "Measured runs", 14))
    parts.append("</svg>")

    output_path.write_text("\n".join(parts))


def print_summary(
    baseline: Dict[str, object],
    packed: Dict[str, object],
    improvement_pct: float,
    speedup: float,
    plot_path: Path,
    csv_path: Path,
    json_path: Path,
) -> None:
    print("Array packing comparison results")
    print("=" * 34)
    print(f"Benchmark: {baseline['variant']} vs {packed['variant']}")
    print()
    print(f"Disabled median avg_us: {baseline['median_avg_us']:.4f}")
    print(f"Enabled  median avg_us: {packed['median_avg_us']:.4f}")
    print(f"Speedup: {speedup:.3f}x")
    print(f"Improvement: {improvement_pct:.2f}%")
    print()
    print(
        f"Disabled warnings: {baseline['scatter_gather_warnings']} scatter/gather site(s) at compile time"
    )
    print(
        f"Enabled  warnings: {packed['scatter_gather_warnings']} scatter/gather site(s) at compile time"
    )
    print(f"Disabled checksum: {baseline['checksum']}")
    print(f"Enabled  checksum: {packed['checksum']}")
    print()
    print(f"CSV:  {csv_path}")
    print(f"JSON: {json_path}")
    print(f"SVG:  {plot_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare array packing enabled vs disabled on a Parsimony benchmark."
    )
    script_dir = Path(__file__).resolve().parent
    parser.add_argument(
        "--source",
        default=str(script_dir / "array_packing_perf.cpp"),
        help="Benchmark source file to compile and run.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=7,
        help="Number of measured runs per variant.",
    )
    parser.add_argument(
        "--warmup-runs",
        type=int,
        default=1,
        help="Number of warmup runs before measurements.",
    )
    parser.add_argument(
        "--parsimony",
        default="parsimony",
        help="Parsimony compiler command to invoke.",
    )
    parser.add_argument(
        "--extra-cflags",
        default="",
        help="Additional compile flags passed through to parsimony.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(script_dir / "comparison_results"),
        help="Directory for CSV/JSON/SVG output and temporary build artifacts.",
    )
    args = parser.parse_args()

    tests_dir = script_dir
    source = Path(args.source).resolve()
    if not source.exists():
        raise SystemExit(f"Benchmark source not found: {source}")

    compiler_dir = tests_dir.parent
    include_dir = (compiler_dir.parent / "apps" / "synet-simd" / "src").resolve()
    if not include_dir.exists():
        raise SystemExit(f"Include directory not found: {include_dir}")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    bin_dir = output_dir / "bin"
    tmp_root = output_dir / "tmp"
    bin_dir.mkdir(parents=True, exist_ok=True)
    tmp_root.mkdir(parents=True, exist_ok=True)

    extra_compile_args = shlex.split(args.extra_cflags)

    disabled_compile = compile_variant(
        variant_name="packing_off",
        source=source,
        tests_dir=tests_dir,
        parsimony_cmd=args.parsimony,
        include_dir=include_dir,
        bin_dir=bin_dir,
        tmp_root=tmp_root,
        extra_psv_args="-fno-array-packing",
        extra_compile_args=extra_compile_args,
    )
    enabled_compile = compile_variant(
        variant_name="packing_on",
        source=source,
        tests_dir=tests_dir,
        parsimony_cmd=args.parsimony,
        include_dir=include_dir,
        bin_dir=bin_dir,
        tmp_root=tmp_root,
        extra_psv_args="",
        extra_compile_args=extra_compile_args,
    )

    baseline = run_variant(
        variant_name="packing_disabled",
        binary=disabled_compile["binary"],
        tests_dir=tests_dir,
        warmup_runs=args.warmup_runs,
        measured_runs=args.runs,
    )
    packed = run_variant(
        variant_name="packing_enabled",
        binary=enabled_compile["binary"],
        tests_dir=tests_dir,
        warmup_runs=args.warmup_runs,
        measured_runs=args.runs,
    )

    baseline["scatter_gather_warnings"] = disabled_compile["scatter_gather_warnings"]
    packed["scatter_gather_warnings"] = enabled_compile["scatter_gather_warnings"]
    baseline["compile_command"] = disabled_compile["command"]
    packed["compile_command"] = enabled_compile["command"]

    if baseline["checksum"] != packed["checksum"]:
        raise ValueError(
            "Checksum mismatch between packing-disabled and packing-enabled binaries: "
            f"{baseline['checksum']} vs {packed['checksum']}"
        )

    improvement_pct = (
        (baseline["median_avg_us"] - packed["median_avg_us"]) / baseline["median_avg_us"]
    ) * 100.0
    speedup = baseline["median_avg_us"] / packed["median_avg_us"]

    csv_path = output_dir / f"{source.stem}_comparison.csv"
    json_path = output_dir / f"{source.stem}_comparison.json"
    svg_path = output_dir / f"{source.stem}_comparison.svg"

    write_csv(csv_path, baseline, packed)
    generate_svg(svg_path, baseline, packed, improvement_pct, speedup)

    summary = {
        "source": str(source),
        "runs": args.runs,
        "warmup_runs": args.warmup_runs,
        "comparison": {
            "packing_disabled": baseline,
            "packing_enabled": packed,
            "median_speedup": speedup,
            "median_improvement_pct": improvement_pct,
        },
    }
    json_path.write_text(json.dumps(summary, indent=2))

    print_summary(baseline, packed, improvement_pct, speedup, svg_path, csv_path, json_path)


if __name__ == "__main__":
    main()
