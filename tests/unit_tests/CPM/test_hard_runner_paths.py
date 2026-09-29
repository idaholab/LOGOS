"""Path and CLI regression tests for the hard-instance benchmark runners."""

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATHS = [
    REPO_ROOT / "doc" / "demos" / "heuristics" / "ga_test_hard.py",
    REPO_ROOT / "doc" / "demos" / "heuristics" / "gans_test_hard.py",
]
EXPECTED_CASES = {
    "j12051_6",
    "j12031_10",
    "j12036_6",
    "j12056_7",
    "j12051_5",
    "j12056_1",
    "j12026_10",
    "j12051_7",
    "j12056_5",
    "j12056_9",
}


def _load_runner(path: Path):
    spec = importlib.util.spec_from_file_location(f"test_{path.stem}_{path.parent.name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=RUNNER_PATHS, ids=("ga", "gans"))
def runner(request):
    return _load_runner(request.param)


def test_runner_uses_canonical_repository_files(runner):
    assert runner.REPO_ROOT == REPO_ROOT
    assert runner.SCHEMA == (REPO_ROOT / "src" / "CPM" / "outage_schema.json").resolve()
    assert runner.BEST_RESULTS_PATH == (
        REPO_ROOT / "doc" / "demos" / "benchmarks" / "best_results.json"
    ).resolve()
    assert runner.SCHEMA.is_file()
    assert runner.BEST_RESULTS_PATH.is_file()


def test_cases_are_relative_to_external_data_root(runner):
    assert {name for name, _ in runner.CASES} == EXPECTED_CASES
    for name, relative_path in runner.CASES:
        assert isinstance(relative_path, Path)
        assert not relative_path.is_absolute()
        assert relative_path == Path("j120") / f"{name}.json"


def test_parse_args_requires_data_dir(runner):
    with pytest.raises(SystemExit) as exc_info:
        runner.parse_args([])
    assert exc_info.value.code == 2

    data_dir = Path("somewhere/PSPLIB_Json")
    assert runner.parse_args(["--data-dir", str(data_dir)]).data_dir == data_dir


def test_preflight_aggregates_all_missing_cases(runner, tmp_path):
    with pytest.raises(FileNotFoundError) as exc_info:
        runner.preflight_inputs(tmp_path / "PSPLIB_Json")

    message = str(exc_info.value)
    assert "--data-dir" in message
    assert "PSPLIB_Json root" in message
    for case_name in EXPECTED_CASES:
        assert case_name in message


def test_preflight_returns_resolved_case_paths(runner, tmp_path):
    data_root = tmp_path / "PSPLIB_Json"
    for name, relative_path in runner.CASES:
        path = data_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    case_paths = runner.preflight_inputs(data_root)

    assert [name for name, _ in case_paths] == [name for name, _ in runner.CASES]
    assert all(path.is_absolute() for _, path in case_paths)
    assert case_paths == [
        (name, (data_root / relative_path).resolve())
        for name, relative_path in runner.CASES
    ]


def test_case_runner_path_parameter_is_a_path_annotation(runner):
    case_runner = getattr(runner, "run_ga_case", None) or runner.run_gans_case
    assert case_runner.__annotations__["json_path"] is Path
