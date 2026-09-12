# Self-hosted Decap GitHub OAuth proxy

This service completes the server-side part of the Decap CMS GitHub backend.
It is dependency-free, binds only to `127.0.0.1:9190`, and is exposed by
Caddy at `https://whm12.art/projects/oauth/`.

The browser flow is:

1. Decap opens `/auth?provider=github` in a popup.
2. The proxy creates a short-lived, HMAC-signed state and redirects to GitHub.
3. GitHub redirects to `/callback` with `code` and `state`.
4. The proxy checks the state cookie, exchanges the code server-side, validates
   the GitHub user and repository push permission, then posts the access token
   to the Decap opener and closes the popup.

Required production variables:

```dotenv
GITHUB_OAUTH_ID=the-client-id-from-github
GITHUB_OAUTH_SECRET=the-client-secret-from-github
OAUTH_PUBLIC_BASE_URL=https://whm12.art/projects/oauth
CMS_ORIGIN=https://whm12.art
OAUTH_STATE_SECRET=at-least-32-random-characters
GITHUB_OAUTH_SCOPE=public_repo,user
GITHUB_REPO=2667741708/server-network-assist
OAUTH_BIND=127.0.0.1
OAUTH_PORT=9190
```

The client secret and state secret are server-only. Never put them in
`site/blog/.env`, `public/admin/config.json`, JavaScript, or Git.
