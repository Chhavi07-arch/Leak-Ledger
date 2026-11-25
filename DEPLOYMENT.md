# Career-profile deployment

Live demo: **https://leak-ledger-beta.vercel.app**

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

Published and verified on November 25, 2026. The production URL serves the
dashboard without authentication. `/api/health` reports Python 3.12, and
`/api/run` successfully executes reconciliation. Its confirmed/flagged totals,
13 correct scoreable matches, and 5 adversarial refusals match the local run.
All 669 ground-truth assertions pass in the hosted response. These measurements
apply to the included synthetic dataset, not real customer transactions.

All 201 local unit tests passed before publication. The Vercel project is
`leak-ledger` in `chhavis-projects-e5f24557`; it was published using the CLI from
the `deploy/career-demo` branch. Git push alone does not redeploy it.
