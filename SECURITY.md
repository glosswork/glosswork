# Security

Glosswork holds credentials, per-object-type access grants, an audit trail, and uploaded
files. Treat a defect in any of those as a security issue rather than an ordinary bug.

## Reporting

**Do not open a public issue.** This binds the maintainer and the
agents that work on this project as much as anyone else: a security-shaped defect found while
working here is reported the same private way, never as a public issue. Email
hello@glosswork.dev with SECURITY in the subject.

Include what you did, what happened, what you expected, and the deployment's auth mode
(`GW_AUTH_MODE`). A curl line or an MCP call that reproduces it is worth more than a
description.

## Scope

In scope: authentication and session handling, the three authorization axes (credential scope,
system role, per-object-type grant), the attachment read rule, CSV import and export handling,
the request edge, and anything that lets a caller act with authority it was not granted.

Out of scope: findings that require an already-compromised admin credential, denial of service
through resource exhaustion on a self-hosted deployment the reporter controls, and missing
hardening headers on a deployment run without the documented reverse proxy
(see `docs/DEPLOYMENT.md`).

## What this project already assumes

These are decided, not open questions, and a report that re-raises one should say why the
reasoning is wrong rather than only that the behavior exists:

- **Every request needs a real credential.** No credential resolves to `401`, never to `admin`.
  A credential-exempt path runs as an anonymous principal at `read` scope, not as a bootstrap
  admin (DD-15).
- **A credential is a ceiling, never a grant.** What a call may do is
  `min(credential scope, granted level)`, and every object type is closed by default (DD-11).
- **Token revocation is not retroactive** for an attachment reference already written. This is
  recorded as deferred, not rejected (DD-12).
- **A capability credential narrows the ceiling and never widens it**, is single use, and is
  bound to one filename and content type (DD-16).
- **Changing your own password needs a browser session and the current password.** A personal
  access token is refused even with it. Every password change, yours or an administrator's reset,
  revokes every token and every other session the account holds, and a self-change that races an
  administrator's reset loses (DD-13).
- **`update_comment` refuses a non-author with `validation_failed`**, deliberately, while
  `delete_comment` refuses with `forbidden` or `insufficient_scope` depending on which axis fell
  short (DD-11).

`docs/DESIGN_DECISIONS.md` carries the reasoning for each.

## Operating securely

`docs/DEPLOYMENT.md` is the authority. The load-bearing settings: `GW_BASE_URL` must name the
deployment's own origin, `GW_COOKIE_SECURE` must stay on anywhere but plain-HTTP localhost, and
the first admin must be created out of band rather than left to a bootstrap default.
