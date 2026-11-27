"""Compile and run the upstream CmdStan interface tests with native Windows tools."""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

import windows_build as native

INTERFACE = native.ROOT / "src/test/interface"
MODEL_DIR = native.ROOT / "src/test/test-models"


def discover_tests(paths):
    """Use the same *_test.cpp convention as runCmdStanTests.py."""
    tests = set()
    for name in paths:
        path = Path(name).resolve()
        if not path.is_relative_to(INTERFACE.resolve()):
            raise ValueError(f"Not under src/test/interface: {path}")
        if path.is_dir():
            tests.update(path.rglob("*_test.cpp"))
        elif path.is_file() and path.name.endswith("_test.cpp"):
            tests.add(path)
        else:
            raise ValueError(f"Not a test directory or *_test.cpp file: {path}")
    if not tests:
        raise ValueError("No interface test files found")
    return sorted(tests)


def test_models(tests, rules):
    """Read model prerequisites directly from CmdStan's make/tests."""
    selected = {p.relative_to(INTERFACE).as_posix().removesuffix(".cpp") for p in tests}
    models = set()
    for line in rules.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if "$(call depends_on_test_models," not in line:
            continue
        match = re.fullmatch(
            r"test/interface/(\S+)\$\(EXE\):\s*\$\(call depends_on_test_models,\s*([^)]*)\)\s*",
            line,
        )
        if not match:
            raise ValueError(f"Unrecognized model dependency rule: {line}")
        if match[1] in selected:
            models.update(MODEL_DIR / f"{name}.stan" for name in match[2].split())
    for model in models:
        if not model.is_file():
            raise ValueError(f"Missing upstream test model: {model}")
    return sorted(models)


def gtest_path():
    """Use the vendored GoogleTest selected by Stan Math's make/libraries."""
    text = (native.MATH / "make/libraries").read_text(encoding="utf-8")
    match = re.search(r"(?m)^\s*GTEST\s*\?=\s*(\$\((MATH|BENCHMARK)\))([^\s#]+)", text)
    if not match:
        raise RuntimeError("Cannot determine GTEST from Stan Math make/libraries")
    base = native.MATH if match[2] == "MATH" else native.dependency("BENCHMARK")
    path = base / match[3].lstrip("/")
    for name in ("include/gtest/gtest.h", "src/gtest-all.cc", "src/gtest_main.cc"):
        if not (path / name).is_file():
            raise RuntimeError(f"Missing vendored GoogleTest file: {path / name}")
    return path


def test_target(source):
    return native.ROOT / "test/interface" / source.relative_to(INTERFACE).with_suffix(".exe")


def build_tests(build, tests):
    """Compile GoogleTest once; link each original test with the native libs."""
    gtest = gtest_path()
    extra = ["/DGTEST_HAS_PTHREAD=0", "/I" + str(gtest), "/I" + str(gtest / "include")]
    objects = []
    for name in ("gtest-all", "gtest_main"):
        obj = build.directory / (name + ".obj")
        # Rebuild test objects on each invocation; CmdStan libraries remain cached.
        build.compile_cpp(gtest / "src" / (name + ".cc"), obj, extra)
        objects.append(obj)
    targets = []
    for source in tests:
        target = test_target(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        obj = target.with_suffix(".obj")
        build.compile_cpp(source, obj, extra)
        build.link_exe([obj, *objects], target, build.libs)
        native.copy_binary(build.directory / "tbb.dll", target.parent / "tbb.dll")
        targets.append(target)
    return targets


def run_tests(targets):
    """Run serially from CmdStan root, retaining every GoogleTest XML result."""
    # Run with the normal process environment, not a compiler activation script.
    # Do not allow updating the checked-in expected output by mistake.
    env = {key: value for key, value in os.environ.items()
           if key.upper() != "CMDSTAN_UPDATE_EXPECTED_OUTPUT"}
    failures = []
    for target in targets:
        xml = target.with_suffix(".xml")
        xml.unlink(missing_ok=True)
        try:
            native.run([target, "--gtest_output=xml:" + str(xml)], env)
            if not xml.is_file():
                raise RuntimeError(f"Missing GoogleTest XML result: {xml}")
        except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
            failures.append((target, str(exc)))
    for target, error in failures:
        print(f"FAILED: {target}: {error}", file=sys.stderr)
    if failures:
        raise RuntimeError(f"{len(failures)} of {len(targets)} test executables failed")
    print(f"Completed {len(targets)} upstream test executables (see XML for test counts)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    native.add_build_arguments(parser)
    parser.add_argument("paths", nargs="*", default=[str(INTERFACE)])
    args = parser.parse_args()
    native.validate_build_arguments(parser, args)
    # Upstream tests use repository-relative paths and share output files.
    os.chdir(native.ROOT)
    try:
        tests = discover_tests(args.paths)
        models = test_models(tests, (native.ROOT / "make/tests").read_text(encoding="utf-8"))
        print(f"Selected {len(tests)} upstream test files and {len(models)} required models", flush=True)
        build = native.Build(args, native.environment(args))
        build.build()
        for model in models:
            build.model(model, build_first=False)
        targets = build_tests(build, tests)
        run_tests(targets)
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Native CmdStan tests failed: {exc}\n")


if __name__ == "__main__":
    main()
