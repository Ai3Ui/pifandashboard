# Security Policy

## Never commit
Do not commit passwords, API keys, bearer tokens, private keys, recovery material, SSH credentials, device credentials, Wi-Fi credentials, session secrets, or unredacted production configuration.

## Runtime safety
This project can control hardware and install system services. Changes to fan control, privilege boundaries, authentication, installation, uninstall behaviour, or systemd units must preserve safe defaults and a rollback/uninstall path.

Do not weaken authentication, validation, permissions, or service isolation merely to simplify setup or make tests pass.

## Reporting
Validate suspected vulnerabilities privately. Avoid publishing credentials, private infrastructure data, or exploit details before appropriate remediation or coordinated disclosure.
