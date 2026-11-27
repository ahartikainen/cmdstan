<a href="http://mc-stan.org">
<img src="https://raw.githubusercontent.com/stan-dev/logos/master/logo.png" width=200 alt="Stan Logo"/>
</a>

# CmdStan

<b>CmdStan</b> is the command line interface to Stan, a package providing

* full Bayesian inference using the No-U-Turn sampler (NUTS), a variant of Hamiltonian Monte Carlo (HMC),
* penalized maximum likelihood estimation (MLE) using optimization, either Newton or quasi-Newton algorithms BFGS and L-BFGS,
* approximate Bayesian inference using automatic differentiation variational inference (ADVI),
* a full first- and higher-order automatic differentiation library based on C++ template overloads, and
* a supporting fully-templated matrix, linear algebra, and probability special function library.

[![DOI](https://zenodo.org/badge/16967338.svg)](https://zenodo.org/badge/latestdoi/16967338)

### Home Page
Stan's home page, with links to everything you'll need to use Stan is:

[http://mc-stan.org/](http://mc-stan.org/)

### Interfaces
There are separate repositories here on GitHub for interfaces:
* [CmdStan](https://github.com/stan-dev/cmdstan) (command-line/shell interface)
* [CmdStanPy](https://github.com/stan-dev/cmdstanpy) (a lightweight interface to CmdStan for Python users)
* [CmdStanR](https://github.com/stan-dev/cmdstanr) (a lightweight interface to CmdStan for R users)
* [PyStan](https://github.com/stan-dev/pystan) (Python interface)
* [RStan](https://github.com/stan-dev/rstan) (R interface)
* [MathematicaStan](https://github.com/stan-dev/MathematicaStan) (Mathematica interface)

### Source Repository
CmdStan's source-code repository is hosted here on GitHub.

### Licensing
The Stan-to-C++ compiler written in OCaml, core Stan C++ code, and CmdStan are licensed under new BSD.

Note that the Stan math library depends on the Intel TBB library which is licensed under the Apache 2.0 license. This dependency implies an additional restriction as compared to the new BSD lincense alone. The Apache 2.0 license is incompatible with GPL-2 licensed code if distributed as a unitary binary. You may refer to the Apache 2.0 evaluation page on the [Stan Math wiki](https://github.com/stan-dev/math/wiki/Apache-2.0-License-Evaluation).

## Installation
1. Download the latest release tarball (use the "green" link) from: [CmdStan releases](https://github.com/stan-dev/cmdstan/releases)
2. Unpack the tarball.
3. From the folder, type `make` for a quick tutorial on how to build models.

## Installation using git
See [Getting Started with
CmdStan](https://github.com/stan-dev/cmdstan/wiki/Getting-Started-with-CmdStan) for instructions how to clone both CmdStan and Stan submodule.

## Troubleshooting

As of version 2.22, CmdStan has switched to the new Stan-to-C++ compiler, called [stanc3](https://github.com/stan-dev/stanc3).  This compiler is intended to be backwards compatible with the existing Stan language and should accept all models that compile under the release 2.21 compiler, (see this [list of bug fixes](https://github.com/stan-dev/stanc3/wiki/changes-from-stanc2)). 

If a model that compiled for versions prior to 2.22, please report a bug on the [stanc3](https://github.com/stan-dev/stanc3) repository. Otherwise, report the issue to the [CmdStan](https://github.com/stan-dev/cmdstan) repository.

### Experimental native Windows build (MSVC and clang-cl)

The existing GNU Make build remains unchanged. The optional Windows build
script uses native x64 Microsoft-compatible compilers, without GNU Make,
MinGW, Rtools, WSL or a permanent PATH change. It requires Python 3.12+ and
recursively initialized Stan submodules. Run these commands from the CmdStan
root in an ordinary Command Prompt.

```bat
git submodule update --init --recursive
python scripts\windows_build.py install portable --compiler clang-cl
python scripts\windows_build.py build --compiler clang-cl --toolchain portable
python scripts\windows_build.py model examples\bernoulli\bernoulli.stan --compiler clang-cl --toolchain portable
examples\bernoulli\bernoulli.exe sample data file=examples/bernoulli/bernoulli.data.json
bin\stansummary.exe output.csv
```

Use `--compiler cl` for MSVC. `--toolchain official` discovers Visual Studio
2022 or newer Build Tools (and an installed LLVM for clang-cl). The default `auto`
tries an explicitly configured portable directory, an activated environment,
the default portable installation and Visual Studio, then offers a portable
installation if no suitable tools exist. `model` automatically builds CmdStan
if necessary. Use matching compiler options for `build` and `model`.

The portable installation defaults to `%LOCALAPPDATA%\cmdstan\toolchain`.
Set `CMDSTAN_TOOLCHAIN_DIR` to override it, or point it to an existing compatible
portable httpstan installation (a directory containing `msvc` and, for clang-cl,
`llvm`). The installer uses the pinned standalone MSVC extractor, MSVC 14.44,
Windows SDK 26100, and LLVM 20.1.8. The MSVC/SDK version defaults can be overridden
with `CMDSTAN_PORTABLE_MSVC_VERSION` and `CMDSTAN_PORTABLE_SDK_VERSION` before the
first installation. Microsoft components are downloaded only after acceptance
of the applicable [license terms](https://visualstudio.microsoft.com/license-terms/).
After reviewing these terms use `--accept-license` or set
`CMDSTAN_ACCEPT_MSVC_LICENSE=yes` for unattended installation. No Microsoft
compiler or SDK is redistributed by CmdStan. Windows Installer must be permitted
on the machine; several gigabytes of free space are needed.

The driver uses the versions of Boost, Eigen, TBB and SUNDIALS selected in the
checked-out Stan Math makefiles. It downloads the matching Microsoft-ABI TBB
release binaries, compiles the same SUNDIALS source groups to `.lib` files,
and uses the existing Windows `stanc` binary (or the upstream nightly asset).
It does not modify the Stan or Stan Math submodules. The build is cached under
`build/windows` by compiler/configuration/revisions; use `--rebuild` after editing
native sources or headers locally. `--stanc-version v2.40.0` (when that release
exists) or `STANC3_VERSION` selects another stanc release tag. Delete the cached
nightly asset in `build/windows/downloads` and use `--rebuild` to refresh it.

Use `--threads` to enable `STAN_THREADS`, `--optimization Od` to diagnose
compiler failures, and `-j N` for parallel SUNDIALS C compilation. `--stanc-flag=...`
passes an option to stanc. Heavy C++ compilation is deliberately sequential
in this first version. CmdStan utilities are placed in `bin`, the compiled model
beside its `.stan` file, and the corresponding `tbb.dll` beside each executable.
The Microsoft Visual C++ runtime must also be available at runtime. Use a separate
checkout from a GCC/MinGW build: the outputs and adjacent TBB DLL have the same
names but different ABIs. This is an experimental CPU build path; GNU Make and
CmdStanPy integration, MPI, OpenCL, user-header flags, precompiled headers and
full native test-suite support are not included yet.


#### Upstream CmdStan interface tests (native Windows)

Use the same test sources, models and GoogleTest assertions as the existing
Jenkins interface test stage, without GNU Make:

```bat
python runCmdStanTests.py --native --compiler clang-cl --toolchain portable -j2 src/test/interface
python runCmdStanTests.py --native --compiler cl --toolchain official src/test/interface/log_prob_test.cpp
```

With `--native`, the existing runner delegates compilation to the native
Windows build. Model prerequisites come from `make/tests`; GoogleTest comes
from the checked-out Stan Math submodule. Results are written to
`test/interface/**/*.xml` (and the test executables use the same target paths
as the Make-based runner). Tests run sequentially because they share output
files. `-j` controls the SUNDIALS build; C++ tests and models are currently
compiled sequentially. Do not run different compiler configurations in the
same checkout concurrently. Use separate worktrees for parallel configurations.
The compiler, toolchain and `--threads` options match `windows_build.py`.
Without `--native`, the existing runner is unchanged.

These are CmdStan's CPU interface tests; the upstream MPI and OpenCL test
files are feature-gated and contain no active tests in this configuration.
Stan and Stan Math have separate test runners and are not covered by this
command. The GitHub Actions Windows matrix runs the upstream CPU suite for
all four native compiler/toolchain profiles.
