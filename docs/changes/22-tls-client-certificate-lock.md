# 22: A workspace can terminate TLS itself and refuse any caller without the right client certificate

| | |
| --- | --- |
| Issue | [#22](https://github.com/glosswork/glosswork/issues/22) |
| Branch | `22-tls-client-certificate-lock` |
| Spec | PRD.md FR-P1, FR-P3, FR-P7; docs/DEPLOYMENT.md sections 2 and 4; `.env.example`; PLAN.md section 5.4 and Q84 |
| Decisions | DD-46, new (added in the build's first commit, for the reason in P16). DD-40 is read and not changed |
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

The adversarial pass added the three below. Each changes what is being approved, and
"Adversarial pass" says which finding it came from.

6. **A misspelt name under `GW_TLS_` refuses startup (F3). The maintainer can strike
   it.** The settings loader ignores any variable it does not know, so an operator who
   writes `GW_TLS_CA_FILE` for the client CA, or drops `_FILE` from all three, gets a
   workspace that starts, serves plain HTTP to anyone and says nothing (P19). With two
   names right and one wrong the partial-set refusal already fires; with all three wrong
   nothing does. This plan adds a third startup refusal: any environment variable whose
   name begins `GW_TLS_` and is not one of the three. It is one more step past what the
   issue asks for, it is a pattern no other setting group in the repository has, and it
   is in the plan because this group is the one where an ignored name means an open
   door. Struck, the change is smaller by one check, one test and one mutation, and the
   documentation says in so many words that a misspelt name is silently ignored.

7. **What the product does not close, stated so it is approved knowingly (F3).** "None
   of the three set" is a valid state and always will be, so a deployment in which the
   three never arrive (the platform dropped the block, the names sit under another
   prefix) starts unlocked, and the workspace cannot tell that from a self-hosted
   deployment that never wanted the lock. A fourth setting meaning "the lock is
   required" was considered and declined: it is beyond the three the issue names, and
   it can fail to arrive in exactly the same way. The check that closes this belongs to
   whoever provisions the workspace: connect to its address with no certificate and
   require a refusal before anything points at it. The documentation says so (4a,
   item 2) and the control plane's routing work carries it.

8. **The documentation sentence about `GW_TRUSTED_PROXY_IPS` becomes two statements
   (F2).** The issue asks the documentation to say that a wide value is only safe with
   the lock on. That is true and it is not enough: with the lock on, a value of `*`, or
   any range wide enough to cover a visitor's address, still lets a visitor choose the
   address the sign-in limiter sees, through an honest proxy that holds the right
   certificate (P18). So section 4a says both: wide only behind the lock, and never
   `*`, lock or no lock, unless the proxy overwrites `X-Forwarded-For` rather than
   appending to it. Accept criterion AC8 checks both.

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

  *Amended by the adversarial pass (F4, F5).* Rerun with a second harness, the whole
  table reproduced: the seven rows, 14 connections, gave 2 answers, 2 `connection_made`
  lines and 2 `access` events, and with P17's six further rows 26 connections gave 6
  answers and 6 `access` events. Three Linux client positions were
  added, each on both versions: inside the server's container on loopback, from a second
  container to the server's bridge address, and through a userland TCP relay in that
  second container. On all three, and through Docker Desktop's forward, **the TLS 1.3
  client's handshake call returned, with `version()` reading `TLSv1.3`, before the
  refusal, and the TLS 1.2 client's handshake call failed.** The error that follows on
  TLS 1.3 was `BrokenPipeError` raised from the *read* on the two direct Linux paths,
  `SSLEOFError` through the relay and the forward, and `ConnectionResetError` from
  Python's `http.client`: three Python exceptions for one refusal, two of which are not
  `ssl.SSLError`, so a probe catches `OSError` on the send and on the read. The
  release workflow's own runners are still not measured; what a wrong guess there costs
  is a test that errors, never one that passes (P20).

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
  them starts a listener. *Added by the adversarial pass:* a certificate or CA path that
  is a directory gives `IsADirectoryError`, and a private key encrypted with a
  passphrase gives `OSError: [Errno 22] Invalid argument` with no terminal attached;
  both exit 1 before listening. The image runs as uid 1000 (`Dockerfile`, `USER appuser`), so
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
  one listener: the entry point binds one port, read at `src/glosswork/entrypoint.py:22`,
  and measured by the adversarial pass in P21.

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

**Premises added by the adversarial pass (2026-10-02).** A session that did not write
P1 to P16 rebuilt the image from this branch (`docker image inspect` shows the branch
head as its revision; the branch adds only this file to `main`'s tree), wrote its own
wrapper, probe and certificate generator rather than reusing run 1's, and reran P1 to
P15 before adding these. Every one reproduced. P16's two guards were read
(`tests/test_documentation_structure.py:198` and `:205`) and the structural lane run
with this file committed, exit 0; the two failures on the first draft were not
re-created. The scripts and their output are attached to PROD-60 beside run 1's.

- **P17. The lock lets in every certificate that chains to the CA file, by any route,
  and a CA file that is not a self-signed root lets in nobody.** Run against the locked
  wrapper, both TLS versions, the same result on each:

  | Client presents, CA file holds CA A | Result |
  | --- | --- |
  | Leaf under an intermediate that CA A signed, sent with the intermediate | `200` |
  | The same leaf sent alone | refused |
  | Leaf from CA A with **no** extended key usage at all (how a plain server certificate from the same CA looks) | `200` |
  | Leaf from CA A not yet valid | refused |
  | A certificate signed by leaf A, which is not a CA, sent with leaf A | refused |
  | The same, where the signing leaf carries no basic constraints at all | refused |

  | CA file holds | Result |
  | --- | --- |
  | CA A and CA B together | leaves of both `200`; the same-name twin still refused |
  | Only the intermediate, not CA A | **everyone refused**, including the leaf that intermediate signed. uvicorn's context carries verify flags `32768`, which is OpenSSL's "trusted first" and not "partial chain", so a chain must end at a self-signed certificate in the file |
  | Only leaf A itself (a certificate pinned as if it were a CA) | **everyone refused**, leaf A included |
  | A self-signed client certificate, pinned | that certificate `200`, leaf A refused |
  | The server's own CA, client presents the server's certificate and key | refused when that certificate is marked for server use only; P17's third row is the case where it is not |

  So an intermediate is not a narrower lock than its root: name the root and every
  intermediate under it is trusted, name the intermediate and the workspace is closed to
  all. Both wrong ways to fill the file fail closed. The one way to widen the lock by
  accident is a CA that also signs something else.

- **P18. `GW_TRUSTED_PROXY_IPS` set to `*`, or to a range that covers the visitor, is
  forgeable with the lock on.** Read at `uvicorn/middleware/proxy_headers.py:176-187`:
  with `*` the client is the *first* `X-Forwarded-For` entry, and when every entry is
  trusted it is the first entry too; otherwise it is the last entry that is not trusted.
  Run: 12 wrong sign-ins for one account against the locked wrapper, from a client
  holding the CA A leaf and behaving as an honest proxy does, appending one real visitor
  address (`203.0.113.9`) after whatever the visitor sent (`6.6.6.N`, different each
  time).

  | `GW_TRUSTED_PROXY_IPS` | Answers |
  | --- | --- |
  | The proxy's own range | `401` ten times, then `429`, `429` |
  | The proxy's own range, visitor also forges an address inside that range before the real one | `401` ten times, then `429`, `429` |
  | `*` | `401` twelve times: the limiter never fires |
  | `0.0.0.0/0` | `401` twelve times |
  | The proxy's own range, where the visitor's real address is itself inside that range | `401` twelve times |

  P9's four rows reproduced first. So the lock decides who may *connect*. It does not
  make a visitor's own header trustworthy, and a value that trusts the visitor's
  address hands the choice back to the visitor through the proxy.

- **P19. With the plan's check, every partial or malformed value of the three refuses
  startup; the states that start unlocked are "none of the three" and "none of the three
  under their right names".** Run in the project's environment against a scratch subclass
  of `Settings` written from "What changes" (three fields, the blank validator, the
  all-or-none check, the open-for-reading check), over 20 environments:

  | Environment | Result |
  | --- | --- |
  | Each of the six ways to set one or two of the three | refuses, naming the missing ones |
  | One set, the other two blank or spaces | refuses, the same way |
  | All three, one path missing, a directory, or ending in a newline | refuses, naming the variable and the reason |
  | All three given as a literal pair of quotes, as Docker's `--env-file` passes `""` | refuses: `cannot read ""` |
  | All three, the CA file empty | passes the check, and uvicorn then exits 1 (P6) |
  | Lower-case names | read as the three; the lock is on |
  | All three blank, or all three spaces | **starts, lock off**, as designed (P8) |
  | `GW_TLS_CERT`, `GW_TLS_KEY`, `GW_TLS_CLIENT_CA` (no `_FILE`) | **starts, lock off, no message** |
  | `GW_TLS_CA_FILE` alone | **starts, lock off, no message** |
  | `GW_SSL_CERT_FILE`, `GW_SSL_KEY_FILE`, `GW_SSL_CLIENT_CA_FILE` | **starts, lock off, no message** |
  | Two names right and `GW_TLS_CA_FILE` for the third | refuses as a partial set |

  `Settings` is declared `extra="ignore"` (read at `src/glosswork/config.py:45`), which
  is why an unknown name says nothing.

- **P20. "Zero bytes received, the TLS 1.2 handshake call fails, and the access count
  does not move" is true of a server with no lock at all.** Run: the same no-certificate
  probe against the image's own plain HTTP entry point failed its handshake call on both
  versions (`record layer failure`), received zero bytes, and moved the `access` count
  by 0. Against the locked wrapper, a client that held the *right* certificate and
  trusted the wrong server CA did the same (`SSLCertVerificationError` on both
  versions). So those three observations do not show a lock: they show a client that got
  no answer. What separates the cases is the TLS 1.3 client's handshake call *returning*
  with `version()` `TLSv1.3` (P4, four client positions), which happens only when the
  server spoke TLS 1.3 and the client accepted its certificate; and, for TLS 1.2, a
  right-certificate request built by the same code succeeding immediately afterwards.

  The direct evidence can be had without a network. Run in the project's environment
  (OpenSSL 3.5.4, macOS) and again inside the image (OpenSSL 3.5.7, Linux), same output:
  `uvicorn.Config(<a no-op application>, ssl_certfile=…, ssl_keyfile=…, ssl_ca_certs=…,
  ssl_cert_reqs=ssl.CERT_REQUIRED).load()` yields the context uvicorn serves with
  (`verify_mode CERT_REQUIRED`, one trusted CA), and a handshake against it over
  `ssl.MemoryBIO` pairs gave, on TLS 1.3 and 1.2: both sides complete for the CA A leaf;
  the **server's** `do_handshake()` raising `PEER_DID_NOT_RETURN_A_CERTIFICATE` for no
  certificate and `CERTIFICATE_VERIFY_FAILED` for CA B and for the same-name twin; and
  the client's handshake call already returned at that moment on TLS 1.3 and not on
  TLS 1.2. The 24 handshakes of the three variants took 0.03 s and 0.07 s. With
  `ssl_cert_reqs` left out every client completes; with `CERT_OPTIONAL` the client with
  no certificate completes and CA B is still refused.

- **P21. Nothing else in the image answers, and nothing ambient widens the lock.** Run
  inside the locked container: `/proc/net/tcp` holds one listening socket,
  `0.0.0.0:8000`, and `tcp6`, `udp` and `udp6` hold none; `/proc` holds PID 1 and the
  probe. `grep -rnE "uvicorn|socket\.|\.listen\(|\.bind\(|create_server|start_server"`
  over `src/glosswork` outside the entry point finds comments that name uvicorn and one
  `server.bind(adapter)` at `src/glosswork/mcp_server/__init__.py:208`, which attaches an
  adapter object to the MCP server mounted in the same application and opens nothing. No
  module under `src/glosswork` imports `socket`, `socketserver`, `http.server`,
  `multiprocessing` or `subprocess`, and `pyproject.toml` declares no script. The entry
  point ignores its arguments, read at
  `src/glosswork/entrypoint.py:12-17`. Offered by `openssl s_client` at security level 0
  on TLS 1.2, with no certificate: anonymous suites, pre-shared-key suites and
  null-encryption suites each ended with no cipher agreed; a TLS 1.3 pre-shared key was
  ignored and the caller refused as usual. The context holds 17 suites; their
  authentication is RSA, ECDSA or TLS 1.3's own, and none has zero-strength encryption.
  Started with `SSL_CERT_FILE` naming CA B, `SSL_CERT_DIR`,
  `FORWARDED_ALLOW_IPS=*`, `WEB_CONCURRENCY=3`, `UVICORN_SSL_CERT_REQS=0` and
  `UVICORN_PORT=9999` in the environment, the locked wrapper still refused the CA B leaf
  and the caller with no certificate on both versions, listened on 8000 only, and ran
  one process: uvicorn's `Config` reads the environment only for the worker count and
  the forwarded-address list when those arguments are absent
  (`uvicorn/config.py:352-357`), and the entry point passes both.

  One thing that is not a way in and is worth knowing: a client that completed one
  handshake with a good certificate resumed its session on a second connection, on both
  versions (`session_reused` true, `200`). The certificate is checked when a session is
  made, not on each connection, and the keys that seal a session ticket live in the
  process, so a restart ends every session.

- **P22. The container tests are a gate at release and by hand, not on a pull
  request.** Read at `.github/workflows/ci.yml:239` (`uv run pytest -q`, which
  `testpaths` limits to `tests/`) and `.github/workflows/release.yml:146-147` and
  `:200-201`, where `GW_IMAGE=glosswork:release uv run pytest -q container_tests` runs
  before the push step on both architectures; `tests/test_release_workflow.py:36` and
  `:119` pin that order. So a later change that broke the lock (a uvicorn upgrade, say)
  could not be *released* past `container_tests/test_tls_lock.py`, and could be *merged*
  past it, because on a pull request the only check would be that the entry point passed
  four arguments. `container_tests/conftest.py:20-24` uses `GW_IMAGE` when it is set and
  otherwise builds `glosswork:container-test` from the working tree.

**Not established, and who can establish it.**

- The client-side symptom of a TLS 1.3 refusal on the release workflow's native Linux
  runners. P4 now covers three Linux client positions and a userland relay, not the
  runners themselves; the first release dry run after this change is where it is
  measured, and a surprise there fails the test rather than passing it (P20).
- How a hosting platform delivers the three files and under which uid. That is the
  control plane's side (CP-33); P6 says what the workspace needs. CP-07's runbook
  records that its test tenant started on the published image with files delivered by
  the platform, and records neither their owner nor their mode. An unreadable file
  stops the process before it listens (P6, reproduced), so what this gap can hide is a
  workspace that will not start, not one that starts open.
- Semgrep and the full CI lanes on the real diff. P15 is a proxy.
- Behaviour on an OpenSSL older than 3.5. P20's handshake ran on 3.5.4 and 3.5.7 and
  the base image tag floats; the test P20 leads to runs on whatever the pipeline has.
- Revocation. Nothing was measured, because nothing is configured: a certificate from
  the CA is good until it expires or the CA file changes and the process restarts.

## What changes

**`src/glosswork/config.py`**

- Three fields on `Settings`, each `Path | None`, default `None`: `tls_cert_file`,
  `tls_key_file`, `tls_client_ca_file`.
- One `mode="before"` validator over the three that maps `None`, blank and
  whitespace-only to `None` (P8).
- A property `tls_enabled`, true when all three are set. It is the only thing the entry
  point reads to decide.
- `_check_tls(settings)`, called from `load_settings` beside `_check_relay`, with three
  refusals, each a `ConfigError`:
  - **A name under `GW_TLS_` that is not one of the three** (judgment area 6, F3).
    Checked first, over the process environment, comparing names upper-cased because the
    loader reads them without regard to case (P19), and whatever the value, blank
    included. The message names the stray variable and prints no value:
    `GW_TLS_CA_FILE: not a setting. The three are GW_TLS_CERT_FILE, GW_TLS_KEY_FILE and
    GW_TLS_CLIENT_CA_FILE. An unknown name is refused rather than ignored, because
    ignoring it would start the workspace with no client certificate check.`
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

  **Who else these refusals reach (F6).** `load_settings` has four callers, read at
  `src/glosswork/entrypoint.py:14`, `src/glosswork/app.py:671`,
  `src/glosswork/admin.py:466` and `src/glosswork/config.py:547`. So the admin CLI and a
  hand-run `uvicorn glosswork.app:app` refuse on a partial set, a stray name or an
  unreadable file exactly as the entry point does. That is the behaviour the relay and
  bootstrap checks already have, and it is kept: `docker exec` runs the CLI as uid 1000
  (measured), the same user as the server, so a file the server can read the CLI can
  read. The one new consequence is that a CLI run under a third uid that cannot read the
  key is refused over a file it never uses. The documentation says so (4a, item 7).

**`src/glosswork/entrypoint.py`**

- When `settings.tls_enabled`, the existing `uvicorn.run` call also receives
  `ssl_certfile`, `ssl_keyfile`, `ssl_ca_certs` (each `str` of the path) and
  `ssl_cert_reqs=ssl.CERT_REQUIRED`. When it is false the call receives no `ssl_*`
  argument (P7). One `import ssl`. A comment says why the four travel together and
  cites the new decision by its number.
- Port, host, worker count and every other argument are untouched.

**`.env.example`**

- A section with the three variables, each blank, saying: all three or none; what the
  lock does; that `GW_TRUSTED_PROXY_IPS` may name a wide range only with the lock on,
  and never `*` (F2); that the files must be readable by uid 1000.

**`docs/DEPLOYMENT.md`** (written during the build, so Accept can check it)

- A new section 4a, "Terminating TLS in the workspace, locked to one client
  certificate", covering, in this order:
  1. When to use it, and that section 4's proxy on a private network remains the
     ordinary self-hosted shape.
  2. The three settings, all or none, and the three startup refusals. Then, in its own
     paragraph (judgment area 7, F3): **a workspace started with none of the three is
     unlocked and says nothing about it**, because that is also the ordinary self-hosted
     state. So prove the lock from outside after every deployment that is meant to have
     it: connect to the workspace's own address with no certificate and require that no
     answer comes back, and require the log line `Uvicorn running on https://`. Do that
     before any name or proxy points at the workspace.
  3. What a refused caller sees on TLS 1.2 and on TLS 1.3, that no request is read, and
     that a refused connection leaves no log line (judgment area 3).
  4. **`GW_TRUSTED_PROXY_IPS` behind the lock** (judgment area 8). Two statements, both
     in so many words:
     - A value wider than your own proxy's address is safe only while the lock is on,
       because whoever can open a connection is believed about `X-Forwarded-For`; with
       the lock that is the certificate's holder and nobody else. Never set a wide value
       on a workspace without the lock (P9).
     - The lock does not make `*` safe. With `*`, or with any range that also covers
       addresses your visitors can have, the workspace takes the *first* address in
       `X-Forwarded-For`, which is the one the visitor wrote, and a visitor coming
       through your own proxy then chooses the address the sign-in limiter sees. Name
       the range your proxy connects from and nothing wider (P18).
  5. The CA file should hold one self-signed authority that signs your proxy's client
     certificate and nothing else. Every certificate that chains to anything in the file
     is let in (P2), and that includes a certificate under any intermediate that
     authority has signed, and a server certificate from the same authority unless it is
     marked for server use only. A file holding only an intermediate, or only the client
     certificate itself, lets nobody in, the right client included (P17).
  6. Probes. `/readyz` and `/healthz` cannot be reached without the certificate, so an
     orchestrator's HTTP probe fails. Use a TCP check on the port, or a prober that
     holds the certificate. A silent connection is dropped after 60 seconds (P12).
  7. Operations: files readable by uid 1000; rotation is a restart; TLS 1.2 is the
     minimum; the port stays 8000; the settings are applied by the image's entry point,
     so `uvicorn glosswork.app:app` run by hand, or a container whose entry point has
     been replaced, serves plain HTTP whatever the three say; the admin CLI reads the
     same settings and is refused by the same three checks (F6).
  8. What it does not do: no revocation list, no encrypted private key (a key that needs
     a passphrase stops startup, P6), and the certificate identifies no principal inside
     the application. The certificate is checked when a TLS session is made, so an open
     connection or a resumed session outlives the certificate's expiry until the process
     restarts (P21).
- Section 2's probe paragraph and section 4's first paragraph each gain one sentence
  pointing at 4a.

**Tests**

- `tests/test_config.py`: the six partial combinations each refuse, naming each missing
  variable (parametrized); all three blank is off; all three set to files that exist is
  on; a path that does not exist refuses naming its variable; a stray `GW_TLS_CA_FILE`
  refuses naming it, alone and beside the three real ones (judgment area 6).
- `tests/test_infra.py`:
  - *All three set:* the captured `uvicorn.run` arguments carry the three paths and
    `ssl.CERT_REQUIRED`, and `uvicorn.Config` built from them reports `is_ssl` true.
  - *The context uvicorn builds from those arguments refuses in its own handshake
    (F4, F7; added by the adversarial pass, prototyped in P20).* Certificates are made
    in the test with `cryptography`. The `ssl_*` arguments captured from
    `entrypoint.main()` go to `uvicorn.Config` with a no-op application, `.load()` is
    called, and `.ssl` is the context under test: `verify_mode` is `CERT_REQUIRED` and
    `cert_store_stats()` counts one CA. Then, over `ssl.MemoryBIO` pairs, on TLS 1.3 and
    on TLS 1.2: the CA A leaf completes on both sides; with no certificate, with a CA B
    leaf, and with a leaf from a second CA carrying CA A's subject name, the *server's*
    `do_handshake()` raises `ssl.SSLError`; and at that moment the client's handshake
    call has returned on TLS 1.3 and has not on TLS 1.2. No socket, no thread, no
    Docker, no wait. This is the test that runs on every pull request (P22), and it is
    the direct measurement of "refused at the handshake" that the container test can
    only infer from the client's side. The OpenSSL reason is recorded in the assertion
    message and not asserted: the exception type and which side raised are.
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

    **Those three observations are also true of a server with no lock, and of a client
    that cannot verify the server (P20), so each refused case carries controls that are
    not (F4):**
    - one function builds every probe, and the accepted and refused cases differ only
      in the client certificate handed to it;
    - on TLS 1.3 the client's handshake call must *return* and `version()` must read
      `TLSv1.3` before the zero-bytes assertion. That is the proof, inside the refused
      case itself, that the server spoke TLS and the client accepted its certificate, so
      the silence that follows is the server's refusal;
    - on TLS 1.2 the handshake failure must not be an `ssl.SSLCertVerificationError`,
      which is the client rejecting the server;
    - the right-certificate request that closes the count is made on each version, by
      the same function, in the same test.

    The probe catches `OSError` on the send and on the read (P4): a narrower catch turns
    a refusal into a test error on a path with a different symptom, which is a red run
    and never a false pass.
  - `test_the_port_answers_nothing_in_plain_http`.
  - `test_a_partial_set_refuses_to_start`: a container with the CA only, one with the
    certificate and key only, and one with a stray `GW_TLS_CA_FILE` and nothing else
    (judgment area 6), each exit 1 with `Configuration error: GW_TLS_` in the log. The
    wait ends when the container has exited or when it answers `/readyz` over plain HTTP
    or over TLS, so on a tree without the check it fails quickly instead of timing out.
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
  settings, or with a name under `GW_TLS_` that is not one of them. If execution finds a
  case where it would, stop and report.
- **`ssl.CERT_REQUIRED` and the CA travel with the certificate in one place.** The four
  arguments are added together, by one branch, or not at all. No refactor passes the
  certificate and key from one place and the client check from another: the certificate
  and key alone are TLS that lets anyone in (P5).
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

1. [x] Write the new tests in `tests/test_config.py`, `tests/test_infra.py` and
       `container_tests/test_tls_lock.py`, and nothing else. They do not cite the new
       decision's number yet (P16); the citation is added in step 3. The handshake test
       and the refused-case controls are written as "What changes" words them; the
       scratch prototype attached to PROD-60 is a reference for the technique, not code
       to copy.

       *Done (run 3, 2026-10-02).* Twelve config cases, four entry point cases, and nine
       container cases in six test functions. Every new test in the two unit files has
       `tls` in its name. How the tests differ from "What changes" is under "Deviations"
       (D2 to D6).
2. [x] Run each against the unfixed tree and record here how each failed, in the order
       written: the two unit files with `uv run pytest -q <file>`, and the container
       file with `GW_IMAGE` naming an image built from `42e9d45`. Expected: the config
       tests fail because the settings are ignored; the entry point tests fail on a
       missing `ssl_certfile` and on no exit; the handshake test fails because
       `uvicorn.Config(...).ssl` is `None`; the fence passes and is recorded as a
       fence; the container tests fail because the image serves plain HTTP, except the
       single-process fence. **Each of the two refused-case container tests must fail on
       its own TLS 1.3 control (the handshake call does not return), not only on the
       right-certificate request at its end (P20, F4).** If either gets as far as its
       zero-bytes assertion against a plain HTTP image, the control is missing: stop and
       fix the test before going on. Record what actually happens, not this sentence.

       *What happened (run 3, 2026-10-02).* The unit files ran on the branch with only
       the tests added. The container file ran with `GW_IMAGE` naming an image built
       from `git archive 42e9d45`, whose revision label `docker image inspect` printed as
       that commit.

       `uv run pytest -q tests/test_config.py`: exit 1, 12 failed, 21 passed. All twelve
       new cases failed and nothing else did.
       - the six partial sets, the missing path and the three stray names: `Failed: DID
         NOT RAISE ConfigError`. The settings are ignored, so nothing refuses;
       - all three blank: `assert None is False`, because `Settings` has no `tls_enabled`;
       - all three naming files: `assert None is True`, the same way.

       `uv run pytest -q tests/test_infra.py`: exit 1, 3 failed, 13 passed.
       - all three set: the `ssl_*` arguments captured were `{}`, four expected;
       - the handshake test: `uvicorn built no TLS context from the entry point's
         arguments` (`assert None is not None`), on its first assertion;
       - a partial set: `Failed: DID NOT RAISE SystemExit`;
       - none set: **passed, and it is a fence.** It cannot fail here by construction.
         M4 in step 8 is what shows it can fail.

       `GW_IMAGE=<the unfixed image> uv run pytest -q container_tests/test_tls_lock.py`:
       exit 1, 8 failed, 1 passed, in 12 s.
       - the right certificate: its TLS 1.3 handshake failed with `[SSL] record layer
         failure`, which is a plain HTTP server answering a TLS client;
       - **no certificate, another CA's certificate, and the same-name CA's certificate:
         each failed on its own TLS 1.3 control**, `the client's handshake call did not
         return, so this is not a TLS 1.3 server refusing a client certificate`, with the
         same `record layer failure`. None reached its zero-bytes assertion, and none
         reached the right-certificate request at its end. The stop condition did not
         fire;
       - plain HTTP: the port answered `HTTP/1.1 200 OK` where zero bytes were required;
       - the three partial sets (client CA only; certificate and key only; a stray
         `GW_TLS_CA_FILE` only): each container started and answered `/readyz` in plain
         HTTP, where an exit was required. Each failed within seconds, not at a
         timeout;
       - one process: **passed, and it is a fence.**
3. [x] docs/DESIGN_DECISIONS.md: the new entry, under the next unused number, before
       any file cites that number (P16). Then `src/glosswork/config.py`: the three
       fields, the validator, `tls_enabled`, `_check_tls`.
       `uv run pytest -q tests/test_config.py` exits 0 and
       `uv run pytest -q -m structural` exits 0.

       *Done, with one command that could not exit 0 at this step (D1).* The entry is
       DD-46, the last in the file. `uv run pytest -q -m structural` exited 0, 101
       passed. `uv run pytest -q tests/test_config.py` exited 1 with one failure, 32
       passed: `test_every_setting_config_reads_appears_in_env_example`, which fails
       until step 4 lists the three in `.env.example`. All twelve new cases passed.
4. [x] `.env.example`: the section. `uv run pytest -q tests/test_config.py` still exits 0.

       *Done.* Exit 0, 33 passed. This is the first point at which the file is green
       (D1).
5. [x] `src/glosswork/entrypoint.py`: the four arguments.
       `uv run pytest -q tests/test_infra.py` exits 0.

       *Done.* Exit 0, 16 passed.
6. [x] Build the image from the working tree and run
       `uv run pytest -q container_tests/test_tls_lock.py`. Exits 0.

       *Done.* With `GW_IMAGE` unset, so the suite built the image from the working
       tree: exit 0, 9 passed in 10 s.
7. [x] `docs/DEPLOYMENT.md`: section 4a and the two pointers.

       *Done.* The eight items in the order given, and one sentence each in section 2's
       probe paragraph and section 4's first paragraph. Three facts in 4a were measured
       in this run and are not in the premises (D7). After it: `uv run pytest -q` exit
       0, 2215 passed, 3 xfailed; `uv run ruff check .` exit 0; `uv run ruff format
       --check .` exit 0; `uv run mypy src` exit 0.
8. [x] Mutations, each applied alone, the named tests run, the result recorded here, and
       the mutation reverted (`git diff --quiet` afterward for the file). Check each
       mutated tree still starts before believing a failure:
       - **M1**, entry point passes no `ssl_cert_reqs`: the no-certificate and
         other-CA container tests fail; the right-certificate test passes. In
         `tests/test_infra.py` the all-three test and the handshake test fail too.
         *Measured on the wrapper by the adversarial pass (F7):* every client, with any
         certificate or none, got `200` on both versions.
       - **M2**, entry point passes `ssl.CERT_OPTIONAL`: the no-certificate container
         test fails, and so do the all-three test and the handshake test. **The other-CA
         container test still passes, and that is the expected result, not a gap:**
         measured, a client with no certificate got `200` and a CA B leaf was still
         refused, because an optional check still verifies a certificate that is offered.
       - **M3**, `load_settings` does not call `_check_tls`: the partial-set tests fail
         in all three files, and so do the missing-path and stray-name config tests.
       - **M4**, entry point passes `ssl_cert_reqs=ssl.CERT_NONE` when the settings are
         off: the none-set fence in `tests/test_infra.py` fails.
       - **M5**, the blank validator removed: the all-blank config test fails. Measured
         on the scratch prototype: all three blank then refuses with
         `GW_TLS_CERT_FILE: cannot read . (Is a directory)`.
       - **M6** (added, F3), the stray-name refusal removed from `_check_tls`: the
         stray-name config test and the third container in
         `test_a_partial_set_refuses_to_start` fail.
       - **M7** (added, F7), entry point passes no `ssl_ca_certs`: the right-certificate
         container test and the handshake test's accepted case fail, because a required
         check with nothing to check against refuses everyone. Measured on the context
         uvicorn builds: an empty trust store, and the CA A leaf refused with
         `CERTIFICATE_VERIFY_FAILED` on both versions. This is the mutation that shows
         the CA argument is load-bearing, and that losing it fails closed.
       - **M8** (added, F4), in `container_tests/test_tls_lock.py` only: start the
         module's container with none of the three settings. Both refused-case tests
         fail at their TLS 1.3 control. This mutates the test's subject, not the
         product, and it is what shows the refused cases cannot pass against an unlocked
         server.

       *Results (run 3, 2026-10-02).* Each mutation was applied alone to the committed
       build. For each: `import glosswork.entrypoint, glosswork.config` exited 0 and
       `uv run mypy src` exited 0, so the mutated tree compiles; then
       `uv run pytest -q tests/test_config.py tests/test_infra.py` (49 tests) and, with
       `GW_IMAGE` unset so the image is rebuilt from the mutated tree,
       `uv run pytest -q container_tests/test_tls_lock.py` (9 tests). In every container
       run at least four tests passed against the mutated image, which is the proof that
       it started. After each, `git checkout` of the file and `git diff --quiet` exit 0.

       | | Unit files | Container file | As predicted |
       | --- | --- | --- | --- |
       | M1 | 2 failed: the all-three test (no `ssl_cert_reqs` among the arguments) and the handshake test (`verify_mode` is `CERT_NONE`) | 3 failed: no certificate, another CA, the same-name CA, each answered `200` where zero bytes were required. The right-certificate test passed | yes |
       | M2 | 2 failed: the same two (`CERT_OPTIONAL` where `CERT_REQUIRED` is required) | 1 failed: no certificate, answered `200`. Both other-CA cases passed | yes, the other-CA pass included |
       | M3 | 11 failed: the six partial sets, the missing path, the three stray names, and the entry point's partial-set test | 3 failed: the three partial-set containers, each answering in plain HTTP | yes |
       | M4 | 1 failed: the none-set fence, with an extra `ssl_cert_reqs` among the arguments | 9 passed | yes. The fence can fail |
       | M5 | 1 failed: all three blank | 9 passed | yes |
       | M6 | 3 failed: the three stray-name cases | 1 failed: the stray-name container, answering in plain HTTP | yes |
       | M7 | 2 failed: the all-three test (no `ssl_ca_certs`) and the handshake test | 5 failed: the right certificate got no answer on TLS 1.3, and the four tests that close with a right-certificate request failed at that request | yes, with one difference below |
       | M8 | not run: the mutation is in the container test | 5 failed. No certificate, another CA and the same-name CA each failed **at the TLS 1.3 control**; the right-certificate and plain HTTP tests failed too | yes |

       Two things the table cannot hold.

       *M7 fails the handshake test one assertion before its accepted case.* The test
       asserts the context's trust store holds one CA before it runs any handshake, and
       under M7 the store is empty (`{'x509': 0, 'crl': 0, 'x509_ca': 0}`), so it fails
       there. The accepted case was therefore not reached under M7 in this run. That an
       empty store refuses the right certificate is shown by M7's container half, where
       the right certificate's TLS 1.3 handshake returned and no answer came.

       *M4's container half was run twice, and the first run measured nothing.* Docker's
       disk filled during it (`sqlite3.OperationalError: database or disk is full` in the
       container's log, at application startup), so the locked container exited and six
       tests failed for a reason that had nothing to do with the mutation. The disk held
       about 2 GB free before this run, and each rebuilt image costs about 350 MB of
       layers. This run's own images and build cache records were removed, nothing
       older was touched, and from M4 on each mutated image was removed before the next
       was built. M1 to M3 ran before the disk filled; their container runs passed
       six, eight and six tests against a running container. The rerun of M4 is the
       row above.
9. [x] Run the whole Accept block and paste the output here.

       *Run by the build run (run 3, 2026-10-02) at `75d633e`, the commit before the one
       that records this.* This is the build's own run. The verification of record is a
       separate session's. AC8's three reading verdicts and AC12's reading of step 8
       are that session's and are not claimed here. The image was built by AC2 with
       `GW_IMAGE` unset, and AC3 reused it.

       ```
       HEAD 75d633e7020f5ff21167de3cd2e7a7613c272101
       --- AC1   uv run pytest -q tests/test_config.py tests/test_infra.py -k tls
       exit=0
       16 passed, 33 deselected in 0.10s
       --- AC2   env -u GW_IMAGE uv run pytest -q -rs container_tests/test_tls_lock.py
       git status --short: []
       exit=0
       9 passed in 9.45s
       image revision: 75d633e7020f5ff21167de3cd2e7a7613c272101
       git rev-parse HEAD: 75d633e7020f5ff21167de3cd2e7a7613c272101
       --- AC3   uv run pytest -q container_tests
       exit=0
       41 passed in 272.72s (0:04:32)
       --- AC4   uv run pytest -q
       exit=0
       2215 passed, 3 xfailed, 2 warnings in 288.62s (0:04:48)
       --- AC5
       ruff check exit=0
       ruff format --check exit=0
       mypy exit=0
       Success: no issues found in 90 source files
       --- AC6   <path> <origin/main object id> <HEAD object id>
       Dockerfile c4303b2f6c2543d9bbc2e3b0817e2ffa422606bd c4303b2f6c2543d9bbc2e3b0817e2ffa422606bd
       pyproject.toml feecb0f199ee21ff9abe6d556d325ec353ddc473 feecb0f199ee21ff9abe6d556d325ec353ddc473
       uv.lock 84a197533000a924d7ddd2763c627953d375f94f 84a197533000a924d7ddd2763c627953d375f94f
       THIRD_PARTY_LICENSES.md 41104e462ab81280412cb34bc2c739b240371cd5 41104e462ab81280412cb34bc2c739b240371cd5
       --- AC7   container_tests/test_tls_lock.py -k one_process, then the grep -c
       one_process exit=0
       1 passed, 8 deselected in 4.38s
       0
       --- AC8   grep -c of each of the three names in docs/DEPLOYMENT.md
       2
       2
       2
       --- AC9   uv run pytest -q -m structural
       exit=0
       101 passed, 2117 deselected in 21.16s
       --- AC10  the semgrep command as written
       exit=0
       Ran 225 rules on 451 files: 0 findings.
       --- AC11  git diff --name-only origin/main...HEAD
       .env.example
       container_tests/test_tls_lock.py
       docs/DEPLOYMENT.md
       docs/DESIGN_DECISIONS.md
       docs/changes/22-tls-client-certificate-lock.md
       src/glosswork/config.py
       src/glosswork/entrypoint.py
       tests/test_config.py
       tests/test_infra.py
       paths ending .pem, .key or .crt: 0
       --- AC12
       git status --short: []
       ```

       AC2's run printed no skip line under `-rs`, so no test was skipped. AC7's first
       half is read from a run of that one test, because `-q` output does not name the
       tests that pass.

## Accept

Each is run from the repository root on the finished branch, and each exit code is read
on its own line.

- **AC1. The settings turn the lock on, and a partial set refuses with its reason.**
  `uv run pytest -q tests/test_config.py tests/test_infra.py -k tls` exits 0. Every new
  test in the two files has `tls` in its name. The number selected is read from the
  run's own summary line and is at least fifteen: eleven config cases and four entry
  point cases (thirteen if judgment area 6 is struck). On `42e9d45` the same command
  selects nothing and exits 5, measured, so every test it selects is this change's.
- **AC2. The three lock cases hold against the real image, at the handshake.**
  `git status --short` prints nothing, then
  `env -u GW_IMAGE uv run pytest -q container_tests/test_tls_lock.py` exits 0 with no
  test skipped, then
  `docker image inspect glosswork:container-test --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'`
  prints what `git rev-parse HEAD` prints. With `GW_IMAGE` unset the suite builds the
  image from the working tree (P22); the three commands together are what make "the
  real image" mean this branch's and not one left over from checklist step 2 (F8).
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
  verifier reads section 4a and reports, one verdict each, whether it states in so many
  words: (a) that a `GW_TRUSTED_PROXY_IPS` wider than the operator's own proxy is safe
  only with the lock on; (b) that `*`, or a range covering visitors' addresses, is not
  made safe by the lock (judgment area 8); (c) that a workspace started with none of the
  three is unlocked and silent about it, and how to prove the lock from outside
  (judgment area 7). A grep proves the words are present; only reading proves the claim
  is made.
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

Run on 2026-10-02 by a session that did not write the plan, against an image it built
from this branch, with its own harness. P1 to P15 were rerun and all reproduced; P16 was
read and the structural lane rerun. Nothing in the plan's own measurements was found
wrong. What the pass found is what the plan had not asked. Every script and its output
is attached to PROD-60.

**Three findings change what the maintainer is asked to approve: F2, F3 and F4.** The
rest tighten the plan without changing its design.

- **F1. The lock itself holds against everything tried. No change.**
  *Ran:* the seven rows of P4 on both versions from the host, and its accept and refuse
  rows from three Linux client positions; six more certificate shapes (P17); anonymous,
  pre-shared-key and null-encryption suites and TLS 1.1 and 1.0 at security level 0
  (P21); `SSL_CERT_FILE`, `SSL_CERT_DIR`,
  `FORWARDED_ALLOW_IPS`, `WEB_CONCURRENCY` and `UVICORN_*` set in the container's
  environment (P21); the listening sockets and the process list (P21).
  *Showed:* no caller without a certificate chaining to the CA file got one byte, on any
  path; one listening socket, one process; no environment variable widened the trust.
  *Disposition:* none needed. P10's "one listener" is now measured, not read.

- **F2. With the lock on, `GW_TRUSTED_PROXY_IPS=*` is still forgeable. Changes the
  documentation the issue asks for.**
  *Ran:* P18. *Showed:* through a certificate holder that appends the visitor's address,
  as a real proxy does, `*` and `0.0.0.0/0` let twelve wrong sign-ins through with no
  `429`; the proxy's own range stopped the eleventh. uvicorn takes the first entry when
  it trusts everything, and the first entry is the visitor's.
  *Disposition:* fixed in place. Judgment area 8, section 4a item 4, `.env.example`, AC8
  (b), and the decision text. The plan's sentence "safe only behind the lock" was true
  and would have been read as "safe behind the lock".

- **F3. A misspelt or undelivered setting starts the workspace unlocked and silent.
  Changes the design: one more startup refusal, and one limit stated.**
  *Ran:* P19. *Showed:* with the plan's validation every partial or malformed value
  refuses, which is the plan's claim and it holds. Three wrong names (`GW_TLS_CERT`,
  `GW_TLS_KEY`, `GW_TLS_CLIENT_CA`), or `GW_TLS_CA_FILE` alone, start plain HTTP with no
  message, because the loader ignores names it does not know.
  *Disposition:* fixed in place for names under `GW_TLS_` (judgment area 6, the third
  refusal in `_check_tls`, two tests, M6); the maintainer can strike it. **Not fixed,
  and not fixable in the product:** none of the three arriving at all. Judgment area 7
  states it, section 4a item 2 tells the operator how to prove the lock from outside,
  and the check belongs to the control plane's provisioning (CP-33): refuse to publish a
  workspace until a connection with no certificate is refused.

- **F4. The container test's three observations do not show a lock. Changes the
  tests.**
  *Ran:* P20. *Showed:* "zero bytes, TLS 1.2 handshake fails, access count unchanged"
  held against the image's plain HTTP entry point and against a locked server whose
  certificate the client could not verify. As the plan stood, the refused-case tests
  failed on an unlocked server only because of the right-certificate request at their
  end. An edit that dropped or moved that request would have left tests that pass
  against no lock at all.
  *Disposition:* fixed in place. Each refused case now carries its own controls (the
  TLS 1.3 handshake returns with `version()` `TLSv1.3`; the TLS 1.2 failure is not a
  verification error; one probe builder), checklist step 2 requires each to fail on its
  control, and M8 runs them against an unlocked container. The direct measurement,
  the server's own handshake raising, is the new test in `tests/test_infra.py`.

- **F5. The TLS 1.3 symptom on Linux. Partly established; the risk is a red test, not
  an open lock.**
  *Ran:* the probe from a Linux client on loopback, from a second container over the
  bridge, and through a userland relay (P4, amended). *Showed:* three exception types
  across the paths; the handshake call returned on TLS 1.3 on every one.
  *Disposition:* the probe catches `OSError` on send and read. The release runners
  themselves stay "not established". A symptom nobody has seen there makes the test
  error, and the handshake test in `tests/` does not depend on a path at all.

- **F6. The file check living in `load_settings` reaches the admin CLI and a hand-run
  uvicorn. Kept.**
  *Ran:* read the four callers; `docker exec` in the running container reports uid 1000.
  *Showed:* the CLI runs as the server's user by default, so it reads what the server
  reads; half-set relay or bootstrap settings already stop the CLI in the same way, from
  the same function.
  *Disposition:* no design change. Stated under "What changes" and in section 4a item 7.
  Considered and declined: moving the open-for-reading check into the entry point alone,
  which would let `python -m glosswork.config`, the image's own configuration check,
  pass a configuration the server then refuses.

- **F7. Mutations M1 to M5: predictions confirmed, two sharpened, three added.**
  *Ran:* M1 and M2 on the wrapper and on the context uvicorn builds; M5 on the scratch
  prototype; M7 on the context. M3 and M4 need the product code and are predicted, not
  measured. *Showed:* M1 opens the lock to everyone. M2 opens it to a caller with no
  certificate and still refuses another CA's, so the other-CA test passing under M2 is
  correct. M7 refuses everyone.
  *Disposition:* checklist step 8 amended; M6, M7 and M8 added.

- **F8. AC2 could run against a stale image. Fixed.**
  *Ran:* read `container_tests/conftest.py:20-24`. *Showed:* `GW_IMAGE`, which
  checklist step 2 sets to an image built from `42e9d45`, wins over a fresh build if it
  is still exported.
  *Disposition:* AC2 unsets it and checks the built image's revision label against
  `HEAD`. The direction of the error was safe (a stale unfixed image fails AC2), and it
  would still have been a verdict about the wrong image.

- **F9. The lock is tested on a pull request only by argument names. Narrowed.**
  *Ran:* P22. *Showed:* `container_tests` runs at release and by hand. A release cannot
  carry a broken lock past it; a merge can.
  *Disposition:* the handshake test in `tests/test_infra.py` runs on every pipeline and
  builds the context with uvicorn's own code from the entry point's own arguments, so a
  dependency upgrade that changed what those arguments mean fails a required check.
  Not adopted: running `container_tests` in CI, which is a decision about CI and outside
  this change.

- **F10. What the "not established" list could hide.**
  *Intermediate CAs:* now established (P17). An intermediate is trusted whenever its
  root is named, and naming the intermediate alone locks everyone out. Section 4a item 5
  says so. *File ownership:* fails closed (P6, reproduced with a key readable only by
  another uid: `PermissionError`, exit 1, no listener). *The Linux symptom:* F5.
  *Also found, and written into section 4a item 8:* a certificate with no key-usage
  restriction from the same CA is accepted as a client, and a resumed session is not
  re-checked against the certificate.
  *Disposition:* documentation only. None opens the lock to a caller the CA did not
  sign.

**Not attacked, and why.** Anything on the far side of the certificate holder: whether
the proxy's own account can be made to forward another party's request is the hosting
operator's configuration (PLAN Q84), not this image. Timing and resource exhaustion
beyond P12. An OpenSSL older than 3.5.

## Verification

A separate session that wrote none of the plan, the tests or the code verified the branch
at `2152e58` on 2026-10-02, with `GW_IMAGE` unset. It ran the Accept block as the
verification of record and changed nothing in the repository. **All twelve criteria
passed.** Its output is under "Final Accept output".

AC8's three reading verdicts on section 4a, which only a reader can give: (a) stated, "A
value wider than your own proxy's address is safe only while the lock is on"; (b) stated,
"The lock does not make `*` safe"; (c) stated, "A workspace started with none of the three
settings is unlocked, and it says nothing about it", followed by the two-step outside
check.

What it reconstructed rather than read, in scratch copies made with `git archive` and
images under its own tags, checking for each image that the package inside it was the tree
it was built from:

- **The unfixed tree.** Against an image built from `42e9d45`: the container file exits 1
  with 8 failed and 1 passed, and the no-certificate, other-CA and same-name-CA cases each
  fail at their own TLS 1.3 control. The unit files give 12 failed and 3 failed, as step 2
  records.
- **M1 to M8.** Every row of step 8's table reproduced, in both halves, with the same
  counts.
- **M7's shadowed assertion.** With the trust-store count taken out of the handshake test
  in a scratch copy, the accepted case fails on its own under M7 (`the right certificate
  did not complete`, `CERTIFICATE_VERIFY_FAILED`). The same for M1 and M2 with the
  `verify_mode` assertion taken out: the refused-case handshakes fail on their own. So
  those cases were shadowed by an earlier assertion, not vacuous.
- **Two mutations nobody had named.** Dropping the upper-casing from the stray-name check
  fails exactly D2's test. Removing the file-open check fails exactly the missing-path
  test.
- **The lock, with none of the suite's code.** Certificates from the `openssl` CLI, clients
  `openssl s_client` and `curl`. The right certificate got `200` on TLS 1.3 and 1.2. No
  certificate, another CA and a second CA with the same name got zero bytes on both. Eight
  connections left two access events. A key readable only by another uid, and the three
  settings plus `GW_TLS_CERT`, each exit 1 with a `Configuration error:` line naming the
  variable. A silent connection is dropped at 60.0 s.
- **D1.** With step 3 done and step 4 not, `tests/test_config.py` exits 1 with the one
  failure D1 names, and with step 4 it exits 0. No version of step 3 passes that command
  without step 4's file, so this is a defect in the plan's step 3 check. The interim
  state is in no commit.
- **The build against "What changes".** It matches: the three fields, the before-validator,
  `tls_enabled`, the three refusals in the approved order with the approved copy, four
  arguments from one branch and none when off, the decision as proposed with judgment
  area 6 kept, section 4a's eight items in order. 22 environments through `load_settings`
  found no refusal added or missing.

**One finding outside the Accept block, known and accepted for this change.** The outside
check in section 4a gives one command in code, the request over `https://` with no client
certificate, and says it "prints `000` and exits non-zero". Measured on this image, that
is also true of a workspace with no lock: a locked one prints `000` and curl exits 52, an
unlocked one prints `000` and curl exits 35, because a plain HTTP server cannot answer a
TLS client. What tells the two apart is the sentence after the command (the same request
over `http://`, which an unlocked workspace answers `200` with exit 0) and the check's
second step, the startup log line. Followed in full the check works, which is why AC8 (c)
passes. Run as the `https://` request alone, it passes on exactly the case it exists to
catch, a workspace whose three settings never arrived. The maintainer chose on 2026-10-02
to ship the wording as verified, so section 4a is unchanged. A provisioning check built on
it needs the `http://` request refused, or curl's exit 52, or the log line, and never the
`https://` request alone.

**Error copy the maintainer read after the build and left as it is.** D8's message for two
of the three set. And the unreadable-file message for a file that does not exist, which
reads `cannot read <path> (No such file or directory)` and then the sentence about uid
1000 and readability, where readability is not the fault: the variable, the path and the
reason are all correct.

**Not established by anyone.** The client-side symptom of a TLS 1.3 refusal on the release
workflow's own Linux runners, and how a hosting platform delivers the three files. Both
are as "Premises" lists them.

## Final Accept output

The verifying session's run at `2152e58`, 2026-10-02, from the repository root. Every
`exit=` is the command's own status, read on the line after it, with output sent to a file
and no pipe.

```
HEAD 2152e5858ca086d78a33eb98882dbc59657ed7ef
--- AC1   uv run pytest -q tests/test_config.py tests/test_infra.py -k tls
16 passed, 33 deselected in 0.11s
exit=0
(on a git archive of 42e9d45, the same command: "33 deselected", exit=5)
--- AC2   git status --short
(prints nothing) exit=0
          env -u GW_IMAGE uv run pytest -q -rs container_tests/test_tls_lock.py
9 passed in 8.69s        (no skip line under -rs)
exit=0
          docker image inspect glosswork:container-test --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
2152e5858ca086d78a33eb98882dbc59657ed7ef
          git rev-parse HEAD
2152e5858ca086d78a33eb98882dbc59657ed7ef
          (beyond the block: the glosswork package copied out of that image, diff -r against src/glosswork: exit=0)
--- AC3   uv run pytest -q -rs container_tests
41 passed in 271.95s (0:04:31)
exit=0
--- AC4   uv run pytest -q
2215 passed, 3 xfailed, 2 warnings in 311.27s (0:05:11)
exit=0
--- AC5
uv run ruff check .            All checks passed!                        exit=0
uv run ruff format --check .   263 files already formatted               exit=0
uv run mypy src                Success: no issues found in 90 source files  exit=0
--- AC6   <path> <origin/main object id> <HEAD object id>
Dockerfile c4303b2f6c2543d9bbc2e3b0817e2ffa422606bd c4303b2f6c2543d9bbc2e3b0817e2ffa422606bd
pyproject.toml feecb0f199ee21ff9abe6d556d325ec353ddc473 feecb0f199ee21ff9abe6d556d325ec353ddc473
uv.lock 84a197533000a924d7ddd2763c627953d375f94f 84a197533000a924d7ddd2763c627953d375f94f
THIRD_PARTY_LICENSES.md 41104e462ab81280412cb34bc2c739b240371cd5 41104e462ab81280412cb34bc2c739b240371cd5
--- AC7   uv run pytest -v -rs container_tests/test_tls_lock.py (same image as AC2)
container_tests/test_tls_lock.py::test_there_is_one_process PASSED
9 passed in 7.79s   exit=0
          grep -c "subprocess\|Popen\|os.fork" src/glosswork/entrypoint.py
0    (grep exit=1, the zero count)
--- AC8   grep -c of each name in docs/DEPLOYMENT.md
GW_TLS_CERT_FILE 2   GW_TLS_KEY_FILE 2   GW_TLS_CLIENT_CA_FILE 2   (each exit=0)
          reading verdicts: (a) stated, (b) stated, (c) stated. See the verification comment.
--- AC9   uv run pytest -q -m structural
101 passed, 2117 deselected in 21.61s
exit=0
--- AC10  uvx --from semgrep==1.178.0 semgrep scan --config p/python --config p/javascript --config p/typescript --exclude src/glosswork/repositories/sqlite.py --metrics off --error .
Ran 225 rules on 451 files: 0 findings.
exit=0
--- AC11  git diff --name-only origin/main...HEAD
.env.example
container_tests/test_tls_lock.py
docs/DEPLOYMENT.md
docs/DESIGN_DECISIONS.md
docs/changes/22-tls-client-certificate-lock.md
src/glosswork/config.py
src/glosswork/entrypoint.py
tests/test_config.py
tests/test_infra.py
exit=0; paths ending .pem, .key or .crt: 0
--- AC12  git status --short
(prints nothing) exit=0
```

Fences, which cannot fail on a tree without this change and are not counted as coverage:
the none-set test in `tests/test_infra.py` (inside AC1's sixteen; M4 shows it can fail),
`test_there_is_one_process` (AC7), AC3's other container files, and AC6.

## Deviations from the approved plan

Recorded by the build run (run 3, 2026-10-02), as each happened. None changes the
design in "Judgment areas", a setting name, or what is refused.

- **D1. Checklist step 3's first command cannot exit 0 until step 4 is done.** Step 3
  adds the three fields and says `uv run pytest -q tests/test_config.py` exits 0. It
  exits 1 there, with one failure: `test_every_setting_config_reads_appears_in_env_example`,
  the test P8 names, which fails for any setting `.env.example` does not list. Step 4
  adds the section and the file exits 0. The steps were done in the order written and
  nothing was reordered; what moved is the point at which the command is green.

- **D2. One more config case than "What changes" lists: twelve, not eleven.** The
  stray-name test has a third case, `gw_tls_ca_file` in lower case and alone. "What
  changes" says names are compared upper-cased and lists no test for it, and without
  one a mutation that dropped the upper-casing would pass. The refusal names the
  variable as it is written in the environment. AC1's command selects 16.

- **D3. Two container tests are parametrized, so each case is measured on its own.**
  `test_another_cas_certificate_is_refused_at_the_handshake` has two cases (another
  CA; a second CA with the right CA's subject name) and
  `test_a_partial_set_refuses_to_start` three (client CA only; certificate and key
  only; the stray name only). As one function each, the later cases would only ever
  run after an earlier one had passed, and on the unfixed image they would never have
  been measured. Nine container cases in six functions.

- **D4. How the access count is closed.** "What changes" says to make one request with
  the right certificate and wait for the count to reach before plus one, and its
  controls say that request is made on each version. Both hold as written per
  version: for TLS 1.3 and then for TLS 1.2, count, make the refused attempt, make
  one right-certificate request on that version, wait, and assert the count is
  exactly before plus one. The wait is for the access event carrying that request's
  own `X-Request-ID`, not for a number, and every answered request this module sends
  is waited for the same way, the readiness probes included. Without that, an access
  line from an earlier test could land after a later test had read its count. The
  refused attempt carries its own id too, and the test asserts no access event has it.

- **D5. A timeout is not a refusal.** "What changes" says the probe catches `OSError` on
  the send and on the read. `TimeoutError` is an `OSError`, and catching it would let a
  server that hung for ten seconds read as zero bytes received. The probe raises it
  instead, which is the plan's own rule that a timeout is a failure and not a pass.

- **D6. The wait that starts each container has four outcomes, not three.** "What
  changes" names three for the partial-set test: exited, answering in plain HTTP,
  answering over TLS. The same wait starts the module's locked container, and it
  asserts nothing, so the refused-case tests reach their own TLS 1.3 control against
  an unlocked image (checklist step 2, M8). The fourth outcome is a server that
  completes a TLS 1.3 handshake and gives the right certificate no answer, which is
  what M7 produces; without it M7 would have been a fixture that timed out after 90
  seconds and nine errors, not five failed assertions.

- **D7. Section 4a carries three facts measured in this run.** Against a locked
  container from this branch's image, with curl 8.7.1 on macOS and no client
  certificate: `curl -sk -o /dev/null -w '%{http_code}'` printed `000` over `https://`
  and over `http://`; curl exited 52 on TLS 1.3 and over plain HTTP, and 35 when held
  to TLS 1.2; and the startup log line read `Uvicorn running on https://0.0.0.0:8000`.
  Item 2's instruction to prove the lock from outside is written as that command.
  4a also carries an example `docker run`, which the plan did not ask for.

- **D8. The partial-set message when two of the three are set.** The plan gives the
  copy for one set variable (`required when GW_TLS_CERT_FILE is set`). With two set
  it reads `required when GW_TLS_CERT_FILE and GW_TLS_KEY_FILE are set`; the rest of
  the sentence is the plan's. The unreadable-file message carries the plan's second
  sentence, about uid 1000, for every reason the system gives, a missing file
  included.

- **D9. Where DD-46 sits.** It is the last entry in docs/DESIGN_DECISIONS.md, after
  DD-45 and so under that file's "Interface" heading, because
  `test_every_design_decision_is_listed_once_in_ascending_order` requires the numbers
  to ascend through the file. DD-45, which is about sign-in, sits there for the same
  reason. Its text is the proposal under "Durable content", with judgment area 6 kept.

- **D10. The certificate helpers are written twice**, once in `tests/test_infra.py` and
  once in `container_tests/test_tls_lock.py`, about sixty lines each. Checklist step 1
  names three files and nothing else, and `container_tests` runs against a built image
  at release, so it does not import from `tests/`.

- **D11. The closeout raises the PRD's line bound, which is an edit to one test.** Found
  at closeout, by running the structural lane with FR-P11 in place. PRD.md stood at 618
  lines against `< 619` in `test_the_prd_is_the_durable_specification_and_stays_small`
  (`tests/test_documentation_structure.py`), so no new requirement of any length fits,
  and neither P16 nor "What changes" saw that guard. Its docstring says what to do: a
  genuine new requirement may raise the bound, in the change that adds it, with the
  reason recorded there. FR-P11 is five lines at the PRD's width, so the closeout commit
  raises the bound to 624 and records the reason in that docstring. The requirement's
  text is the wording approved in judgment area 5 and was not cut to fit. This is the
  only file under `tests/` the closeout touches, it asserts nothing about the lock, and
  it was not part of what the verifying session passed at `2152e58`: the closeout's own
  run of the Accept block on the final head is what covers it.

## Durable content moved out of this plan

Two parts moved during the build, because Accept reads them and because of P16. Two move
in the closeout commit that deletes this file.

- **docs/DESIGN_DECISIONS.md, DD-46**, "A workspace's own TLS is all three settings or
  none, and it always carries the client-certificate lock". Added in the build's first
  commit (checklist step 3), before any file cited its number (P16), with judgment area 6
  kept: the rule, why the half states and a misspelt name are refused, and that a wide
  `GW_TRUSTED_PROXY_IPS` is safe only behind the lock and `*` is not safe behind it
  either.
- **`docs/DEPLOYMENT.md` section 4a**, written during the build (checklist step 7), with
  the measured facts it needed from this plan: P4's table, P9's and P18's tables, P6, P12
  and P17.
- **PRD.md, new FR-P11**, in the closeout commit:

  > **FR-P11.** With `GW_TLS_CERT_FILE`, `GW_TLS_KEY_FILE` and `GW_TLS_CLIENT_CA_FILE`
  > all set, the process terminates TLS itself and completes a handshake only with a
  > client whose certificate chains to the named CA; no request from any other caller is
  > read. With some of them set, startup is refused naming what is missing, and with
  > any other name under `GW_TLS_` present it is refused naming that. With none, nothing
  > changes (DD-46).

  FR-P7 is unchanged. The PRD's line bound rises from 619 to 624 for it (D11).
- **AGENTS.md, Traps**, in the closeout commit: one entry, that a TLS 1.3 client's
  handshake returns before the server has checked the client certificate, so a test of a
  refused client asserts zero bytes received and never a particular exception (P4); and
  that zero bytes alone is also what a server with no TLS gives a TLS client, so the same
  test first asserts the TLS 1.3 handshake returned (P20).

Not moved, because it is not a rule of the product: what the control plane's provisioning
takes from this change (the outside check and its soft spot under "Verification", a TCP
health check, files readable by uid 1000, `GW_TRUSTED_PROXY_IPS` as the proxy's own range
and never `*`, the image's entry point left in place, a CA file holding one self-signed
root). Those are on that work's own task. A release that carries this change is its own
change.
