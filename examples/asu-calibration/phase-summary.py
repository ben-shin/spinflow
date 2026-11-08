"""UNEXECUTED report-only reader aid; no NMR imports or processing."""
import json
from pathlib import Path

report = json.loads(Path("outputs/phase.json").read_text())
operation = report["operation"]
objective = report["objective"]
lines = [
    "Automatic phase illustration — not scientific phase acceptance",
    f"Fit finished: {report['convergence']['success']}",
    f"Correction: p0={operation['p0']} degrees; p1={operation['p1']} degrees; pivot={operation['pivot']} points",
    f"Normalized residual score: {objective['initial_rms']} -> {objective['normalized_rms']}",
    f"Accepted seed lines: {len(report['lines'])}; line span: {report['line_span_fraction']}",
    f"Flags: {', '.join(report['flags'])}",
    "Open diagnostic-phased-real.png and diagnostic-phased-imaginary.png alongside the before-phase views.",
    "Imaginary signal need not vanish; apparent positive peaks do not establish physical sign or accuracy.",
    "Full objective, per-line residuals, convergence, screening assumptions and units: phase.json.",
]
with Path("outputs/phase-summary.txt").open("x", encoding="utf-8") as stream:
    stream.write("\n".join(lines) + "\n")
