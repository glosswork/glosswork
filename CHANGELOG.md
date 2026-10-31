# Changelog

One section per released version, newest first. `CONTRIBUTING.md`, "Releases", says how a
version is numbered and when this file is written.

## 0.3.0

**Upgrading.** Pull the new image and start it on the same volume. No setting, REST route or MCP
tool that 0.2.0 had is renamed or removed. Two things behave differently:

- A date in a filter must now be exact, for every caller. A date filter that used to answer with
  rows, compared as text, can now answer `validation_failed`, 422. That covers `query_records`,
  `search`, `bulk_update_records`, CSV export and saved views. Use `YYYY-MM-DD`,
  `YYYY-MM-DDTHH:MM:SSZ` or a token such as `@today-7d`. If an agent or script builds date filters,
  check what it sends.
- People & agents, Activity, Schema and Setup moved under `/workspace`. Old links still work: they
  redirect, unless an object type has that key, in which case they open its table.

- 13: the View menu and every other popover stay inside the window.
- 14: unsaved filters and other table changes survive leaving a table and coming back, in the same
  tab.
- 15: a date filter refuses what it cannot read and says why, and a refused filter shows no rows. It
  never keeps rows it did not produce.
- 16: the sidebar and the table toolbar stay in view while a long table scrolls.
- 17: a multi-value text filter keeps what is typed.
- 18: columns can be pinned, and the pins are saved with a view.
- 19: a CSV upsert clears a field from a blank cell only when `blank_clears` is set, and reports what
  it cleared.
- 28: the backend tests run as four shards in CI. Nothing changes for anyone running the product.
- 30: where a hosting operator sets `GW_TRIAL_ENDS_AT`, a workspace on trial shows the time left and
  a subscribe link, and says when the trial has ended. A self-hosted workspace sets nothing and sees
  nothing new.
- 32: an object type's table is reachable when its key matches one of those four screens or the
  older `audit` and `settings` addresses. The keys `inbox`, `search` and `login` are still taken.

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
