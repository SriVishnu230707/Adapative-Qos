# Third-party dependency inventory

The following versions were resolved for phase 3. License identifiers are taken from the installed distributions' metadata, except where the metadata is explicitly incomplete. This repository does not vendor their source or binary wheels. Preserve upstream notices and review distribution obligations when packaging the final project.

| Dependency | Version | Declared license / scope |
| --- | --- | --- |
| pydantic | 2.14.2 | MIT; runtime validation |
| pydantic-core | 2.50.2 | MIT; runtime validation engine |
| PyYAML | 6.0.3 | MIT; configuration parser |
| annotated-types | 0.8.0 | MIT; runtime dependency |
| typing-extensions | 4.16.0 | PSF-2.0; runtime dependency |
| typing-inspection | 0.4.5 | MIT; runtime dependency |
| pytest | 9.2.0 | MIT; test tooling |
| build | 1.6.1 | MIT; build tooling |
| setuptools | 82.0.1 | MIT; build backend |
| wheel | 0.48.0 | MIT; build tooling |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause; tooling dependency |
| pluggy | 1.7.0 | MIT; test dependency |
| iniconfig | 2.3.1 | MIT; test dependency |
| Pygments | 2.21.0 | BSD-2-Clause; test output dependency |
| pyproject-hooks | 1.3.3 | MIT; build dependency |
| colorama | 0.4.6 | Windows test dependency; SPDX field absent from wheel metadata, consult its bundled LICENSE.txt |
| pycdlib | 1.22.0 | LGPL-2.1-only; optional VM seed-ISO helper, outside application runtime |

Ubuntu, Linux, VirtualBox and the system networking utilities remain separately installed prerequisites; VM images and system binaries are not committed. Their versions and acquisition sources are recorded in the setup guide/environment manifest. A complete release/distribution review remains part of the final packaging phase. No project license has been selected on the owner's behalf by this inventory.
