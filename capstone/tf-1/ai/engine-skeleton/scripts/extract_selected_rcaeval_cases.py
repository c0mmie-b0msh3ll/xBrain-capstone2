from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from extract_rcaeval_subsets import DEFAULT_SELECTION, wanted_files_for_case


def dataset_dir_for_case(case: str) -> Path:
    name = Path(case).name.lower()
    if name.startswith("re1ob"):
        return Path("RE1") / "RE1-OB"
    if name.startswith("re1ss"):
        return Path("RE1") / "RE1-SS"
    if name.startswith("re1tt"):
        return Path("RE1") / "RE1-TT"
    if name.startswith("re2ob"):
        return Path("RE2") / "RE2-OB"
    if name.startswith("re2ss"):
        return Path("RE2") / "RE2-SS"
    if name.startswith("re2tt"):
        return Path("RE2") / "RE2-TT"
    if name.startswith("re3ob"):
        return Path("RE3") / "RE3-OB"
    if name.startswith("re3ss"):
        return Path("RE3") / "RE3-SS"
    if name.startswith("re3tt"):
        return Path("RE3") / "RE3-TT"
    raise ValueError(f"Unsupported RCAEval case prefix: {case}")


def find_case_dir(data_root: Path, case: str) -> Path | None:
    case_name = Path(case).name
    dataset_root = data_root / dataset_dir_for_case(case)
    direct = dataset_root / case_name
    if direct.exists():
        return direct
    matches = list(dataset_root.rglob(case_name))
    return matches[0] if matches else None


def copy_case(case_dir: Path, destination_dir: Path, case: str) -> list[str]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for filename in sorted(wanted_files_for_case(case)):
        source = case_dir / filename
        if not source.exists():
            continue
        shutil.copy2(source, destination_dir / filename)
        copied.append(filename)
    return copied


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract TF1 selected cases from locally downloaded RCAEval utility data.")
    parser.add_argument("--data-root", type=Path, required=True, help="Root passed to RCAEval.utility download functions, containing RE1/RE2/RE3 folders.")
    parser.add_argument("--output-dir", type=Path, default=Path("datapack/external/rcaeval-subsets"))
    parser.add_argument("--keep-existing", action="store_true")
    args = parser.parse_args()

    if args.output_dir.exists() and not args.keep_existing:
        shutil.rmtree(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, object] = {
        "source": "Local RCAEval utility download output",
        "data_root": str(args.data_root).replace("\\", "/"),
        "selection_policy": "Three RCAEval cases per TF1 scenario; copied metrics/inject time plus RCAEval logs/traces when available for the selected dataset.",
        "cases": [],
    }
    cases_manifest = manifest["cases"]
    assert isinstance(cases_manifest, list)

    for scenario, cases in DEFAULT_SELECTION.items():
        for case in cases:
            case_name = Path(case).name
            source_dir = find_case_dir(args.data_root, case)
            destination_dir = args.output_dir / scenario / case_name
            copied = copy_case(source_dir, destination_dir, case) if source_dir else []
            expected = wanted_files_for_case(case)
            cases_manifest.append(
                {
                    "scenario": scenario,
                    "source_case": case,
                    "source_dir": str(source_dir).replace("\\", "/") if source_dir else None,
                    "local_dir": str(destination_dir).replace("\\", "/"),
                    "files": copied,
                    "expected_files": sorted(expected),
                    "complete": expected.issubset(set(copied)),
                }
            )

    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
