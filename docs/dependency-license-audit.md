# Phase 0 dependency licence inventory

Inspected 2026-11-15 on the local Linux / Python 3.14.4 environment.

**Result / Recommendation:** retain the current dependency choices for the
source-only, separately installed dependency workflow. No replacement is
recommended before phase 1. This is a dependency inventory and packaging
boundary check, not a certification of every possible redistribution.

## Scope and evidence

Read `pyproject.toml`, `uv.lock`, installed distribution metadata and licence
files, and the local Gmsh/KiCad copyright notices. Upstream licensing pages
were also checked for Gmsh, KiCad, Shapely/GEOS, Colorama and Hatchling.
The table lists the installed versions actually inspected; it does not claim
that every platform-specific wheel in the lockfile was inspected.

The declared runtime dependencies are meshio, NumPy, SciPy and Shapely.
Matplotlib and pytest are development dependencies. Other entries below are
installed transitive dependencies. pip and the project's own editable install
are environment tooling/project metadata, not additional project dependencies.

## Installed distributions

| Distribution | Installed version | Role | Licence / notices |
| --- | --- | --- | --- |
| contourpy | 1.4.0 | transitive | BSD-3-Clause |
| cycler | 0.12.1 | transitive | BSD-3-Clause |
| fonttools | 4.68.0 | transitive | MIT; external font notices include OFL-1.1 |
| iniconfig | 2.3.1 | transitive | MIT |
| kiwisolver | 1.5.1 | transitive | BSD-3-Clause |
| markdown-it-py | 4.3.0 | transitive | MIT |
| matplotlib | 3.11.2 | development | Matplotlib licence (PSF-based), with third-party notices |
| mdurl | 0.1.2 | transitive | MIT |
| meshio | 5.3.5 | runtime | MIT |
| numpy | 2.5.4 | runtime | BSD-3-Clause project; composite metadata and bundled notices (below) |
| packaging | 26.3 | transitive | Apache-2.0 OR BSD-2-Clause |
| pillow | 13.0.0 | transitive | MIT-CMU project; bundled third-party notices |
| pluggy | 1.7.0 | transitive | MIT |
| pygments | 2.21.0 | transitive | BSD-2-Clause |
| pyparsing | 3.3.3 | transitive | MIT |
| pytest | 9.2.0 | development | MIT |
| python-dateutil | 4.0.0 | transitive | BSD-3-Clause |
| rich | 15.0.0 | transitive | MIT |
| scipy | 1.18.1 | runtime | BSD-3-Clause project; bundled notices (below) |
| shapely | 2.2.0 | runtime | BSD-3-Clause; bundled GEOS LGPL-2.1 |
| tzdata | 2026.6 | transitive | Apache-2.0 project; retain bundled timezone notices |

## Native and third-party notices

- NumPy's installed metadata expression is
  `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`.
  Its additional licence files must be retained if redistributing that package.
- The installed NumPy and SciPy top-level licence files both identify bundled
  OpenBLAS (BSD-3-Clause), LAPACK (BSD-3-Clause-Open-MPI), GCC runtime
  (`GPL-3.0-or-later WITH GCC-exception-3.1`) and libquadmath
  (`LGPL-2.1-or-later`). They are not all covered by the Python project's
  BSD licence. Their notice sections are included in the hashes below.
- Shapely's installed `LICENSE_GEOS` identifies bundled GEOS under LGPLv2.1.
  The LGPL component must not be described as BSD-only.
- Matplotlib and Pillow licence files contain additional third-party notices.
  FontTools has a separate external-licence file that includes OFL-1.1 font
  notices. The table's project licence does not replace those notices.

## External programs and distribution boundary

| Program | Locally reported version | Licence evidence | Current use |
| --- | --- | --- | --- |
| Gmsh | 4.14.0-git | Local copyright file and upstream: GPL-2.0-or-later with the Gmsh linking exception | External executable; geometry/mesh files exchanged |
| KiCad CLI | 10.0.8 | Current upstream program licence: GPL-3.0-or-later, with third-party notices | External executable; IPC-2581 export read |

A source search found no `gmsh`, `pcbnew`, padne or KiPI imports in `src/` or
`experiments/`. `pyproject.toml` does not declare them as Python dependencies.
The installed KiCad Debian copyright text still describes an older GPL-v2-era
release, so it is not sufficient evidence for the current KiCad version;
the current upstream licensing page is used for that row instead.

PDNLab's own code remains Apache-2.0. Recommendation: keep Gmsh and KiCad
separately installed and invoked by process/file interfaces; do not copy or
link their code into the solver. This inventory does not assert that every
file-based interaction automatically settles all licensing questions.

No dependency wheel or third-party executable is added to this repository by
this closeout. Before distributing a bundled application, installer or
container image, review that exact artifact's notices, source/relinking
requirements for LGPL components, runtime exceptions and component versions.
That future packaging review is separate from the phase-0 source workflow.

## Other environments and future dependencies

- `uv.lock` contains Python-version alternatives for NumPy, SciPy and
  ContourPy. Their uninstalled versions were not inspected locally.
- Colorama 0.4.6 is a Windows-conditional locked dependency and is not
  installed here. Upstream identifies BSD-3-Clause; its wheel was not read.
- Hatchling is the declared build backend. It is not installed in the active
  environment; its published project metadata identifies MIT. The isolated
  build environment and its transitive closure were not inventoried here.
- PyAMG, scikit-fem, Typer, Jinja2, WeasyPrint, PyVista, VTK and PySide6 are
  future plan entries, not current declared dependencies. Audit them when
  adding them; this inventory does not approve future binary bundles.
- No padne or KiPI code is incorporated. KiPI was dropped as a required
  reference run by the maintainer. A source-provenance/clone-detection audit
  of all project code was not performed.

## Upstream references checked

- [Gmsh licensing](https://gmsh.info/#Licensing)
- [KiCad program licences](https://www.kicad.org/about/licenses/)
- [Shapely documentation: licence](https://shapely.readthedocs.io/en/stable/)
- [GEOS licence](https://libgeos.org/)
- [Colorama licence](https://github.com/tartley/colorama/blob/master/LICENSE.txt)
- [Hatchling published metadata](https://pypi.org/project/hatchling/)

## Local licence evidence hashes

Paths below are relative to the installed Python site-packages directory.
SHA-256 values identify the licence text inspected, not a whole wheel or a
package source archive. Regenerate the inventory when dependency versions
change. Full upstream licence texts remain with their installed packages.

| Licence file | SHA-256 |
| --- | --- |
| `contourpy-1.4.0.dist-info/licenses/LICENSE` | `11401c428c7014a75728e8679f9c000aacf262015aaba65a760d41aa1130b3af` |
| `cycler-0.12.1.dist-info/LICENSE` | `f1218143d766da3fea66f13396b7f15df46a83303f29bf96ba6e98eb4d42f408` |
| `fonttools-4.68.0.dist-info/licenses/LICENSE` | `6787208f83f659ccbc2223b2fde952ffa6f7e8aca62f1a8a2bf5bc51bb1b2383` |
| `fonttools-4.68.0.dist-info/licenses/LICENSE.external` | `94a83aaee0729a0f302d34acc4acecbd9d58366f262429075fe557e4a54b2e69` |
| `iniconfig-2.3.1.dist-info/licenses/LICENSE` | `3409fa91f7ace557894632676656e32264fe5ef7581535725dc9a23774551bd4` |
| `kiwisolver-1.5.1.dist-info/licenses/LICENSE` | `529c40e5f67f2f88904657a9f7879ae2f8dc76bc9bfef9cb10d988b48804ed61` |
| `markdown_it_py-4.3.0.dist-info/licenses/LICENSE` | `4a2260d6e2cd0f5a151a1e86dbfe7d3ed552b1e2beabf9941c1ba5c49cbce484` |
| `markdown_it_py-4.3.0.dist-info/licenses/LICENSE.markdown-it` | `792c48c5a849a15fdf9e37e8bcf9e6d1dd13b32b46c642a748a0a46a9919d473` |
| `matplotlib-3.11.2.dist-info/LICENSE` | `822e8e528147569a41975592aee19c11992ab667ba50451cd929031d5fc74491` |
| `mdurl-0.1.2.dist-info/LICENSE` | `7c605df6e28667a9603118e98274f64a49ce3eed0d26fccce9534a345e0ef955` |
| `meshio-5.3.5.dist-info/LICENSE.txt` | `3152e464fdd307cac91bf9668c66f86cb8e02cd40d5b48a8da1a241ae1264e79` |
| `numpy-2.5.4.dist-info/licenses/LICENSE.txt` | `4860083caa0de2ac3292ca98bd074bd8f45d8b32624e37b1e70a240bff61e488` |
| `numpy-2.5.4.dist-info/licenses/numpy/_core/include/numpy/libdivide/LICENSE.txt` | `fbc539f47d0cf83bc61378080fb873d5c14630126cacbfe754035c3926daa5ec` |
| `numpy-2.5.4.dist-info/licenses/numpy/_core/src/common/pythoncapi-compat/COPYING` | `3f2c1ffa76383a55ae0aac01c9737cd70050dff63e1f026694fbe9d662573c98` |
| `numpy-2.5.4.dist-info/licenses/numpy/_core/src/highway/LICENSE` | `e340270d4f64384569a91d546acb5b094d69ce47f0c015db77abb74dc6f815af` |
| `numpy-2.5.4.dist-info/licenses/numpy/_core/src/multiarray/dragon4_LICENSE.txt` | `f7b14309e5f54e5022b26448eefd419988ada2aec01d3c1162d84a0ea8f21f2c` |
| `numpy-2.5.4.dist-info/licenses/numpy/_core/src/npysort/x86-simd-sort/LICENSE.md` | `211e7d737d6179a08f791ebb0a99897ce7cd2457410c542876d125576edfe910` |
| `numpy-2.5.4.dist-info/licenses/numpy/_core/src/umath/svml/LICENSE` | `c17011467b4fe6e20fbfbeda4df09e58c2bca5480a063e55735a4000a379aad9` |
| `numpy-2.5.4.dist-info/licenses/numpy/fft/pocketfft/LICENSE.md` | `a85ca13fdf90160b64a0698215868c13b74d835ad0a4e2ba44713b8c5058a056` |
| `numpy-2.5.4.dist-info/licenses/numpy/linalg/lapack_lite/LICENSE.txt` | `a14cc25e10d40a3aa705b7de2fb764a6535d8ee9b2db4e1724900585457dfd55` |
| `numpy-2.5.4.dist-info/licenses/numpy/ma/LICENSE` | `05f3b88351988ecfad10abe92c0c50e5875c6452d5009a0084cc291551ffcca6` |
| `numpy-2.5.4.dist-info/licenses/numpy/random/LICENSE.md` | `103166b62b80443afb9eb3488e052ea06be0cff566b908f199e561fde49af19f` |
| `numpy-2.5.4.dist-info/licenses/numpy/random/src/distributions/LICENSE.md` | `469778be8f33d54e1bcaab28c281cc472916d2d273f835e55bc487d30503face` |
| `numpy-2.5.4.dist-info/licenses/numpy/random/src/mt19937/LICENSE.md` | `0e111337b25494a773106695f0e799f642b8567aaacca4f116a74a8177179156` |
| `numpy-2.5.4.dist-info/licenses/numpy/random/src/pcg64/LICENSE.md` | `e7d4f4fb20358f314210a892ae17351e04993813646f697ec387385ca2a36918` |
| `numpy-2.5.4.dist-info/licenses/numpy/random/src/philox/LICENSE.md` | `6f5828a57223d4cc7f8ea505a2623e7b978b242752c1515e355f46ba2cec1800` |
| `numpy-2.5.4.dist-info/licenses/numpy/random/src/sfc64/LICENSE.md` | `9d4098846018319cd68e228ee6220361787f73bccbf00237a85e4fc9621b998f` |
| `numpy-2.5.4.dist-info/licenses/numpy/random/src/splitmix64/LICENSE.md` | `268b0ca06759d31ea3ca68dd35f20e877916e927b1c2a835e4592a3c4354b1f9` |
| `packaging-26.3.dist-info/licenses/LICENSE` | `cad1ef5bd340d73e074ba614d26f7deaca5c7940c3d8c34852e65c4909686c48` |
| `packaging-26.3.dist-info/licenses/LICENSE.APACHE` | `0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594` |
| `packaging-26.3.dist-info/licenses/LICENSE.BSD` | `b70e7e9b742f1cc6f948b34c16aa39ffece94196364bc88ff0d2180f0028fac5` |
| `pillow-13.0.0.dist-info/licenses/LICENSE` | `dda12a98c1979cf3d94df1cff45d27a4cb3f04a60c76f76902ac54cac03ec0ce` |
| `pluggy-1.7.0.dist-info/licenses/LICENSE` | `d6b65e6c213a5d0b577911d34d6e5949b9f59d76c238c5071a2f3fc16cfb2606` |
| `pygments-2.21.0.dist-info/licenses/AUTHORS` | `71a83872ad82f57be692cf827e048e2cd51d45169cfa1ea3f28706d97a872c0f` |
| `pygments-2.21.0.dist-info/licenses/LICENSE` | `a9d66f1d526df02e29dce73436d34e56e8632f46c275bbdffc70569e882f9f17` |
| `pyparsing-3.3.3.dist-info/licenses/LICENSE` | `a5425f9dc14ac74d4c5f0b679e941f2442e32cca7452a4418d5b1a49893ebe4e` |
| `pytest-9.2.0.dist-info/licenses/LICENSE` | `ca836a5f9ecca3b2f350230faa20a48fb8b145653b5568d784862df864706b9b` |
| `python_dateutil-4.0.0.dist-info/licenses/AUTHORS.md` | `56004c45a95e9ee5582ea60f9a7ee42179c8594c7b9c245bbfb4a3445b4af640` |
| `python_dateutil-4.0.0.dist-info/licenses/LICENSE` | `ba00f51a0d92823b5a1cde27d8b5b9d2321e67ed8da9bc163eff96d5e17e577e` |
| `rich-15.0.0.dist-info/licenses/LICENSE` | `deed7c17a4318158190a3ea239cc879a5a50271cebb98ae7025f48fbe58dca15` |
| `scipy-1.18.1.dist-info/LICENSE.txt` | `17e7db9ffa4913121fa2df484633a362456a482da736c99db4711a98473770ea` |
| `shapely-2.2.0.dist-info/licenses/LICENSE.txt` | `4a207eac9bf257ed286e1c6f0fce253d1abfdb134fc61ec855e37f5692a3f754` |
| `shapely-2.2.0.dist-info/licenses/LICENSE_GEOS` | `81d9aaf9ab7e3e6184568a70f27ee3e138d8d1e543b46dad5af88cff61cf1b36` |
| `tzdata-2026.6.dist-info/licenses/LICENSE` | `df58f69ea88683035d720b5375bcef0cf5aeaa98a1d098d572e0992c378deb46` |
| `tzdata-2026.6.dist-info/licenses/licenses/LICENSE_APACHE` | `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` |
