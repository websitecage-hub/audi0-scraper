# Deploy to Render (free tier)

Two ways — pick one.

## Option A — dashboard (no API key needed)
1. Push this repo to GitHub (I do this when you send the repo URL + PAT).
2. render.com  ->  New  ->  Blueprint  ->  connect the GitHub repo.
   Render reads `render.yaml`: Docker web service `trending-audio-api`, free plan,
   /data mounted, health check /health.
3. Deploy. Wait for the build (~cron: installs scrapling + patchright chromium + ffmpeg).
4. Copy the service URL -> `https://<you>.onrender.com/health` then
   `POST https://<you>.onrender.com/v1/download {"url":"..."}`.

Note: render.yaml declares a 1GB disk. Free plan has no persistent disk; delete the
`disk:` block in render.yaml to deploy on free (audio then resets on redeploy), or keep
it if you're on the paid plan for persistence. Either way the app runs on free.

## Option B — API (I do it)
Give me:
  1. GitHub repo URL + PAT (repo scope)  -> I push the code.
  2. Render API key (rnd_...)            -> I call POST /v1/services to create the
     web service from the repo with these settings:
       - envVars: TL_LIBRARY=/data, PORT=8000, TL_MAX_LIBRARY_MB=700, TL_MAX_CONCURRENT=2
       - dockerfilePath: ./Dockerfile
       - healthCheckPath: /health
       - plan: free
Then poll the service until live and verify /health.

## Verify it works
```
curl https://<you>.onrender.com/health
curl -X POST https://<you>.onrender.com/v1/download \
     -H 'Content-Type: application/json' \
     -d '{"url":"https://soundcloud.com/elderbrook/inner-light-feat-bob-moses","niche":"test"}'
curl -O https://<you>.onrender.com/v1/file/test/<title>.mp3
```

## Free-tier behavior
- No persistent disk -> /data audio resets on redeploy. Library auto-prunes past
  TL_MAX_LIBRARY_MB (700MB) so the ephemeral disk never fills/crashes.
- Instance sleeps after ~15min idle; first request cold-starts (~1-2 min on Docker).
- Gunicorn (1 worker, 4 threads, 600s timeout) caps RAM to fit the 512MB free tier.
