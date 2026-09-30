"""Generate the fictional DemoMart Retail sample datasets.

Usage::

    python scripts/generate_sample_data.py                  # data/sample, with DQ issues
    python scripts/generate_sample_data.py --clean          # no injected issues
    python scripts/generate_sample_data.py --scale 20 --output data/perf --no-excel
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from smartmis.samples import SampleDataGenerator  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "data" / "sample")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--scale", type=float, default=1.0, help="volume multiplier (20 ≈ 500k sales lines)"
    )
    parser.add_argument("--clean", action="store_true", help="do not inject data-quality issues")
    parser.add_argument("--no-excel", action="store_true", help="skip the multi-sheet workbook")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    dataset = SampleDataGenerator(
        args.seed, scale=args.scale, inject_issues=not args.clean
    ).generate()
    paths = dataset.write(args.output, excel=not args.no_excel)

    print(f"DemoMart sample data written to {args.output}")
    for name, frame in dataset.tables().items():
        print(f"  {name:<10} {len(frame):>9,} rows")
    print(f"  files: {', '.join(p.name for p in paths)}")
    if dataset.injected:
        print("  injected issues / anomalies:")
        for key, value in dataset.injected.items():
            print(f"    {key:<34} {value:>6}")
    print(f"Done in {time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
