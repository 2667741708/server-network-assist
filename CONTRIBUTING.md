# Contributing

Issues and pull requests are welcome. Keep changes focused and do not include environment-specific addresses, credentials, generated databases, or deployment logs.

Before opening a pull request:

```bash
cd frontend
npm ci
npm test -- --watch=false
npm run build
cd ..
python scripts/build_frontend.py
python -m unittest discover -s tests -v
```

Network-helper changes must document the failure mode and recovery path. UI changes must preserve full access to long IPs, routes, fingerprints, paths, timestamps and error messages on narrow screens.
