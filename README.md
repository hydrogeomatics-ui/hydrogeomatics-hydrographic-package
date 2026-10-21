# HYDROGEOMATICS Lowrance Converter

Python desktop survey and conversion application, prepared from the supplied single-file application.
The embedded code has been extracted into readable modules for inspection.

## Run

Python 3.10 or newer is required. Use a virtual environment, then run:

```sh
python -m pip install -r requirements.txt
python lowrance_converter.py --check-install
python lowrance_converter.py
```

Dependencies are inherited from the source instructions and are not version locked.
The complete GUI and instrument workflows have not been validated for this public distribution.

## Public distribution changes

- Personal support email and phone contacts were removed.
- Embedded protected map/background resources were removed.
- The Indus reference-data editor and tide/astronomy module are disabled because they bundle geographic, harmonic, and city datasets whose publication status is unconfirmed.
- No original input file, logs, survey files, or reference datasets are included.
- Original application terms and branding are retained. No new open-source license is granted by this packaging work.

## Upload controls

```sh
git config core.hooksPath .githooks
python scripts/check_publication.py
```

The default-deny `.gitignore`, exact tracked-file allowlist, local hooks, and CI check reduce accidental uploads. Keep operational data outside this repository. New source files require explicit allowlist review.
Do not upload the original merged script: its compressed datasets bypass a superficial text review.

See SECURITY.md for limits and GitHub settings.
