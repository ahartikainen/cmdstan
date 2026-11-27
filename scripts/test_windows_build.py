"""Regression tests for Windows build preparation (no compiler required)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import windows_build
import windows_toolchain
import windows_test


class SundialsHeaderTests(unittest.TestCase):
    def test_posix_timer_documentation_is_preserved(self):
        comment = "/* Use POSIX timers if available.\n *     #define SUNDIALS_HAVE_POSIX_TIMERS\n */\n"
        source = comment + "#define SUNDIALS_HAVE_POSIX_TIMERS\n"
        expected = comment + "#undef SUNDIALS_HAVE_POSIX_TIMERS\n"
        self.assertEqual(windows_build.patch_sundials_header(source), expected)
        self.assertEqual(windows_build.patch_sundials_header(expected), expected)

    def test_deprecation_and_macro_boundary(self):
        source = (
            "#  define SUNDIALS_HAVE_POSIX_TIMERS 1\n"
            "#define SUNDIALS_HAVE_POSIX_TIMERS_OTHER 1\n"
            "#define DEPRECATED(msg) __attribute__ ((__deprecated__(msg)))\n"
            "#define DEPRECATED_DEFAULT __attribute__ ((__deprecated__))\n"
        )
        expected = (
            "#undef SUNDIALS_HAVE_POSIX_TIMERS\n"
            "#define SUNDIALS_HAVE_POSIX_TIMERS_OTHER 1\n"
            "#define DEPRECATED(msg) __declspec(deprecated(msg))\n"
            "#define DEPRECATED_DEFAULT __declspec(deprecated)\n"
        )
        self.assertEqual(windows_build.patch_sundials_header(source), expected)


class OfficialToolchainTests(unittest.TestCase):
    def test_official_clang_prefers_visual_studio_llvm(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            vswhere = base / "Microsoft Visual Studio/Installer/vswhere.exe"
            vswhere.parent.mkdir(parents=True)
            vswhere.touch()
            root = base / "Microsoft Visual Studio/18/Enterprise"
            bundled = root / "VC/Tools/Llvm/x64/bin/clang-cl.exe"
            standalone = base / "LLVM/bin/clang-cl.exe"
            for compiler in (bundled, standalone):
                compiler.parent.mkdir(parents=True)
                compiler.touch()
            env = {
                "PROGRAMFILES(X86)": str(base),
                "PROGRAMFILES": str(base),
                "PATH": str(standalone.parent),
            }

            def which(name, path):
                for directory in path.split(os.pathsep):
                    candidate = Path(directory) / name
                    if candidate.is_file():
                        return str(candidate)
                return None

            with (
                patch.object(windows_toolchain.subprocess, "check_output", return_value=str(root) + "\n"),
                patch.object(windows_toolchain, "activate_batch", return_value={"PATH": env["PATH"]}),
                patch.object(windows_toolchain.shutil, "which", side_effect=which),
            ):
                result = windows_toolchain.official_environment("clang-cl", env)
                self.assertEqual(which("clang-cl.exe", result["PATH"]), str(bundled))
                bundled.unlink()
                result = windows_toolchain.official_environment("clang-cl", env)
                self.assertEqual(which("clang-cl.exe", result["PATH"]), str(standalone))

    def test_visual_studio_2026_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            vswhere = base / "Microsoft Visual Studio/Installer/vswhere.exe"
            vswhere.parent.mkdir(parents=True)
            vswhere.touch()
            for version in ("17", "18"):
                root = base / "Microsoft Visual Studio" / version / "Enterprise"
                for family in ("cl", "clang-cl"):
                    with self.subTest(version=version, family=family):
                        env = {"PROGRAMFILES(X86)": str(base), "PROGRAMFILES": str(base), "PATH": ""}
                        with (
                            patch.object(windows_toolchain.subprocess, "check_output", return_value=str(root) + "\n") as query,
                            patch.object(windows_toolchain, "activate_batch", return_value={"PATH": str(root)}) as activate,
                            patch.object(windows_toolchain.shutil, "which", return_value=str(root / (family + ".exe"))),
                        ):
                            result = windows_toolchain.official_environment(family, env)
                        args = query.call_args.args[0]
                        self.assertEqual(args[args.index("-version") + 1], "[17.0,)")
                        self.assertEqual(activate.call_args.args[0], root / "VC/Auxiliary/Build/vcvars64.bat")
                        self.assertIn(str(root), result["PATH"])


class UpstreamInterfaceTests(unittest.TestCase):
    def test_full_model_coverage(self):
        tests = windows_test.discover_tests([windows_test.INTERFACE])
        self.assertEqual(set(tests), set(windows_test.INTERFACE.rglob("*_test.cpp")))
        rules = (windows_build.ROOT / "make/tests").read_text(encoding="utf-8")
        models = windows_test.test_models(tests, rules)
        self.assertEqual(set(models), set(windows_test.MODEL_DIR.glob("*.stan")))

    def test_selected_tests_and_dependencies(self):
        test = windows_test.INTERFACE / "log_prob_test.cpp"
        selected = windows_test.discover_tests([test, test])
        self.assertEqual(selected, [test])
        rules = (windows_build.ROOT / "make/tests").read_text(encoding="utf-8")
        self.assertEqual({p.stem for p in windows_test.test_models(selected, rules)},
                         {"bern_log_prob_model", "bern_gq_model", "simplex_model"})
        argument = windows_test.INTERFACE / "arguments/argument_test.cpp"
        self.assertEqual(windows_test.test_models([argument], rules), [])
        self.assertEqual(windows_test.test_target(argument),
                         windows_build.ROOT / "test/interface/arguments/argument_test.exe")
        with self.assertRaises(ValueError):
            windows_test.discover_tests([windows_test.MODEL_DIR])

    def test_vendored_gtest_and_native_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            math = root / "math"
            benchmark = math / "lib/benchmark_1.5.1"
            gtest = benchmark / "googletest/googletest"
            for name in ("include/gtest/gtest.h", "src/gtest-all.cc", "src/gtest_main.cc"):
                path = gtest / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            (math / "make").mkdir()
            (math / "make/libraries").write_text(
                "BENCHMARK ?= $(MATH)lib/benchmark_1.5.1\n"
                "GTEST ?= $(BENCHMARK)/googletest/googletest\n", encoding="utf-8")
            build = Mock(directory=root / "build", libs=[root / "tbb.lib"])
            source = windows_test.INTERFACE / "arguments/argument_test.cpp"
            with (patch.object(windows_build, "MATH", math),
                  patch.object(windows_build, "ROOT", root),
                  patch.object(windows_build, "copy_binary") as copy):
                self.assertEqual(windows_test.gtest_path(), gtest)
                targets = windows_test.build_tests(build, [source])
                target = root / "test/interface/arguments/argument_test.exe"
                self.assertEqual(targets, [target])
                self.assertEqual(build.compile_cpp.call_count, 3)
                build.link_exe.assert_called_once_with(
                    [target.with_suffix(".obj"), build.directory / "gtest-all.obj",
                     build.directory / "gtest_main.obj"], target, build.libs)
                copy.assert_called_once_with(build.directory / "tbb.dll", target.parent / "tbb.dll")

    def test_results_and_failures_are_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            targets = [Path(tmp) / name for name in ("first.exe", "second.exe")]
            targets[0].with_suffix(".xml").touch()  # stale result must not count
            def fake_run(args, env):
                self.assertNotIn("CMDSTAN_UPDATE_EXPECTED_OUTPUT", env)
                target = args[0]
                if target == targets[0]:
                    raise RuntimeError("test failed")
                target.with_suffix(".xml").touch()
            with (patch.dict(os.environ, {"CMDSTAN_UPDATE_EXPECTED_OUTPUT": "1"}),
                  patch.object(windows_build, "run", side_effect=fake_run) as run):
                with self.assertRaisesRegex(RuntimeError, "1 of 2"):
                    windows_test.run_tests(targets)
                self.assertEqual(run.call_count, 2)
            self.assertFalse(targets[0].with_suffix(".xml").exists())
            self.assertTrue(targets[1].with_suffix(".xml").exists())


if __name__ == "__main__":
    unittest.main()
