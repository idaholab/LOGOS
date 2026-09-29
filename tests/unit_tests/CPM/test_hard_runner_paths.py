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
    assert runner.BENCHMARK_DIR == (
        REPO_ROOT / "doc" / "demos" / "benchmarks"
    ).resolve()
    assert runner.SCHEMA.is_file()
    assert runner.BEST_RESULTS_PATH.is_file()


def test_cases_are_benchmark_filenames(runner):
    assert {name for name, _ in runner.CASES} == EXPECTED_CASES
    for name, relative_path in runner.CASES:
        assert isinstance(relative_path, Path)
        assert not relative_path.is_absolute()
        assert relative_path == Path(f"{name}.json")
        assert (runner.BENCHMARK_DIR / relative_path).is_file()


def test_parse_args_defaults_to_all_cases(runner):
    assert runner.parse_args([]).case is None


@pytest.mark.parametrize("case_value", ["j12051_6", "j12051_6.json"])
def test_parse_args_accepts_case_stem_or_filename(runner, case_value):
    assert runner.parse_args(["--case", case_value]).case == ["j12051_6"]


def test_parse_args_rejects_unknown_case(runner):
    with pytest.raises(SystemExit) as exc_info:
        runner.parse_args(["--case", "not_a_case"])
    assert exc_info.value.code == 2


def test_preflight_returns_selected_resolved_case_path(runner):
    selected = [("j12051_6", Path("j12051_6.json"))]
    assert runner.preflight_inputs(selected) == [
        ("j12051_6", (runner.BENCHMARK_DIR / "j12051_6.json").resolve())
    ]


def test_case_runner_path_parameter_is_a_path_annotation(runner):
    case_runner = getattr(runner, "run_ga_case", None) or runner.run_gans_case
    assert case_runner.__annotations__["json_path"] is Path
