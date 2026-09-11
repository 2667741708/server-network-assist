# Documentation screenshots

The PNG files in this directory are browser captures of the project's shipped UI, rendered with synthetic API fixtures. They do not contain production host inventories, credentials, or measured performance results. Addresses in `192.0.2.0/24`, host names, tunnel counters, timing, and connectivity outcomes are demonstration data.

| File | What it illustrates |
| --- | --- |
| `windows-proxy-diagnosis.png` | Direct access succeeds while access through Windows user proxy settings fails |
| `mixed-network-profile.png` | An Ubuntu gateway and Windows/Ubuntu clients in a profile before activation |
| `desktop-overview.png` | A healthy local desktop status overview |
| `disconnect-confirmation.png` | The confirmation dialog, captured without confirming the operation |
| `proxy-sharing.png` | The optional upstream HTTP/HTTPS proxy sharing fields, before saving or enabling |

## Reproduce

From the repository root, with Node.js and npm installed:

```text
cd frontend
npm ci
npx playwright install chromium
node capture-docs.cjs
```

The capture script serves packaged assets from `src/server_network_assist/ui` and `src/server_network_assist/desktop_ui` on a temporary loopback port. Playwright intercepts API requests and supplies fixtures; it does not run a backend, connect to SSH hosts, or change any tunnel or proxy. Browser and server are closed after capture. The script reports unexpected API requests and page errors as failures.

If Angular source was changed, first rebuild and package its assets using the development commands in the root README. Review the resulting images before committing them. Keep the demonstration-data notice in the READMEs when replacing or reusing these screenshots.
