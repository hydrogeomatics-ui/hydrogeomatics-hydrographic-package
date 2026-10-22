# Contributing to HYDROGEOMATICS

Surveyors, students, researchers, developers, and documentation contributors are welcome. Be respectful, explain assumptions, and keep technical disagreements constructive.

## Report a bug or suggest an improvement

Search existing issues first. For a bug, include your operating system, Python and package versions, steps to reproduce, expected behavior, actual behavior, and sanitized error text.

For calculation or conversion changes, describe units, coordinate reference systems, horizontal/vertical datums, corrections, and the independently expected result. Use a small synthetic example. Never attach operational survey data, private locations, client material, credentials, or unredacted logs/screenshots. Propose larger changes in an issue first.

## Make a contribution

1. Fork the repository and clone your fork.
2. Create a virtual environment and install `requirements.txt` as described in the README.
3. Enable local checks: `git config core.hooksPath .githooks`.
4. Create a branch and make one focused change. Update affected documentation.
5. Check changed Python files for syntax and run meaningful checks for the behavior you changed. Record results and anything you could not test.
6. Stage only reviewed files, then run `python scripts/check_publication.py`.
7. Push your branch and open a pull request against `main`.

The `review` CI check is required on the protected main branch. A passing publication check does not establish scientific accuracy or instrument compatibility. Numerical changes need evidence against independently known results; GUI changes need a description of the workflow checked. The public distribution does not yet have a comprehensive automated or field-validation suite.

## Adding files safely

The versioned single-file edition is generated, not edited directly. After changing the launcher or a source module, run `python scripts/check_publication.py --build-single-file`, stage the result, and run the normal publication check. The scanner verifies that the indexed single-file content exactly matches the indexed public modules; large embedded strings remain blocked in other files.

The default-deny `.gitignore` and exact `ALLOWED` list in `scripts/check_publication.py` restrict tracked files. For a legitimate new source, documentation, or synthetic test file, add its exact path to both controls in the same pull request and explain why it is safe to publish. Do not allow all data files, binary archives, or an entire directory. Never reintroduce opaque embedded datasets or the original merged script.

Keep local datasets and output outside the checkout. Enable hooks after each clone. Review the diff and every outgoing commit; CI runs after the upload reaches GitHub.

## Code and documentation

Keep changes readable, describe domain assumptions, avoid unrelated formatting, and preserve useful attribution. Prefer isolating calculations from UI callbacks. State units explicitly and describe conversions. New dependencies need a clear purpose and compatible licensing.

The integration uses dynamically loaded source modules and a Qt widget compatibility layer. Read the relevant loading path before moving functions between files.

## Contribution license

By submitting a contribution, you agree to license your original contribution under the project's MIT License. Submit only material you are entitled to share and identify third-party material and its license. Separately supplied data is not automatically covered by the code license.

For security or accidental-disclosure reports, follow SECURITY.md instead of posting sensitive details publicly.
