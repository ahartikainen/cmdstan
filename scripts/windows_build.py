"""Experimental native Windows x64 CmdStan builds (cl and clang-cl)."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import windows_toolchain as toolchain

ROOT = Path(__file__).resolve().parents[1]
STAN = ROOT / "stan"
MATH = STAN / "lib" / "stan_math"
BUILD = ROOT / "build" / "windows"
UTILITIES = ("print", "stansummary", "diagnose")
MACROS = (
    "BOOST_DISABLE_ASSERTS", "BOOST_PHOENIX_NO_VARIADIC_EXPRESSION",
    "BOOST_ALL_NO_LIB", "__TBB_NO_IMPLICIT_LINKAGE=1", "NOMINMAX",
    "WIN32_LEAN_AND_MEAN", "_USE_MATH_DEFINES", "_BOOST_LGAMMA=1",
    "SUNDIALS_STATIC_DEFINE", "_ITERATOR_DEBUG_LEVEL=0", "NDEBUG",
)


def run(args, env=None):
    """Stream commands and diagnostics, aborting on the first failure."""
    args = [str(arg) for arg in args]
    print(subprocess.list2cmdline(args), flush=True)
    subprocess.run(args, cwd=ROOT, env=env, check=True)


def executable(name, env):
    path = shutil.which(name + ".exe", path=env.get("PATH", ""))
    if not path:
        raise RuntimeError(f"{name}.exe not found in selected toolchain")
    return path


def accept_license(args):
    return args.accept_license or os.environ.get("CMDSTAN_ACCEPT_MSVC_LICENSE", "").lower() == "yes"


def install(args):
    root = toolchain.toolchain_directory()
    accepted = accept_license(args)
    if not (root / "msvc" / "setup_x64.bat").is_file() and not accepted:
        print(f"Review the Microsoft Build Tools and SDK terms: {toolchain.LICENSE_URL}")
        if sys.stdin.isatty():
            accepted = input("Accept the applicable license terms? [y/N] ").strip().lower() in {"y", "yes"}
        if not accepted:
            raise RuntimeError("License acceptance required for Microsoft downloads")
    toolchain.install_portable(root, args.compiler, accepted)


def environment(args):
    base = {key.upper(): value for key, value in os.environ.items()}
    if args.toolchain == "official":
        env = toolchain.official_environment(args.compiler, base)
    elif args.toolchain == "portable":
        install(args)
        env = toolchain.portable_environment(toolchain.toolchain_directory(), args.compiler, base)
    else:
        try:
            env = toolchain.toolchain_environment(args.compiler)
        except RuntimeError:
            # The installer reuses existing components and refuses partial installations.
            install(args)
            env = toolchain.portable_environment(toolchain.toolchain_directory(), args.compiler, base)
    for name in (args.compiler, "link", "lib"):
        print(f"{name}: {executable(name, env)}", flush=True)
    # Avoid flags injected by another developer environment.
    for name in ("CL", "_CL_", "LINK", "_LINK_"):
        env.pop(name, None)
    return env


def fetch(url, target):
    """Cache release assets. Log their digests (not a pinned-hash verification)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        temp = target.with_suffix(target.suffix + ".part")
        print(f"Downloading {url}", flush=True)
        try:
            with urllib.request.urlopen(url, timeout=120) as src, temp.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
    with target.open("rb") as stream:
        print(f"{target.name}: sha256={hashlib.file_digest(stream, 'sha256').hexdigest()}", flush=True)
    return target


def dependency(name):
    """Use the dependency selected by the checked-out Stan Math makefiles."""
    text = (MATH / "make" / "libraries").read_text(encoding="utf-8")
    match = re.search(rf"^\s*{name}\s*\?=\s*\$\(MATH\)lib/([^\s#]+)", text, re.MULTILINE)
    if not match:
        raise RuntimeError(f"Cannot determine {name} from Stan Math make/libraries")
    path = MATH / "lib" / match.group(1)
    if not path.is_dir():
        raise RuntimeError(f"Missing vendored dependency: {path}")
    return path


def patch_sundials_header(text):
    """Adapt vendored SUNDIALS headers to the Windows compiler."""
    text = text.replace("__attribute__ ((__deprecated__(msg)))", "__declspec(deprecated(msg))")
    text = text.replace("__attribute__ ((__deprecated__))", "__declspec(deprecated)")
    # Match the directive, not the example inside the preceding C comment.
    return re.sub(
        r"(?m)^[ \t]*#[ \t]*define[ \t]+SUNDIALS_HAVE_POSIX_TIMERS\b[^\r\n]*$",
        "#undef SUNDIALS_HAVE_POSIX_TIMERS",
        text,
    )


def checked_out_revision(path):
    if (path / ".git").exists():
        return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    return "release archive"


def copy_binary(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or not source.samefile(destination):
        shutil.copy2(source, destination)


class Build:
    """Build the existing CmdStan sources without modifying the submodules."""

    def __init__(self, args, env):
        self.args, self.env = args, env
        if not (MATH / "make" / "libraries").is_file():
            raise RuntimeError("Initialize submodules: git submodule update --init --recursive")
        self.cxx = executable(args.compiler, env)
        self.link = executable("link", env)
        self.lib = executable("lib", env)
        self.sundials = dependency("SUNDIALS")
        self.tbb = dependency("TBB")
        self.boost = dependency("BOOST")
        self.eigen = dependency("EIGEN")
        rapid = sorted((STAN / "lib").glob("rapidjson_*"))
        cli = sorted((ROOT / "lib").glob("CLI11-*"))
        if len(rapid) != 1 or len(cli) != 1:
            raise RuntimeError("Expected exactly one vendored RapidJSON and CLI11 directory")
        self.groups = ["cvodes", "idas", "kinsol"]
        if "SUNDIALS_ARKODE" in (MATH / "make" / "libraries").read_text(encoding="utf-8"):
            self.groups.append("arkode")
        self.groups.append("nvecserial")
        config = {
            "compiler": args.compiler, "optimization": args.optimization, "threads": args.threads,
            "compiler_path": self.cxx, "link_path": self.link, "lib_path": self.lib,
            "compiler_mtime": Path(self.cxx).stat().st_mtime_ns,
            "link_mtime": Path(self.link).stat().st_mtime_ns,
            "lib_mtime": Path(self.lib).stat().st_mtime_ns,
            "INCLUDE": env.get("INCLUDE"), "LIB": env.get("LIB"),
            "stanc": args.stanc_version, "revisions": [checked_out_revision(p) for p in (ROOT, STAN, MATH)],
            "script": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
        key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]
        self.directory = BUILD / f"{args.compiler}-{key}"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.marker = self.directory / "complete.json"
        self.include = self.directory / "sundials-include"
        self.includes = [ROOT / "src", STAN / "src", rapid[0], cli[0], MATH,
                         self.eigen, self.boost, self.include, self.sundials / "src/sundials", self.tbb / "include"]
        self.libs = [self.directory / f"sundials_{name}.lib" for name in self.groups] + [self.directory / "tbb.lib"]
        self.flags = ["/nologo", "/std:c++17", "/EHsc", "/MD", "/bigobj", "/Zc:__cplusplus",
                      "/" + args.optimization, "/fp:precise"]
        if args.compiler == "cl":
            self.flags += ["/permissive-", "/utf-8"]
        self.flags += ["/D" + macro for macro in MACROS]
        if args.threads:
            self.flags.append("/DSTAN_THREADS")
        self.flags += ["/I" + str(p) for p in self.includes]
        self.clang_libs = []
        if args.compiler == "clang-cl":
            resource = Path(subprocess.check_output([self.cxx, "--print-resource-dir"], env=env, text=True).strip())
            directory = resource / "lib" / "windows"
            if directory.is_dir():
                self.clang_libs.append("/LIBPATH:" + str(directory))
                builtins = directory / "clang_rt.builtins-x86_64.lib"
                if builtins.is_file():
                    self.clang_libs.append(str(builtins))
        print(f"Native build cache: {self.directory}", flush=True)
        print(f"Dependencies: {self.boost.name}, {self.eigen.name}, {self.sundials.name}, {self.tbb.name}", flush=True)

    def compile_cpp(self, source, target, extra=()):
        run([self.cxx, *self.flags, *extra, "/TP", "/c", source, "/Fo" + str(target)], self.env)

    def link_exe(self, objects, target, libs):
        # Publish a complete executable only after the linker succeeds.
        with tempfile.TemporaryDirectory(prefix="link-", dir=target.parent) as tmp:
            staged = Path(tmp) / target.name
            run([self.link, "/NOLOGO", "/MACHINE:X64", "/OUT:" + str(staged),
                 *objects, *libs, "Ws2_32.lib", *self.clang_libs], self.env)
            staged.replace(target)

    def prepare(self):
        """Use vendored headers; adapt only a private SUNDIALS include copy."""
        shutil.copytree(self.sundials / "include", self.include, dirs_exist_ok=True)
        for name in ("sundials_config.h", "sundials_export.h"):
            path = self.include / "sundials" / name
            text = path.read_text(encoding="utf-8")
            path.write_text(patch_sundials_header(text), encoding="utf-8")
        version = self.tbb.name.removeprefix("tbb_")
        archive = fetch(f"https://github.com/uxlfoundation/oneTBB/releases/download/v{version}/tbb-{version}-win.zip",
                        BUILD / "downloads" / f"tbb-{version}-win.zip")
        with zipfile.ZipFile(archive) as z:
            for name in ("tbb.dll", "tbb.lib"):
                candidates = [p for p in z.namelist() if p.lower().endswith("/" + name)
                              and "/intel64/" in p.lower() and any(x.startswith("vc14") for x in p.lower().split("/"))]
                if not candidates:
                    raise RuntimeError(f"No x64 vc14-family {name} in {archive}")
                chosen = sorted(candidates, key=lambda p: ("/vc14/" not in p.lower(), p))[0]
                print(f"TBB: {chosen}", flush=True)
                with z.open(chosen) as src, (self.directory / name).open("wb") as dst:
                    shutil.copyfileobj(src, dst)
            for name in z.namelist():
                if name.rsplit("/", 1)[-1].lower() in {"license", "license.txt"}:
                    (self.directory / "TBB-LICENSE.txt").write_bytes(z.read(name))
                    break
        bundled = ROOT / "bin" / "windows-stanc"
        if bundled.is_file() and self.args.stanc_version == "nightly":
            copy_binary(bundled, self.directory / "stanc.exe")
        else:
            tag = self.args.stanc_version
            # Same nightly default and Windows asset as make/stanc.
            asset = fetch(f"https://github.com/stan-dev/stanc3/releases/download/{tag}/windows-stanc",
                          BUILD / "downloads" / f"stanc-{tag}.exe")
            copy_binary(asset, self.directory / "stanc.exe")
        run([self.directory / "stanc.exe", "--version"], self.env)

    def build_sundials(self):
        """Match Stan Math's C source groups, including ARKODE when present."""
        src = self.sundials / "src"
        common = [p for p in (src / "sundials").glob("*.c") if p.name != "sundials_profiler.c"]
        for folder in ("sunmatrix/band", "sunmatrix/dense", "sunlinsol/band", "sunlinsol/dense",
                       "sunnonlinsol/newton", "sunnonlinsol/fixedpoint"):
            common += [p for p in (src / folder).glob("*.c") if not p.name.startswith("f")]
        groups = {}
        for name in self.groups:
            if name == "nvecserial":
                groups[name] = [src / "nvector/serial/nvector_serial.c", src / "sundials/sundials_math.c"]
            else:
                files = list((src / name).glob("*.c"))
                if not files:
                    raise RuntimeError(f"Missing SUNDIALS {name} sources")
                groups[name] = sorted(set(common + files))
        objects = {p: self.directory / ("_".join(p.relative_to(src).parts) + ".obj")
                   for p in sorted({p for files in groups.values() for p in files})}

        def compile_c(path):
            args = [self.cxx, "/nologo", "/TC", "/c", "/MD", "/O2", "/DNDEBUG",
                    "/DSUNDIALS_STATIC_DEFINE", "/DNO_FPRINTF_OUTPUT", "/D_USE_MATH_DEFINES",
                    "/I" + str(self.include), "/I" + str(src / "sundials"),
                    "/FI" + str(self.include / "stan_sundials_printf_override.hpp"), path, "/Fo" + str(objects[path])]
            if self.args.compiler == "cl":
                args.insert(3, "/std:c11")
            run(args, self.env)

        with ThreadPoolExecutor(max_workers=self.args.jobs) as pool:
            list(pool.map(compile_c, objects))
        for name, paths in groups.items():
            target = self.directory / f"sundials_{name}.lib"
            target.unlink(missing_ok=True)
            run([self.lib, "/NOLOGO", "/OUT:" + str(target), *(objects[p] for p in paths)], self.env)

    def publish(self):
        for name in ("stanc.exe", "tbb.dll", *(u + ".exe" for u in UTILITIES)):
            copy_binary(self.directory / name, ROOT / "bin" / name)
        license_file = self.directory / "TBB-LICENSE.txt"
        if license_file.is_file():
            copy_binary(license_file, ROOT / "bin" / license_file.name)

    def build(self):
        required = [*self.libs, self.directory / "main.obj", self.directory / "tbb.dll",
                    self.directory / "stanc.exe", *(self.directory / (u + ".exe") for u in UTILITIES)]
        if self.args.rebuild or not self.marker.is_file() or not all(p.is_file() for p in required):
            self.marker.unlink(missing_ok=True)
            self.prepare()
            self.build_sundials()
            self.compile_cpp(ROOT / "src/cmdstan/main.cpp", self.directory / "main.obj")
            for utility in UTILITIES:
                obj = self.directory / (utility + ".obj")
                self.compile_cpp(ROOT / "src/cmdstan" / (utility + ".cpp"), obj)
                self.link_exe([obj], self.directory / (utility + ".exe"), self.libs)
            self.marker.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
        self.publish()
        print("CmdStan native Windows build complete", flush=True)

    def model(self, source):
        source = source.resolve()
        if source.suffix.lower() != ".stan" or not source.is_file():
            raise RuntimeError(f"Expected an existing .stan model: {source}")
        self.build()
        target = source.with_suffix(".exe")
        copy_binary(self.directory / "tbb.dll", target.parent / "tbb.dll")
        with tempfile.TemporaryDirectory(prefix="model-", dir=self.directory) as tmp:
            generated = Path(tmp) / (source.stem + ".hpp")
            obj = generated.with_suffix(".obj")
            run([self.directory / "stanc.exe", *self.args.stanc_flag, "--o=" + str(generated), source], self.env)
            self.compile_cpp(generated, obj, ["/I" + str(source.parent)])
            self.link_exe([obj, self.directory / "main.obj"], target, self.libs)
        license_file = self.directory / "TBB-LICENSE.txt"
        if license_file.is_file():
            copy_binary(license_file, target.parent / license_file.name)
        print(f"Model executable: {target}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "build", "model"))
    parser.add_argument("target", nargs="?", help="portable (install) or path to .stan (model)")
    parser.add_argument("--compiler", choices=("cl", "clang-cl"), default="clang-cl")
    parser.add_argument("--toolchain", choices=("auto", "official", "portable"), default="auto")
    parser.add_argument("--accept-license", action="store_true")
    parser.add_argument("--optimization", choices=("O2", "Od"), default="O2")
    parser.add_argument("--threads", action="store_true")
    parser.add_argument("--stanc-version", default=os.environ.get("STANC3_VERSION", "nightly"))
    parser.add_argument("--stanc-flag", action="append", default=[])
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("-j", "--jobs", type=int, default=2, help="parallel SUNDIALS C compilations")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.stanc_version) or args.stanc_version in {".", ".."}:
        parser.error("--stanc-version must be a release tag or nightly")
    if args.action == "install" and args.target not in {None, "portable"}:
        parser.error("install only supports portable")
    if args.action == "model" and not args.target:
        parser.error("model requires a .stan file")
    if args.action == "build" and args.target:
        parser.error("build takes no target")
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        parser.error("Native Windows x64 Python is required")
    try:
        if args.action == "install":
            install(args)
            return
        build = Build(args, environment(args))
        if args.action == "build":
            build.build()
        else:
            build.model(Path(args.target))
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Windows build failed: {exc}\n")


if __name__ == "__main__":
    main()
