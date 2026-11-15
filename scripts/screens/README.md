# Screenshot scripts

The owner reviews every visual change from screenshots, so each change ends with a gallery.
These take them from the demo (synthetic data only) with the Playwright Chromium the browser
tests use.

```bash
npm --prefix web run build
uv run kitty-web --demo --port 8890          # prints a one-use link; each later run needs the "next link" it prints
node scripts/screens/tour.mjs "<link>" /tmp/design-labs/tour          # widget colours, forms, job list, the Kitty theme
node scripts/screens/animations.mjs "<link>" /tmp/design-labs/animations   # a video and slowed frames of the Animations module
node scripts/screens/calendar.mjs "<link>" /tmp/design-labs/calendar  # every calendar view (add --kitty for that theme too)
node scripts/screens/readme.mjs "<link>" /tmp/readme-shots            # the README's 1400x875 JPEGs
python3 scripts/screens/gallery.py /tmp/design-labs/tour "Title"      # index.html from the folder's shots.json
python3 -m http.server 8790 --directory /tmp/design-labs              # then open http://127.0.0.1:8790/tour/index.html
```

On NixOS set `PLAYWRIGHT_BROWSERS_PATH` to the store path of `playwright-driver.browsers` and
`PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true`, and run node as `nix shell nixpkgs#nodejs_22 -c node …`.
A demo serves `web/dist` as it is when a page loads: do not rebuild while a script is running.
The Kitty theme's stills are made by `scripts/kitty-theme-scenes.py` and `scripts/kitty-theme-render.mjs`.
