# Changelog

One section per released version, newest first. `CONTRIBUTING.md`, "Releases", says how a
version is numbered and when this file is written.

## 0.2.0

**Upgrading.** Pull the new image and start it on the same volume. A deployment set up as
0.1.0 documents has nothing else to do: the first start adds two tables and changes no
existing one, and no setting, REST route or MCP tool that 0.1.0 had is renamed or removed.
One thing to check first: this version reads six environment variables that 0.1.0 ignored,
`GW_RELAY_URL`, `GW_RELAY_TOKEN`, `GW_OPERATOR_BACKUP` and the three `GW_TLS_` names in
change 22 below, and it refuses to start, naming the variable, on any other name that
begins `GW_TLS_`. If your environment already carries one of them, remove it unless you
mean what it now does.

- 7: the README's run line pulls the published image, where it used to build one from
  source.
- 9: a workspace run by a hosting control plane signs people in with a six-digit code sent
  to their email address, and its administrators invite people by email. This is on only
  when `GW_RELAY_URL` and `GW_RELAY_TOKEN` are both set, and password sign-in is then off.
  A self-hosted workspace sets neither and keeps passwords or its own identity provider.
- 11: the image runs one process whatever `WEB_CONCURRENCY` says, and `docs/DEPLOYMENT.md`
  says what pausing a container does to a workspace. Indexing is unaffected by a pause.
- 20: where a deployment sets `GW_OPERATOR_BACKUP=true`, the operator token takes a backup
  at `POST /api/v1/operator/backup`. It is off by default, and the token opens nothing else
  new. No backup holds a usable sign-in code, and an abandoned backup download leaves no
  copy of the database on the volume.
- 22: a workspace can terminate TLS itself and answer only a caller whose client
  certificate chains to a named authority, with `GW_TLS_CERT_FILE`, `GW_TLS_KEY_FILE` and
  `GW_TLS_CLIENT_CA_FILE`, all three or none. With none it serves plain HTTP behind your
  own proxy, as before.
- 26: each release also moves the `latest` tag to the newest version in both registries, so
  `docker.io/glosswork/glosswork:latest` is this version until the next release. A
  published version is still never replaced, and a deployment that should stay put names
  the version it runs.

## 0.1.0

The first release. Everything before it was built in a private repository, so this
version has no list of earlier changes.

- 1: versioned images publish to the GitHub registry and Docker Hub, as
  `ghcr.io/glosswork/glosswork:0.1.0` and `docker.io/glosswork/glosswork:0.1.0`, for
  `linux/amd64` and `linux/arm64`.
