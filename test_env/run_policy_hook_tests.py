#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all config policy hook integration tests.")
    parser.add_argument("--work-root", default="/workspace/.tmp", help="Mounted temporary workspace for test repos.")
    parser.add_argument("--keep", action="store_true", help="Keep previous config test repos instead of cleaning first.")
    args = parser.parse_args()

    work_root = Path(args.work_root).resolve()
    work_root.mkdir(parents=True, exist_ok=True)

    configs = sorted(path for path in (ROOT / "configs").iterdir() if (path / "contribution.md").exists())
    if not configs:
        raise AssertionError("no configs with contribution.md found")

    for config_dir in configs:
        test_case = load_test_case(config_dir)
        test_case(config_dir=config_dir, work_root=work_root, keep=args.keep).run()

    print(f"policy hook tests passed for {len(configs)} configs under {work_root}")
    return 0


def load_test_case(config_dir: Path) -> type[Any]:
    test_path = config_dir / "test_case.py"
    if not test_path.exists():
        raise AssertionError(f"missing config test case: {test_path}")

    module_name = f"git_flow_guard_config_test_{config_dir.name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, test_path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load config test case: {test_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    test_case = getattr(module, "TEST_CASE", None)
    if test_case is None:
        raise AssertionError(f"{test_path} does not define TEST_CASE")
    return test_case


if __name__ == "__main__":
    raise SystemExit(main())
