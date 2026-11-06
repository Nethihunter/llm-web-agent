# Why Chrome Runs in a Container

## The Problem

Chrome can consume 4-6 GB of RAM on complex sites (SPAs, sites with heavy
JavaScript, ad-laden pages). On an 8th-gen i5 with 20 GB total RAM, a single
runaway Chrome tab can:

- Slow down the entire host system (swap thrashing)
- OOM-kill other processes (editor, terminal, browser for research)
- Leave zombie processes after a crash

This makes development painful and unreliable.

## The Solution

The agent runs Chrome in a **single, resource-limited Docker container**:

```bash
docker run --memory=2g --cpus=2 -p 127.0.0.1:9222:9222 ...
```

Three guarantees:

1. **Memory cap (2 GB)**: Chrome cannot exceed this limit. If it tries,
   Docker kills the container cleanly. The host stays responsive.
2. **CPU cap (2 cores)**: prevents CPU starvation of the agent itself
   and the developer's editor.
3. **Port binding (127.0.0.1)**: CDP is only reachable from localhost.
   No network exposure, no remote access.

## What This Is NOT

- **Not multi-session**: one container, one browser, one profile.
- **Not multi-account**: no profile rotation, no cookie isolation per user.
- **Not a VPN or IP-masking tool**: no proxy, no Tor, no VPN.
- **Not an anti-bot evasion tool**: no fingerprint spoofing, no
  user-agent rotation, no headless-detection bypass.

If you need any of the above, this project is the wrong tool.
Those use cases violate the Terms of Service of most websites and may be
illegal in your jurisdiction.

## Trade-offs

| Aspect | Local Chrome | Containerized Chrome |
|---|---|---|
| Startup time | ~0 s (already running) | ~5 s (docker run) |
| Memory safety | ❌ Host at risk | ✅ 2 GB hard cap |
| Reproducibility | ❌ Depends on host | ✅ Fixed image |
| Debugging | ✅ DevTools on host | ⚠️ Needs CDP client |
| Complexity | Low | Medium |

The container mode is **opt-in** (`--browser container`). For quick
iterations, use local Chrome. For long runs, use the container.

## Implementation

- `browser/container_manager.py` — lifecycle (build, start, stop, health)
- `browser/Dockerfile.browser` — minimal Chrome image (no VPN, no VNC)
- `browser/start-chrome.sh` — Xvfb + Chrome with CDP

Total: ~150 lines of Python + ~50 lines of Docker config.

## When to use each mode

Use **local Chrome** when:

- You are iterating on the agent code
- You want to use DevTools to inspect the DOM
- You already have Chrome open for other work

Use **containerized Chrome** when:

- You run long tasks (> 2 minutes)
- You run unattended (CI, cron, background)
- You want reproducible behavior across machines
- Your host machine has less than 8 GB of RAM
