# Security

Aezab is under active development. Supported behavior and deployment limits are documented in [deployment.md](docs/deployment.md) and [business-data.md](docs/business-data.md). This repository does not claim a security certification or an enterprise HA guarantee.

If you find a vulnerability, do not include working credentials, customer records or an exploit against a live deployment in a public issue. Use GitHub's private vulnerability reporting option if it is available on this repository. Otherwise, open a minimal issue asking the maintainer for a private reporting channel before sharing sensitive details. There is currently no published response SLA.

Useful reports include the affected revision, deployment mode, caller role/key scope, a synthetic reproduction and the expected authorization boundary. In particular, report cross-tenant access, credential exposure, unauthorized external actions and ways to bypass bounded workflow execution.

Operators should run with authentication enabled, restrict exposed network endpoints and use TLS at the reverse proxy. An `invoke` key grants use of its tenant's bound agent capabilities; it is not end-user identity. Receiving business services must enforce customer/employee permissions. PostgreSQL source credentials should be read-only and limited to approved tables or views. Backups contain sensitive records and may contain the local encryption key; protect both storage and restore access.
