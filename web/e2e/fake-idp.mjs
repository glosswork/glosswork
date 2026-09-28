#!/usr/bin/env node
/**
 * A minimal, dependency-free local OIDC provider for the e2e OIDC scenario
 * (`e2e/auth.spec.ts`). See the block comment above `OIDC_ISSUER_PORT` in
 * `e2e/constants.ts` for why this exists and what it deliberately does not check.
 *
 * Reads its configuration from the environment (set by `playwright.config.ts`, sourced
 * from `e2e/constants.ts`) rather than hardcoding it, so the two files cannot drift:
 * FAKE_IDP_PORT, OIDC_CLIENT_ID, OIDC_ADMIN_GROUP, OIDC_IDENTITY_EMAIL, OIDC_IDENTITY_NAME.
 */
import crypto from "node:crypto";
import http from "node:http";

const PORT = Number(process.env.FAKE_IDP_PORT);
const ISSUER = `http://localhost:${PORT}`;
const CLIENT_ID = process.env.OIDC_CLIENT_ID;
const ADMIN_GROUP = process.env.OIDC_ADMIN_GROUP;
const IDENTITY_EMAIL = process.env.OIDC_IDENTITY_EMAIL;
const IDENTITY_NAME = process.env.OIDC_IDENTITY_NAME;
const KID = "fake-idp-key-1";

for (const [name, value] of Object.entries({
  FAKE_IDP_PORT: PORT,
  OIDC_CLIENT_ID: CLIENT_ID,
  OIDC_ADMIN_GROUP: ADMIN_GROUP,
  OIDC_IDENTITY_EMAIL: IDENTITY_EMAIL,
  OIDC_IDENTITY_NAME: IDENTITY_NAME,
})) {
  if (!value) {
    throw new Error(`fake-idp.mjs: missing required env var ${name}`);
  }
}

const { publicKey, privateKey } = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
const jwk = { ...publicKey.export({ format: "jwk" }), kid: KID, alg: "RS256", use: "sig" };

// One authorization code per `/v1/authorize` redirect, consumed exactly once by
// `/v1/token` — enough to catch a client that skips the exchange or replays a code,
// without reimplementing PKCE verification (see the module doc comment above).
const issuedCodes = new Set();

function base64url(data) {
  return Buffer.from(data).toString("base64url");
}

function signIdToken() {
  const now = Math.floor(Date.now() / 1000);
  const header = { alg: "RS256", typ: "JWT", kid: KID };
  const claims = {
    iss: ISSUER,
    aud: CLIENT_ID,
    sub: "fake-idp-subject-1",
    exp: now + 600,
    iat: now,
    email: IDENTITY_EMAIL,
    name: IDENTITY_NAME,
    groups: [ADMIN_GROUP],
  };
  const signingInput = `${base64url(JSON.stringify(header))}.${base64url(JSON.stringify(claims))}`;
  const signature = crypto.sign("RSA-SHA256", Buffer.from(signingInput), privateKey);
  return `${signingInput}.${base64url(signature)}`;
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, ISSUER);

  if (req.method === "GET" && url.pathname === "/.well-known/openid-configuration") {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(
      JSON.stringify({
        issuer: ISSUER,
        authorization_endpoint: `${ISSUER}/v1/authorize`,
        token_endpoint: `${ISSUER}/v1/token`,
        jwks_uri: `${ISSUER}/jwks`,
      }),
    );
    return;
  }

  if (req.method === "GET" && url.pathname === "/jwks") {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ keys: [jwk] }));
    return;
  }

  if (req.method === "GET" && url.pathname === "/v1/authorize") {
    const redirectUri = url.searchParams.get("redirect_uri");
    const state = url.searchParams.get("state");
    if (!redirectUri) {
      res.writeHead(400, { "content-type": "text/plain" });
      res.end("missing redirect_uri");
      return;
    }
    const code = crypto.randomUUID();
    issuedCodes.add(code);
    const target = new URL(redirectUri);
    target.searchParams.set("code", code);
    if (state) {
      target.searchParams.set("state", state);
    }
    res.writeHead(302, { location: target.toString() });
    res.end();
    return;
  }

  if (req.method === "POST" && url.pathname === "/v1/token") {
    const body = new URLSearchParams(await readBody(req));
    const code = body.get("code");
    if (!code || !issuedCodes.has(code)) {
      res.writeHead(400, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "invalid_grant" }));
      return;
    }
    issuedCodes.delete(code); // one-time use, like a real authorization code
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ id_token: signIdToken(), token_type: "Bearer" }));
    return;
  }

  res.writeHead(404);
  res.end();
});

server.listen(PORT, () => {
  console.log(`fake-idp listening on ${ISSUER}`);
});
