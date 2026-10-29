# One-click Refresh trigger (optional)
1. Create a fine-grained GitHub token: only this repo, permission Actions: Read and write.
2. `npx wrangler login && npx wrangler secret put GITHUB_TOKEN && npx wrangler deploy` (in this folder).
3. Put the workers.dev URL in config.json `refresh_trigger_url` and push.
The token lives only in Cloudflare; the page just POSTs to the Worker (origin-locked, throttled to 1 run / 2 min).
