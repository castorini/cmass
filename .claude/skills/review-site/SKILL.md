---
name: review-site
description: Run the human-in-the-loop review site for projected questions - build its data from audited records, serve it, collect reviewer decisions and export reviewed JSONL. Use when asked to review projected questions, set up human review, show audit evidence to a reviewer, add a set to the review site, or export reviews. Triggers include "review site", "human review", "review the projections", "reviewer comments", "export reviews", "evidence review".
---

# Review site

Code in `review_site/` (details and the record format: `review_site/README.md`).

| task | command |
|---|---|
| try it on invented data | `python3 review_site/demo/make_demo.py && python3 review_site/serve.py` |
| build data for your sets | `python3 review_site/build_site_data.py config.json` (config: corpus, docs, datasets with id / name / title / group / records) |
| serve with on-disk saving | `python3 review_site/serve.py [--port 8765] [--site DIR]` |
| re-export reviews | `python3 review_site/reviews.py export` |

Outputs: `review_site/data/` (site data), `review_site/reviews/reviews.json` (the store, keyed
`<set>~<record_id>`), `review_site/reviewed/<set>_reviewed.jsonl` (records plus their `review`).

## Rules

- **This repository is public.** `data/`, `reviews/` and `reviewed/` hold the records under review and
  are gitignored: never commit them, never move them elsewhere in the repository, and never paste
  real questions into the demo. Run the review on the private data where it lives.
- The demo (`demo/make_demo.py`) is invented content, listed as `[synthetic]` in `.leak-scan-allow`.
  Extend it only with invented entities.
- Check the page in a real browser after changing `index.html` (desktop and phone widths); the
  inline script must still parse (`node --check` on the extracted script).
