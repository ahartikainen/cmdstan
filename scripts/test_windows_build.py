"""Regression tests for Windows build preparation (no compiler required)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import windows_build
import windows_toolchain


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


if __name__ == "__main__":
    unittest.main()
