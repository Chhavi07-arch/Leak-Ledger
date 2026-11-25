# Career-profile deployment

The existing Vercel adapter serves the real dashboard and runs the existing
Python reconciliation engine. It does not substitute stored screenshots or a
static result for execution. The demo uses the repository's synthetic dataset.

Python is pinned to 3.12 in `.python-version`. `vercel.json` bundles the source,
dataset, configuration and dashboard into the Python function. Environment files,
private keys, Git metadata and tests are excluded from the deployment bundle.

From this checkout, authenticate to the intended Vercel account if needed, then:

```sh
vercel --prod
```

Retain the actual production URL returned by Vercel. Check `/api/health`, open
the dashboard, run reconciliation, and compare the confirmed/flagged split and
false-match figures with `python3 harness/score.py`. Public error responses omit
internal tracebacks; diagnostics remain in the host's server logs.

The deployment has not been published from this checkout yet: the current
terminal cannot resolve the Vercel or GitHub hosts. Local route checks do not
establish that a public site is live.
