# HYDROGEOMATICS

### An open-source hydrographic package, built for the community

[![Publication safeguards](https://github.com/hydrogeomatics-ui/hydrogeomatics-hydrographic-package/actions/workflows/security.yml/badge.svg)](https://github.com/hydrogeomatics-ui/hydrogeomatics-hydrographic-package/actions/workflows/security.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

HYDROGEOMATICS brings survey planning, hydrographic data handling, mapping, processing, and conversion tools into a Python desktop application. It is shared for hydrographers, surveyors, students, researchers, and developers to inspect, learn from, test, and improve together.

**Our aim: make everyday hydrographic workflows easier to understand, reproduce, and extend.**

## Why this project exists

Hydrographic work brings together instruments, positioning, coordinate systems, water levels, file formats, and quality checks. Moving between those steps can be difficult, especially when learning the subject or connecting tools from different vendors.

This project makes the implementation available so the community can examine calculations, improve interoperability, share reproducible fixes, and build useful tools together. Contributions can be code, documentation, field observations, translations, or carefully prepared test cases. You do not need to be a programmer to help.

## What it does

The source includes the following workflows. Availability depends on dependencies, instrument protocols, and input formats; these workflows have not all been independently field validated.

| Area | Tools in the package |
| --- | --- |
| Survey planning | Parallel runline generation, line selection, and navigation calculations |
| Positioning and acquisition | NMEA/GNSS parsing and displays, serial/network acquisition helpers, SBES logging, and MBES raw packet logging |
| Recorded sonar conversion | Supported `.SL2`/`.SL3` recordings decoded through `sonarlight`, with positions and recorded depths exported to CSV |
| Data processing | Sounding CSV processing, draft/water-level/sound-speed correction settings, and XYZ/GeoJSON export helpers |
| Mapping | Map/CAD interface, DXF and KML/KMZ workflows, ENC-related tools, and coordinate transformation helpers |
| Exchange and review | HYPACK-style RAW export, NMEA reports, GPX tracks, and echogram output |
| Dredging tools | Local browser interface for channel design, cross sections, and volume calculations |

Recorded sonar CSV output contains `latitude`, `longitude`, `depth_m`, `time_utc`, and `channel`. Coordinates remain latitude/longitude in decimal degrees. Verify decoded units, timestamps, datums, and sensor offsets for your recording and project.

## Project status

This is an early community release extracted from a larger single-file application. The source is in readable modules, but substantial cleanup, documentation, and testing remain. There are no packaged installers or claims of certified instrument, navigation, or file-format compatibility.

Embedded geographic reference datasets and protected map resources are excluded from the public distribution. The Indus reference-data editor and tide/astronomy module are disabled because their datasets have not been cleared for public distribution. No operational survey data is bundled.

## Get started

**Download the full package:** [Download ZIP](https://github.com/hydrogeomatics-ui/hydrogeomatics-hydrographic-package/archive/refs/heads/main.zip), then **extract all files**. Keep `hydrographic_package.py`, `requirements.txt`, and the `modules` folder together. Downloading just the Python launcher will not work. Run from the extracted folder, not from inside the ZIP viewer.

Use Python 3.10 or newer and Git. The desktop interface uses PyQt6. The public distribution has not yet been validated across all operating systems.

Git is only needed for the clone method below; ZIP downloads do not require Git. Open a terminal in the extracted folder and follow the virtual-environment and installation steps. After setup, Windows users can double-click `start_windows.cmd`; it keeps startup errors visible.

```sh
git clone https://github.com/hydrogeomatics-ui/hydrogeomatics-hydrographic-package.git
cd hydrogeomatics-hydrographic-package
python -m venv .venv
```

Activate the environment on Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Or on Linux/macOS:

```sh
source .venv/bin/activate
```

Install and check the runtime, then launch:

```sh
python -m pip install -r requirements.txt
python hydrographic_package.py --check-install
python hydrographic_package.py
```

The installation check verifies imports; it does not test instruments or validate results. Dependencies are not yet version locked. Some ENC workflows need additional system libraries such as GDAL.

### Startup troubleshooting

- **Download is incomplete / missing modules:** extract the complete ZIP and keep its folder structure intact.
- **Missing dependency / `ModuleNotFoundError`:** activate the intended environment and run `python -m pip install -r requirements.txt` using that same Python.
- **The window closes immediately on Windows:** use `start_windows.cmd` or run `python hydrographic_package.py` in a terminal to read the error.

To convert recorded sonar, find **Recorded sonar conversion (.SL2 / .SL3)** under **Professional Processing**, select a recording and output CSV, and choose the relevant channel. Device variants and damaged recordings may not decode successfully.

Other entry points:

```sh
python hydrographic_package.py --help
python hydrographic_package.py --dredge-volume
```

The dredging tool starts a local browser interface. Keep its terminal open while using it. Store input data and generated files outside the repository.

## Help shape the package

Start with [CONTRIBUTING.md](CONTRIBUTING.md), browse the [issues](https://github.com/hydrogeomatics-ui/hydrogeomatics-hydrographic-package/issues), or propose a focused improvement.

Useful starting points include:

- Test installation on different systems and document reproducible fixes.
- Validate conversions against known, non-confidential reference results.
- Improve device coverage, NMEA handling, units, and error messages.
- Add synthetic test cases for coordinate transformations and sounding corrections.
- Separate processing logic from the GUI and reduce shared state.
- Improve tutorials, accessibility, terminology, and translations.

These are areas where help is welcome, rather than a release schedule. Discuss larger changes in an issue before implementing them.

## Source layout

| Path | Purpose |
| --- | --- |
| `hydrographic_package.py` | Desktop launcher, module integration, installation checks |
| `modules/survey_source.py` | Survey workflows, processing, CLI, recorded sonar conversion |
| `modules/map_source.py` | Map/CAD interface and supporting data tools |
| `modules/engine_source.py` | Acquisition and instrument integration helpers |
| `modules/qt_widget_source.py` | Qt compatibility layer for retained survey widgets |
| `modules/chart_plot_source.py`, `modules/enc_editor_source.py` | Chart plotting and ENC editor tools |
| `modules/dredge_volume_source.py` | Local channel design and volume interface |
| `scripts/check_publication.py` | Tracked-file publication checks |

Some modules are loaded dynamically by the launcher. Run the launcher rather than starting individual modules directly.

## Responsible use and safe contributions

This is a survey and processing aid, supplied without warranty. It is not a certified navigation system or an official chart. Independently verify observations, coordinate reference systems, horizontal and vertical datums, units, corrections, and exported results before operational use.

Never publish client data, sensitive survey locations, credentials, instrument logs, or personal information in commits, issues, screenshots, or pull requests. Use synthetic examples. File restrictions, local hooks, CI checks, and GitHub secret scanning/push protection reduce accidental disclosure but cannot detect every kind of sensitive information. See [SECURITY.md](SECURITY.md).

## License and acknowledgments

Hydrogeomatics project code and documentation are released under the [MIT License](LICENSE), copyright Hydrogeomatics and contributors. Contributions are welcome under the same license.

The package builds on the Python scientific and geospatial ecosystem. Dependencies, third-party material, trademarks, and separately supplied datasets retain their own licenses and attribution requirements. PyQt6 uses GPLv3 or a commercial license; distributing a combined application requires complying with the applicable dependency terms. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

If this project helps your work, share a reproducible improvement or help someone get started. That is how the package grows.
