# Frontend

Angular Material/CDK and Xterm.js interface for Server Network Assist.

```bash
npm ci
npm start
```

The development server expects the API on the same origin. For a complete local run, build the frontend and copy it into the Python package:

```bash
npm run build
cd ..
python scripts/build_frontend.py
server-network-assist serve --data ./data
```

`npm run e2e` expects a running console at `PANEL_TEST_URL` (default `http://127.0.0.1:9182`) and an initialized credential file at `PANEL_TEST_CREDENTIALS` (default `../data/initial-login.json`). Neither file should be committed.
