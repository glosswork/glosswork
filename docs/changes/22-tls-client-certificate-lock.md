# 22: A workspace can terminate TLS itself and refuse any caller without the right client certificate

| | |
| --- | --- |
| Issue | [#22](https://github.com/glosswork/glosswork/issues/22) |
| Branch | `22-tls-client-certificate-lock` |
| Spec | PRD.md FR-P1, FR-P3, FR-P7; docs/DEPLOYMENT.md sections 2 and 4; `.env.example`; PLAN.md section 5.4 and Q84 |
| Decisions | One new decision, taking the next unused number (46 when this was written; text proposed below, and see P16 for why it is not cited by number here). DD-40 is read and not changed |
| Requirements | FR-P1, FR-P3, FR-P7; FR-P11 (new, text proposed below) |
| Depends on | Nothing. `main` at `42e9d45`, which carries change 11's `workers=1` in the entry point. Unblocks control-plane CP-33 and CP-07 |

## Why

A hosted workspace will sit behind one proxy that the hosting operator does not run on
the workspace's own host (PLAN 5.4, Q84, decided 2026-10-02). The workspace has a public
address, and the only caller that should ever reach it is that proxy. The proxy proves
who it is with a client certificate, so the workspace has to terminate TLS itself and
refuse, at the connection, anyone who does not hold a certificate from one named
authority.

Today the entry point serves plain HTTP and nothing else (P1). An operator who needs
this shape has to add a second process to the container, which breaks FR-P1, or leave
the workspace open and trust the proxy's address range for `X-Forwarded-For`, which lets
any direct caller choose the address the sign-in limiter sees (P9).

uvicorn can do the whole job with four arguments to the one call the entry point already
makes, using Python's own `ssl` module (P2, P3). This change adds three optional settings
that turn those arguments on, all three or none.

**This change is a security boundary and it adds a design decision.** CONTRIBUTING keeps
both out of delegation. "Judgment areas" sets out each choice, and the maintainer approves
that design in this plan before any code.

## Judgment areas this change reaches (CONTRIBUTING)

1. **A new design decision.** The workspace's own TLS is all or nothing and always carries
   the client-certificate lock. There is no setting for "TLS, but let anyone in", because
   a workspace that terminates TLS on a public address with no lock looks protected and is
   not, and because the half-configured states are silently open when handed to uvicorn
   as they are (P5). The proposed entry text is under "Durable content".

2. **The setting names.** The task gave working names: `GW_TLS_CERT_FILE`,
   `GW_TLS_KEY_FILE`, `GW_TLS_CLIENT_CA_FILE`. **This plan keeps them.** They fit what the
   repository already does: the `GW_` prefix that `Settings` applies, upper snake case, a
   noun group that sorts together (`GW_OIDC_*`, `GW_RELAY_*`, `GW_LOGIN_*`), and a suffix
   that says what kind of value it is, as `GW_DATA_DIR` and `GW_MODEL_DIR` do with `_DIR`
   (read in `src/glosswork/config.py:47` and `:115`). No existing setting names a single
   file, so `_FILE` is new, and it is the plain counterpart of `_DIR`. Considered and
   declined: `GW_SSL_*`, which matches uvicorn's argument names and no name in this
   repository; and `_PATH`, which does not say file or directory.

3. **What "refused at the handshake" means, exactly.** The server's handshake fails for
   every caller without a good certificate, on TLS 1.2 and on TLS 1.3, and uvicorn's HTTP
   layer is never given the connection (P4). The two versions look different from the
   client's side, and the plan and the documentation say so rather than paper over it:
   on TLS 1.2 the client's own handshake call fails; on TLS 1.3 the client's handshake
   call returns, because in that version the client finishes before the server has
   checked the client's certificate, and the connection is closed before one byte of a
   response is sent. In both, no HTTP request is read. The server sends no TLS alert and
   writes no log line for a refused caller.

4. **Startup refusals and their wording.** A partial set refuses startup. So does a
   named file that cannot be opened, which is one step past what the issue asks for and
   is in the plan for a measured reason: uvicorn's own failure for a missing file is a
   traceback that names neither the variable nor the path (P6). The maintainer can strike
   it. The copy is under "What changes".

5. **A new requirement (FR-P11) rather than an edit to FR-P7.** FR-P7 stays as it is: a
   self-hosted deployment still sits behind its own proxy. The proposed text is under
   "Durable content".

## Premises

Everything below was measured on 2026-10-02 against `main` at `42e9d45` (tree
`0093c24`), on a local image built from that tree (`docker image inspect` shows the
revision that is `42e9d45^2`, the merged branch's head, whose tree is the same
`0093c24`; `git rev-parse '42e9d45^2^{tree}' '42e9d45^{tree}'` prints it twice). Inside
that image: Python 3.13.16, OpenSSL 3.5.7, uvicorn 0.52.4, uvloop 0.22.1, httptools
0.8.0, process uid 1000. Docker 28.4.0 on arm64 macOS.

**The harness.** "The wrapper" is a copy of `src/glosswork/entrypoint.py` as it stands,
run as the container's entry point in place of the real one, that adds to the same
`uvicorn.run` call, from three environment variables with no validation:
`ssl_certfile`, `ssl_keyfile`, `ssl_ca_certs`, and `ssl_cert_reqs=ssl.CERT_REQUIRED`
when the CA is set. With none of the three set it adds nothing. "The probe" is a Python
`ssl` client pinned to one TLS version, with `suppress_ragged_eofs=False`, that reports
whether its handshake call returned and what the first read after sending
`GET /readyz` produced. Certificates were throwaway: a server CA and a server
certificate for `localhost`; client CA A and a leaf from it; client CA B and a leaf from
it. Containers were made with `docker create`, the files put in with `docker cp`, then
started, with port 8000 published on loopback. The scripts and their full output are
attached to the build task this change belongs to (PROD-60).

- **P1. The entry point serves plain HTTP and passes no TLS argument.** Read at
  `src/glosswork/entrypoint.py:19-43`: the application and eight keyword arguments, none
  starting `ssl_`.
  Run: the image with its own entry point logs
  `Uvicorn running on http://0.0.0.0:8000`, and `curl http://127.0.0.1:<port>/readyz`
  answers `200`.

- **P2. uvicorn 0.52.4 turns TLS on from a certificate file, and takes the client check
  from two more arguments.** Read at `uvicorn/config.py:118-135` in the project's
  virtual environment: `create_ssl_context` builds `ssl.SSLContext(ssl_version)`, calls
  `load_cert_chain`, sets `verify_mode` from `cert_reqs`, and calls
  `load_verify_locations(ca_certs)` when `ca_certs` is set. It never loads the system
  trust store. Run inside the image, building the context with that same function: the
  context reports `verify_mode CERT_REQUIRED`, minimum version `TLSv1_2`, and exactly one
  trusted certificate, CA A (`cert_store_stats()` gives `x509: 1, x509_ca: 1`). So only
  a certificate that chains to the named file is accepted, and a publicly trusted
  certificate is not.

- **P3. No new dependency.** `uvicorn/config.py:10` imports the standard library's
  `ssl`. The image already holds everything P4 used: it was the unmodified image with a
  different entry point file.

- **P4. With the four arguments, the lock holds, and the refusal happens in the
  handshake.** Run against the wrapper with all three settings, from the host on both TLS
  versions for every row. The first three rows were run again from inside the
  container, where no port forward sits between client and server, with the same
  results:

  | Client presents | TLS 1.3 | TLS 1.2 |
  | --- | --- | --- |
  | Leaf from CA A | `HTTP/1.1 200 OK` | `HTTP/1.1 200 OK` |
  | No certificate | handshake call returns; first read fails with no bytes | handshake call fails |
  | Leaf from CA B | the same | the same |
  | Leaf from a second CA with CA A's exact subject name and another key | the same | the same |
  | Self-signed leaf | the same | the same |
  | Leaf from CA A, expired | the same | the same |
  | Leaf from CA A, marked for server use only | the same | the same |

  Three measurements say where the refusal happens.

  1. *The server's handshake fails.* A blocking server inside the image, using the
     context from P2, reported for each row above, on both versions: `do_handshake()`
     completed for the CA A leaf, and raised `PEER_DID_NOT_RETURN_A_CERTIFICATE` for no
     certificate and `CERTIFICATE_VERIFY_FAILED` for every other row.
  2. *uvicorn's HTTP layer never gets the connection.* With one line of tracing added to
     the wrapper on the HTTP protocol class's `connection_made`
     (`uvicorn.protocols.http.httptools_impl`), 14 connections produced 2 trace lines,
     the two with the CA A leaf.
  3. *The application never sees a request.* The container's log held 2 `access`
     events after those 14 connections, both `GET /readyz` `200`.

  On TLS 1.3 `openssl s_client -msg` shows no record from the server after the client's
  `Finished`: no alert, no session ticket, no data. The server writes no log line for a
  refused connection.

  **What the client sees on TLS 1.3 depends on the path, so a test cannot assert one
  error.** Three were measured for the same refusal: through Docker Desktop's port
  forward the first read raised `SSLEOFError` (`UNEXPECTED_EOF_WHILE_READING`); from
  inside the container it raised `BrokenPipeError`; `openssl s_client` reported
  `errno=104`, and `curl` exit 52. What does not vary is that the client receives zero
  bytes of a response.

- **P5. A partial set handed straight to uvicorn is silently open or a traceback, never
  a refusal that says why.** Run with the wrapper, which does not validate:

  | Set | Result |
  | --- | --- |
  | CA only | **plain HTTP on the port, `200` to anyone**, no warning. `uvicorn.Config.is_ssl` is `bool(ssl_keyfile or ssl_certfile or ssl_context_factory)` (`uvicorn/config.py:374-375`), so the CA and `CERT_REQUIRED` are ignored without a certificate |
  | Certificate and key, no CA | **TLS with no client check, `200` to anyone** |
  | Key and CA, no certificate | exit 1, bare `AssertionError` |
  | Certificate and CA, no key | exit 1, `ssl.SSLError: [SSL] PEM lib` |

  The first row is the one that matters: an operator who sets only the client CA believes
  the workspace is locked, and it is serving plain HTTP to everyone.

- **P6. A file uvicorn cannot use stops the process with exit 1 and a traceback that
  names no setting.** Run with all three set and one wrong: a certificate or CA path that
  does not exist gives `FileNotFoundError: [Errno 2] No such file or directory` with no
  path in the message; a key readable only by its owner, as another uid, gives
  `PermissionError: [Errno 13] Permission denied`; a key that belongs to another
  certificate gives `ssl.SSLError: [X509: KEY_VALUES_MISMATCH]`; a CA file that is empty
  or not a certificate gives `ssl.SSLError: [X509: NO_CERTIFICATE_OR_CRL_FOUND]`. None of
  them starts a listener. The image runs as uid 1000 (`Dockerfile`, `USER appuser`), so
  the three files must be readable by that uid.

- **P7. With none of the settings, the call uvicorn receives is the one it receives
  today.** The wrapper with none set, and with all three set to blank, logs
  `Uvicorn running on http://0.0.0.0:8000` and answers `200` on plain HTTP, as the
  image's own entry point does. Run in the project's environment:
  `uvicorn.Config(...)` with the four arguments left out, and with them given as
  `None, None, None, ssl.CERT_NONE`, report the same `is_ssl=False` and the same four
  values, which are the defaults in `uvicorn.run`'s signature. The plan goes further than
  equal defaults and passes no `ssl_*` argument at all when the settings are off, so the
  call carries the same eight keyword arguments it carries today.

- **P8. A blank value does not read as unset for a `Path` field unless the plan makes it
  so.** Run in the project's environment with pydantic-settings as pinned: a field typed
  `Path | None` given `GW_TLS_CERT_FILE=` (blank) becomes `PosixPath('.')`, which is
  truthy; given two spaces it becomes `PosixPath('  ')`. With a `mode="before"` validator
  that maps blank to `None`, both become `None`. `.env.example` must list every setting
  (`tests/test_config.py:119` and `:141` fail otherwise, read), and it ships optional
  values blank, so without that validator an operator using the file as `--env-file`
  would get a workspace that tries to serve TLS from the current directory. The existing
  `_blank_is_unset` validator (`src/glosswork/config.py:240-245`) runs after parsing and
  is for `str` fields, so it cannot be reused as it is.

- **P9. A wide `GW_TRUSTED_PROXY_IPS` is safe only behind the lock.** Run: 12 wrong
  sign-ins for one account, each with a different forged `X-Forwarded-For`.

  | Container | Answers |
  | --- | --- |
  | Image's own entry point, default `GW_TRUSTED_PROXY_IPS` | `401` ten times, then `429`, `429` |
  | Image's own entry point, `GW_TRUSTED_PROXY_IPS` set to the range the caller connects from, no lock | `401` twelve times: the limiter never fires |
  | Wrapper with the lock, same range, caller has no certificate | no HTTP answer, twelve times |
  | Wrapper with the lock, same range, caller holds the CA A leaf | `401` twelve times |

  The last row is the point of the documentation sentence: whoever holds the client
  certificate is believed about `X-Forwarded-For`. The lock moves that trust from
  "anyone who can reach the address" to "the holder of the certificate".

- **P10. The port answers nothing in plain HTTP once the lock is on, and old TLS is
  refused.** Run against the locked wrapper: `curl http://127.0.0.1:<port>/readyz`
  exits 52 with no response. `openssl s_client` offering TLS 1.1 and TLS 1.0 at security
  level 0, with the CA A leaf, fails with `unexpected eof` and no cipher agreed. There is
  one listener: the entry point binds one port, read at `src/glosswork/entrypoint.py:22`.

- **P11. No second process.** Inside the locked container, `/proc` lists PID 1
  (the Python entry point) and the probe that listed it, and nothing else.

- **P12. A caller that never finishes a handshake is dropped after 60 seconds, which is
  no worse than today.** Run inside the containers: a TCP connection to the locked
  wrapper that sends nothing, and one that sends the first five bytes of a TLS record,
  were each closed by the server at 60.0 s. The same silent connection to the image's
  own plain HTTP entry point was still open at 75 s, when the measurement stopped.

- **P13. Nothing in the application reads the request's scheme.** Run:
  `grep -rn "url.scheme" src/glosswork` and `grep -rn "url_for(" src/glosswork` each
  exit 1 with no match, and
  `grep -rn "request\.url\|request\.base_url" src/glosswork` finds only `request.url.path`
  (four places) and the outbound relay client's own request object. Cookie
  security is `GW_COOKIE_SECURE` and absolute URLs are built from `GW_BASE_URL` (read at
  `src/glosswork/config.py:172-183` and `src/glosswork/services/attachments.py:229`).
  So a request arriving as `https` at the application changes no behaviour.

- **P14. The test can make its own certificates and place them with what the suite
  already has.** Run: `cryptography` 50.0.0, which `pyjwt[crypto]` already installs and
  `tests/test_oidc.py:25` already imports, built two client CAs, a server CA and three
  leaves; a container started on those files gave the same three results as P4 on both
  TLS versions. `container_tests/docker_support.py` already has `create_container` (with
  `environment` and `port`), `copy_in`, `start_stopped_container`, `exit_code`, `logs`
  and `exec_in` (read at `:253`, `:320`, `:218`, `:205`, `:371`, `:389`); the harness
  used the same `docker create`, `docker cp`, `docker start` sequence. Files copied in
  with mode `0644` were readable by uid 1000.

- **P15. The baseline is green and the code shapes pass the static scan.**
  `uv run pytest -q tests/test_infra.py tests/test_config.py` exited 0 with 33 passed on
  `42e9d45`. The CI's Semgrep command (`uvx --from semgrep==1.178.0 semgrep scan
  --config p/python --metrics off --error`) over the wrapper, the probe, the server-side
  measurement and the certificate generator exited 0 with 0 findings from 151 rules.
  That is the same use of `ssl`, sockets and `cryptography` the change will contain, not
  the change itself.

- **P16. Two documentation guards constrain how this change is written, and both failed
  on the first draft of this plan.** Run: `uv run pytest -q -m structural` with the plan
  staged (the guards read `git ls-files`, so an untracked plan is not checked and the
  run passes vacuously) exited 1 with two failures in
  `tests/test_documentation_structure.py`.
  `test_every_cited_design_decision_exists` fails any tracked file that cites a decision
  number with no heading in docs/DESIGN_DECISIONS.md, so the new decision cannot be cited
  by number anywhere, plan, code comment or test docstring, until its entry exists. The
  entry therefore lands in the first build commit that cites it.
  `test_no_internal_decision_number_is_cited` matches, case-insensitively, the letters
  `a` and `d` followed by a digit, which is how an abbreviated commit id can begin. This
  plan names that commit by its relation to the merge commit instead. With both fixed
  the same command exits 0.

**Not established, and who can establish it.**

- The client-side symptom of a TLS 1.3 refusal on the release workflow's native Linux
  runners. The test asserts only what P4 found invariant, and the first release dry run
  after this change is where it is measured.
- A client certificate that chains through an intermediate authority. Only a single-level
  CA was measured.
- How a hosting platform delivers the three files and under which uid. That is the
  control plane's side (CP-33); P6 says what the workspace needs.
- Semgrep and the full CI lanes on the real diff. P15 is a proxy.
- Behaviour on an OpenSSL other than 3.5.7. The base image tag floats.

## What changes

**`src/glosswork/config.py`**

- Three fields on `Settings`, each `Path | None`, default `None`: `tls_cert_file`,
  `tls_key_file`, `tls_client_ca_file`.
- One `mode="before"` validator over the three that maps `None`, blank and
  whitespace-only to `None` (P8).
- A property `tls_enabled`, true when all three are set. It is the only thing the entry
  point reads to decide.
- `_check_tls(settings)`, called from `load_settings` beside `_check_relay`, with two
  refusals, each a `ConfigError`:
  - **Some but not all set.** The message names every missing variable and the ones that
    are set, in this form:
    `GW_TLS_KEY_FILE, GW_TLS_CLIENT_CA_FILE: required when GW_TLS_CERT_FILE is set. Set
    all three to make the workspace terminate TLS and require a client certificate from
    that CA, or none of them to serve plain HTTP behind your own proxy. There is no TLS
    without the client certificate check.`
  - **A named file cannot be opened for reading** (all three set). Checked by opening
    each for reading and closing it. The message names the variable, the path and the
    operating system's reason, and reads nothing from the file:
    `GW_TLS_KEY_FILE: cannot read /tls/server.key (Permission denied). The process runs
    as uid 1000 in the published image, and the file must be readable by it.`

  A file that opens but that OpenSSL cannot use (a key for another certificate, a file
  that is not a certificate) is left to uvicorn, which stops the process with exit 1 and
  OpenSSL's own reason (P6). Parsing certificates in `config.py` would be a second
  implementation of what uvicorn does a moment later.

**`src/glosswork/entrypoint.py`**

- When `settings.tls_enabled`, the existing `uvicorn.run` call also receives
  `ssl_certfile`, `ssl_keyfile`, `ssl_ca_certs` (each `str` of the path) and
  `ssl_cert_reqs=ssl.CERT_REQUIRED`. When it is false the call receives no `ssl_*`
  argument (P7). One `import ssl`. A comment says why the four travel together and
  cites the new decision by its number.
- Port, host, worker count and every other argument are untouched.

**`.env.example`**

- A section with the three variables, each blank, saying: all three or none; what the
  lock does; that `GW_TRUSTED_PROXY_IPS` may name a wide range only with the lock on;
  that the files must be readable by uid 1000.

**`docs/DEPLOYMENT.md`** (written during the build, so Accept can check it)

- A new section 4a, "Terminating TLS in the workspace, locked to one client
  certificate", covering, in this order:
  1. When to use it, and that section 4's proxy on a private network remains the
     ordinary self-hosted shape.
  2. The three settings, all or none, and the two startup refusals.
  3. What a refused caller sees on TLS 1.2 and on TLS 1.3, that no request is read, and
     that a refused connection leaves no log line (judgment area 3).
  4. **`GW_TRUSTED_PROXY_IPS` behind the lock.** A value wider than your own proxy's
     address is safe only while the lock is on, because whoever can open a connection is
     believed about `X-Forwarded-For`; with the lock that is the certificate's holder and
     nobody else. Never set a wide value on a workspace without the lock (P9).
  5. The CA file should hold only the authority that signs your proxy's client
     certificate: every certificate that chains to anything in the file is let in (P2).
  6. Probes. `/readyz` and `/healthz` cannot be reached without the certificate, so an
     orchestrator's HTTP probe fails. Use a TCP check on the port, or a prober that
     holds the certificate. A silent connection is dropped after 60 seconds (P12).
  7. Operations: files readable by uid 1000; rotation is a restart; TLS 1.2 is the
     minimum; the port stays 8000; the settings are applied by the image's entry point,
     so `uvicorn glosswork.app:app` run by hand does not apply them.
  8. What it does not do: no revocation list, no encrypted private key, and the
     certificate identifies no principal inside the application.
- Section 2's probe paragraph and section 4's first paragraph each gain one sentence
  pointing at 4a.

**Tests**

- `tests/test_config.py`: the six partial combinations each refuse, naming each missing
  variable (parametrized); all three blank is off; all three set to files that exist is
  on; a path that does not exist refuses naming its variable.
- `tests/test_infra.py`:
  - *All three set:* the captured `uvicorn.run` arguments carry the three paths and
    `ssl.CERT_REQUIRED`, and `uvicorn.Config` built from them reports `is_ssl` true.
  - *A partial set:* `entrypoint.main()` exits 1, stderr starts
    `Configuration error: GW_TLS_`, and `uvicorn.run` was never called.
  - *None set (a fence, labelled so in its docstring):* the captured keyword arguments equal
    today's eight, key for key and value for value, and `uvicorn.Config` built from them
    reports `is_ssl` false. It cannot fail on the unfixed tree by construction; checklist
    step 8 shows it can fail.
- `container_tests/test_tls_lock.py`, new, against the real image. Certificates are made
  in the test with `cryptography`, written `0644`, copied in with `copy_in`. One
  module-scoped locked container, and separate test functions so each is measured on its
  own:
  - `test_the_right_certificate_answers`: on TLS 1.3 and TLS 1.2, `GET /readyz` returns
    a `200` status line and a body containing `"status"` (never the status alone:
    AGENTS.md, Traps).
  - `test_no_certificate_is_refused_at_the_handshake` and
    `test_another_cas_certificate_is_refused_at_the_handshake` (CA B, and a second CA
    carrying CA A's subject name): on each version the client receives zero bytes; on
    TLS 1.2 the client's handshake call itself fails; and the container's `access` event
    count does not move. The count is read from the run: count before, make the refused
    attempts, make one request with the right certificate, wait for the count to reach
    before plus one, and assert it is exactly that. Waiting for the later line is what
    makes "no line for the refused ones" a statement about the log and not about timing.
  - `test_the_port_answers_nothing_in_plain_http`.
  - `test_a_partial_set_refuses_to_start`: a container with the CA only, and one with
    the certificate and key only, each exit 1 with `Configuration error: GW_TLS_` in the
    log. The wait ends when the container has exited or when it answers `/readyz`, so on
    a tree without the check it fails quickly instead of timing out.
  - `test_there_is_one_process` (a fence): `/proc` in the locked container holds PID 1
    and the probe only.

  No assertion depends on speed. The only waits are for readiness and for a log line,
  each bounded by a timeout that is a failure, not a pass.

## What does not change

- **Behaviour with none of the three set.** Same `uvicorn.run` arguments, same plain HTTP
  listener, same log lines. Every existing container test runs that way and none is
  edited.
- `Dockerfile`, `pyproject.toml`, `uv.lock`, `THIRD_PARTY_LICENSES.md`. No dependency,
  no base image change, no new port.
- The process count, the port (8000), `workers=1`, and `GW_TRUSTED_PROXY_IPS`'s default
  and meaning.
- The application. No route, service, repository, MCP tool, migration or frontend file
  is touched. The filter compiler, the schema engine and the access model are not
  reached (PLAN 12's last line holds). The client certificate decides whether a
  connection exists and identifies nobody.
- No new log line. uvicorn's own `Uvicorn running on https://0.0.0.0:8000` is what shows
  TLS is on. A line from the application saying "locked" was considered and declined: the
  application cannot know how it was started, and it would say "locked" under a
  hand-run uvicorn that applies none of this.
- PRD.md FR-P7's text, and docs/DATA_MODEL.md section 13's table, which has not carried
  the settings added since the relay and is not extended here.
- The files that define CI (`.github/`, `scripts/ci_changes.py`, `.gitleaks.toml`,
  `scripts/github/`, and the tests CONTRIBUTING lists). None is touched.
- The version, and `CHANGELOG.md`. A release that carries this is its own change.

## Constraints

- **Same image (PLAN Q6).** This is configuration of the one image. No hosted build.
- **FR-P1.** One process. No proxy, no sidecar, no supervisor.
- **The lock is never half on.** No code path starts a listener with some of the three
  settings. If execution finds a case where it would, stop and report.
- **Non-negotiable 1.** No dependency is added or re-pinned, so no `uv lock` runs. If
  one appears necessary, stop: the plan's premise has changed.
- **Non-negotiable 5 and 7.** Every behaviour ships with a test; new and changed
  functions are type-hinted; `pathlib`, not `os.path`.
- **No secret in output.** A startup refusal names a variable, a path and an operating
  system reason. It never prints file contents.
- **Test keys are generated at run time** and never committed. No `.pem` or `.key` file
  enters the repository (the `secrets` job scans history).
- **Exit codes are read directly**, never through a pipe, and lint is two commands read
  separately (AGENTS.md, Traps).

## Checklist

1. [ ] Write the new tests in `tests/test_config.py`, `tests/test_infra.py` and
       `container_tests/test_tls_lock.py`, and nothing else. They do not cite the new
       decision's number yet (P16); the citation is added in step 3.
2. [ ] Run each against the unfixed tree and record here how each failed, in the order
       written: the two unit files with `uv run pytest -q <file>`, and the container
       file with `GW_IMAGE` naming an image built from `42e9d45`. Expected: the config
       tests fail because the settings are ignored; the entry point tests fail on a
       missing `ssl_certfile` and on no exit; the fence passes and is recorded as a
       fence; the container tests fail because the image serves plain HTTP, except the
       single-process fence. Record what actually happens, not this sentence.
3. [ ] docs/DESIGN_DECISIONS.md: the new entry, under the next unused number, before
       any file cites that number (P16). Then `src/glosswork/config.py`: the three
       fields, the validator, `tls_enabled`, `_check_tls`.
       `uv run pytest -q tests/test_config.py` exits 0 and
       `uv run pytest -q -m structural` exits 0.
4. [ ] `.env.example`: the section. `uv run pytest -q tests/test_config.py` still exits 0.
5. [ ] `src/glosswork/entrypoint.py`: the four arguments.
       `uv run pytest -q tests/test_infra.py` exits 0.
6. [ ] Build the image from the working tree and run
       `uv run pytest -q container_tests/test_tls_lock.py`. Exits 0.
7. [ ] `docs/DEPLOYMENT.md`: section 4a and the two pointers.
8. [ ] Mutations, each applied alone, the named tests run, the result recorded here, and
       the mutation reverted (`git diff --quiet` afterward for the file). Check each
       mutated tree still starts before believing a failure:
       - **M1**, entry point passes no `ssl_cert_reqs`: the no-certificate and
         other-CA container tests fail; the right-certificate test passes.
       - **M2**, entry point passes `ssl.CERT_OPTIONAL`: the no-certificate test fails.
       - **M3**, `load_settings` does not call `_check_tls`: the partial-set tests fail
         in all three files.
       - **M4**, entry point passes `ssl_cert_reqs=ssl.CERT_NONE` when the settings are
         off: the none-set fence in `tests/test_infra.py` fails.
       - **M5**, the blank validator removed: the all-blank config test fails.
9. [ ] Run the whole Accept block and paste the output here.

## Accept

Each is run from the repository root on the finished branch, and each exit code is read
on its own line.

- **AC1. The settings turn the lock on, and a partial set refuses with its reason.**
  `uv run pytest -q tests/test_config.py tests/test_infra.py -k tls` exits 0. Every new
  test in the two files has `tls` in its name. The number selected is read from the
  run's own summary line and is at least twelve: nine config cases and three entry
  point cases.
- **AC2. The three lock cases hold against the real image, at the handshake.**
  `uv run pytest -q container_tests/test_tls_lock.py` exits 0 with no test skipped.
- **AC3. With none of the settings the image behaves as before.**
  `uv run pytest -q container_tests` exits 0. Every file in it but the new one starts
  its containers with none of the three settings.
- **AC4. The whole backend suite.** `uv run pytest -q` exits 0.
- **AC5. Lint and types, three commands, three exit codes.** `uv run ruff check .`
  exits 0. `uv run ruff format --check .` exits 0. `uv run mypy src` exits 0.
- **AC6. No new dependency and no image recipe change.** For each of `Dockerfile`,
  `pyproject.toml`, `uv.lock` and `THIRD_PARTY_LICENSES.md`,
  `git rev-parse origin/main:<path>` and `git rev-parse HEAD:<path>` print the same
  object id. (Object ids, because `uv.lock` is `-diff` and a line-based diff of it
  passes vacuously.)
- **AC7. No second process.** `test_there_is_one_process` is in AC2's run and passed.
  `grep -c "subprocess\|Popen\|os.fork" src/glosswork/entrypoint.py` prints 0; chain the
  next command with `;`, since `grep -c` exits 1 on a zero count.
- **AC8. The documentation says what it must.**
  `grep -c "GW_TLS_CERT_FILE" docs/DEPLOYMENT.md`,
  `grep -c "GW_TLS_KEY_FILE" docs/DEPLOYMENT.md` and
  `grep -c "GW_TLS_CLIENT_CA_FILE" docs/DEPLOYMENT.md` each print at least 1, and the
  verifier reads section 4a and reports whether it states, in so many words, that a
  `GW_TRUSTED_PROXY_IPS` wider than the operator's own proxy is safe only with the lock
  on. A grep proves the words are present; only reading proves the claim is made.
- **AC9. The structural lane.** `uv run pytest -q -m structural` exits 0.
- **AC10. The static scan CI runs.**
  `uvx --from semgrep==1.178.0 semgrep scan --config p/python --config p/javascript
  --config p/typescript --exclude src/glosswork/repositories/sqlite.py --metrics off
  --error .` exits 0.
- **AC11. No key material is committed.**
  `git diff --name-only origin/main...HEAD` lists no path ending `.pem`, `.key` or
  `.crt`.
- **AC12. The mutations in checklist step 8 are recorded with their results**, and
  `git status --short` is empty after the last one is reverted.

After merge, and outside this block: CI green on `main` for the merge commit, read from
`gh api repos/glosswork/glosswork/actions/runs?head_sha=<sha>`, and issue #22 closed.

## Baseline repaint

Not a UI change. No baseline is touched.

## Adversarial pass

Not yet run. A session that did not write this plan runs it and records F1..Fn here.

## Deviations from the approved plan

None yet.

## Durable content moved out of this plan

Not yet moved. At closeout:

- **docs/DESIGN_DECISIONS.md, one new entry under the next unused number.** It is added
  during the build (checklist step 3), not at closeout, for the reason in P16. Proposed
  as:

  > **(heading, with its number): A workspace's own TLS is all three settings or none, and it always carries
  > the client-certificate lock.** With `GW_TLS_CERT_FILE`, `GW_TLS_KEY_FILE` and
  > `GW_TLS_CLIENT_CA_FILE` set, the entry point hands uvicorn the certificate, the key,
  > the CA and `CERT_REQUIRED`, and a caller without a certificate chaining to that CA
  > fails the TLS handshake before any request is read. With some set, startup is
  > refused. With none, the process serves plain HTTP as before.
  >
  > **Why.** A workspace on a public address behind a proxy its operator does not run
  > beside it needs to refuse everyone but that proxy, without a second process (FR-P1).
  > The half states are silently open if passed through: a CA with no certificate serves
  > plain HTTP to anyone, and a certificate with no CA serves TLS to anyone. A wide
  > `GW_TRUSTED_PROXY_IPS` is safe only behind the lock.
  >
  > **Held by.** `tests/test_config.py`, `tests/test_infra.py`,
  > `container_tests/test_tls_lock.py`.
  >
  > **See.** `docs/DEPLOYMENT.md` section 4a.

- **PRD.md, new FR-P11**, proposed as:

  > **FR-P11.** With `GW_TLS_CERT_FILE`, `GW_TLS_KEY_FILE` and `GW_TLS_CLIENT_CA_FILE`
  > all set, the process terminates TLS itself and completes a handshake only with a
  > client whose certificate chains to the named CA; no request from any other caller is
  > read. With some of them set, startup is refused naming what is missing. With none,
  > nothing changes (the new decision, cited by its number).

- **docs/DEPLOYMENT.md section 4a** is written during the build (checklist step 7), and
  the measured facts it needs from this plan are P4's table, P9's table, P6 and P12.
- **AGENTS.md, Traps**: one entry, that a TLS 1.3 client's handshake returns before the
  server has checked the client certificate, so a test of a refused client asserts zero
  bytes received and never a particular exception (P4).
