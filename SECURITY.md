# Publication controls

Only the reviewed code and configuration files belong in this repository. Never attach survey data, operational coordinates, client material, reference datasets, settings, credentials, or debug logs to issues or releases.

The local scanner checks the Git index, including Python string literals, for selected credential and personal-information patterns. It blocks large embedded strings and non-allowlisted files. Hooks must be enabled after every clone and can be bypassed; CI detects a problem only after content has reached GitHub. These controls cannot guarantee the absence of sensitive information.

The generated versioned single-file edition is permitted only when it exactly matches a deterministic bundle of the indexed public launcher and modules. Its source inputs undergo the normal checks. This exception does not permit arbitrary embedded data or changes to the generated file; rebuild it after changing reviewed source modules.

Before the first push, verify that GitHub secret scanning and push protection are enabled. Require the Publication safeguards check through a branch ruleset where the account supports it. GitHub credential detection does not identify every form of private survey or geographic data.

GitHub guidance: https://docs.github.com/en/code-security/how-tos/secure-your-secrets/detect-secret-leaks/enable-secret-scanning

The retained protected-file format uses a deterministic key derived from public application values and XOR. Treat that format as obfuscation, not confidentiality protection. No protected payloads are distributed here.

If a credential is ever published, revoke or rotate it immediately. Removing a file from the latest commit does not remove it from history, forks, or existing clones. Review every outgoing commit before pushing.

## Report a vulnerability privately

Use **Security > Report a vulnerability** in the GitHub repository to submit a private advisory. Do not open a public issue containing an exploit, credentials, or private data. Provide a minimal synthetic reproduction and affected commit/version. There is no guaranteed response time. For an exposed credential, revoke or rotate it immediately.
