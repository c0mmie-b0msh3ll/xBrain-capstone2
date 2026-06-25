from __future__ import annotations

import argparse
import json
import shutil
import tarfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

import requests


DEFAULT_FIGSHARE_URL = "https://ndownloader.figshare.com/files/60960049"
BASE_FILES = {"metrics.json", "inject_time.txt"}
LOG_FILES = {"logs.csv"}
TRACE_FILES = {"traces.csv"}
DEFAULT_SELECTION = {
    "latency-degradation": [
        "data/re1ss_carts_delay_1",
        "data/re2ss_catalogue_delay_2",
        "data/re1ob_cartservice_delay_3",
    ],
    "critical-service-down": [
        "data/re2tt_ts-auth-service_loss_1",
        "data/re1ss_user_loss_2",
        "data/re2ss_orders_loss_1",
    ],
    "noisy-false-alert": [
        "data/re1ss_user_cpu_3",
        "data/re1ob_cartservice_mem_1",
        "data/re1tt_ts-route-service_disk_3",
    ],
}


def wanted_files_for_case(case: str) -> set[str]:
    case_name = Path(case).name.lower()
    files = set(BASE_FILES)
    if case_name.startswith(("re2", "re3")):
        files.update(LOG_FILES)
    if case_name.startswith(("re2ob", "re2tt", "re3ob", "re3tt")):
        files.update(TRACE_FILES)
    return files


class CountingStream:
    def __init__(self, raw: BinaryIO) -> None:
        self.raw = raw
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self.raw.read(size)
        self.bytes_read += len(chunk)
        return chunk


def extract_subsets(source_url: str, output_dir: Path) -> dict[str, object]:
    targets = {case for cases in DEFAULT_SELECTION.values() for case in cases}
    case_to_scenario = {case: scenario for scenario, cases in DEFAULT_SELECTION.items() for case in cases}
    wanted_by_case = {case: wanted_files_for_case(case) for case in targets}
    found = {case: set() for case in targets}

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with requests.get(source_url, headers={"User-Agent": "Mozilla/5.0"}, stream=True, timeout=120) as response:
        response.raise_for_status()
        stream = CountingStream(response.raw)
        archive = tarfile.open(fileobj=stream, mode="r|gz")
        for member in archive:
            if not member.isfile():
                continue
            parts = PurePosixPath(member.name).parts
            if len(parts) < 3:
                continue
            case = "/".join(parts[:2])
            filename = parts[-1]
            if case not in targets or filename not in wanted_by_case[case]:
                continue

            source = archive.extractfile(member)
            if source is None:
                continue
            scenario = case_to_scenario[case]
            destination_dir = output_dir / scenario / Path(case).name
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / filename
            with destination.open("wb") as handle:
                shutil.copyfileobj(source, handle)
            found[case].add(filename)
            print(f"extracted {member.name} -> {destination}")

            if all(wanted_by_case[case].issubset(files) for case, files in found.items()):
                break

    manifest = {
        "source": "Figshare RCAEval-v2 stream https://doi.org/10.6084/m9.figshare.31048672.v1",
        "source_url": source_url,
        "selection_policy": "Three RCAEval cases per TF1 scenario; extracted metrics/inject time plus RCAEval logs/traces when available for the selected dataset.",
        "stream_bytes_read": stream.bytes_read,
        "cases": [],
    }
    for scenario, cases in DEFAULT_SELECTION.items():
        for case in cases:
            local_dir = output_dir / scenario / Path(case).name
            files = sorted(path.name for path in local_dir.glob("*")) if local_dir.exists() else []
            manifest["cases"].append(
                {
                    "scenario": scenario,
                    "source_case": case,
                    "local_dir": str(local_dir).replace("\\", "/"),
                    "files": files,
                    "expected_files": sorted(wanted_by_case[case]),
                    "complete": wanted_by_case[case].issubset(set(files)),
                }
            )
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract the TF1 RCAEval subset without storing the full archive.")
    parser.add_argument("--source-url", default=DEFAULT_FIGSHARE_URL)
    parser.add_argument("--output-dir", type=Path, default=Path("datapack/external/rcaeval-subsets"))
    args = parser.parse_args()

    manifest = extract_subsets(args.source_url, args.output_dir)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
