# Dragonkind automation

Dockerized Python/Playwright runner for eight configured accounts. **Prototype: the full automated login/game workflow and live death detection have not yet been validated.** The runner refuses to start until required configuration is supplied. It uses your explicit route rather than guessing which action is deadliest.

## Behavior

- Run accounts sequentially, requesting a fresh verification code for each login. Forward the full original email, including its original From/To headers, to `rljr.code@gmail.com` **after** the console asks for that account's code. If that account receives its code directly in this inbox, no forwarding is necessary. Do not forward stripped-down code-only messages or quoted email threads containing multiple codes.
- Only new inbox messages received after the request boundary are considered. Match the intended account, original sender, and one unique code. Inbox messages are read without marking them read or deleting them. Codes and credentials are not deliberately logged or persisted.
- Follow `route.json`. A radio choice plus its “Next Scene” submission is one route step; Continue and Brave Threshing controls are separate steps. Text matching ignores case, spacing, curly apostrophes, and final punctuation. All choices require a unique exact match except the final “Hope for the best” phrase. Review the route against the site's real labels, especially “it's back.”
- At the configured checkpoint, pause that account if any visible choice label mentions “shadow.” Send an email update to `rljr.code@gmail.com`, save a local screenshot, close that browser context and its browser process, and continue other accounts. This does not preserve a live browser; review may require manually signing in again.
- Follow the supplied death narrative Continue pages to the terminal retry result. Only then start that account's four-hour cooldown, open the result's Rotunda link, verify `#cooldown-label`, and click Profile → Sign out. Confirm the email login form before continuing with the next eligible account. Cooldowns and pauses survive container restarts in `data/state.json`.
- Unexpected pages, ambiguous choices, timeouts, mail failures, or interrupted active runs pause the entire runner for review. It never solves CAPTCHAs or bypasses MFA. A failed notification leaves a persistent pending flag for retry on restart.

## Before you start

This is a prototype, not a ready-to-run live bot. Docker configuration and local browser simulations have been tested, and the Docker image built successfully on the user’s Mac. Gmail IMAP/SMTP authentication, native Mac email submission, and forwarding have succeeded in user testing. Native automatic MFA retrieval and entry have now succeeded for one account. The full game route, eight-account rotation, and Docker login still need live validation. You must supply the website selectors and verification sender below before starting real account attempts.

Download or clone a copy of this project that contains `Dockerfile`, `compose.yaml`, `.env.example`, `route.json`, and the `dragonbot` folder. If GitHub still shows only the original README and LICENSE, the automation files have not been pushed there yet; cloning that version will not provide the app. All commands below assume your project folder is named `dragonkind` in Downloads; change the path to match your copy.

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) for your computer and open it. Wait for its engine to finish starting. You do not need to install Python separately to run the Docker app.

### macOS setup (Terminal)

Install the Docker Desktop version for your Mac's chip (Apple silicon or Intel). Then open Terminal:

```bash
cd "$HOME/Downloads/dragonkind"
docker --version
docker compose version
# Create .env only if it does not already exist; preserve existing credentials.
if [ ! -e .env ]; then cp .env.example .env; fi
chmod 600 .env
open -e .env
```

Use plain text when editing `.env`, save it, then continue with **Email and website settings** below. The container image must support your Mac's architecture; if Docker reports “no matching manifest,” the image/platform needs adjustment before you can run it.

### Windows setup (PowerShell)

Install Docker Desktop and use its WSL2 backend with **Linux containers**. Follow Docker Desktop's prompts to enable WSL2 and restart Windows if requested. Open PowerShell:

```powershell
Set-Location "$HOME\Downloads\dragonkind"
docker --version
docker compose version
# Preserve an existing .env file and its credentials.
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Save the file as `.env`, not `.env.txt`. The app runs inside Linux containers; the commands here run from Windows PowerShell. Continue with the settings below.

## Email and website settings (both systems)

Edit the local `.env` file to include:

```dotenv
MAIL_USER=rljr.code@gmail.com
MAIL_PASSWORD=replace-with-your-google-app-password-without-spaces
NOTIFY_TO=rljr.code@gmail.com
SHADOW_PAUSE_SCOPE=account
```

Replace the example password locally with your Google app password, not your regular Gmail password. Do not paste credentials in chat, commit them, or share `.env`. The project excludes `.env` from Git and the Docker build context. Google two-step verification and account permission to use app passwords are required; this prototype does not implement OAuth. IMAP and authenticated SMTP access must be allowed. Gmail access has not been tested with your account.

Configure forwarding from each of your other seven Gmail inboxes to `rljr.code@gmail.com`, preferably filtered to Dragonkind verification messages. Complete Gmail's forwarding confirmation. The `rljr.code@gmail.com` account already receives its own login messages directly. Preserve the original sender and recipient when forwarding; stripped-down code-only messages are not supported.

Required live-site configuration:

| Variable | Meaning |
| --- | --- |
| `LOGIN_ENTRY_SELECTOR` | Optional welcome button that reveals the login form; supplied HTML uses `button[data-reveal-trigger][data-step="4"]` |
| `EMAIL_SELECTOR` | Email login field; supplied HTML uses `#next-reader` (a text input, not type=email) |
| `EMAIL_SUBMIT_SELECTOR` | Supplied HTML uses `button[aria-label="Send me a code"][type="submit"]` |
| `MFA_SELECTOR` | Code field: `#next-code` |
| `MFA_AUTO_SUBMIT` | `true`: entering the code automatically advances; no submit click |
| `MFA_SUBMIT_SELECTOR` | Optional; required only if `MFA_AUTO_SUBMIT=false` |
| `AUTH_READY_SELECTOR` | Post-login marker: `a.button[href="/archway"]` (“Brave Threshing”); waited for, not clicked during authentication |
| `CHOICES_SELECTOR` | Supplied Brave Threshing links/buttons, `button[data-page-next]`, and `label[for^="choice-"]`, in document order; hidden controls are excluded |
| `CHOICE_SUBMIT_SELECTOR` | `button[data-choices-submit][form="choices"]`; used only after selecting a labeled radio option |
| `DEATH_SELECTOR` | Terminal result span containing the supplied “Reconsider your risk-to-reward ratios … come back in a few hours.” message; see `.env.example` |
| `DEATH_NARRATIVE_SELECTOR` | Supplied “Sometimes they don’t.” / “snapping your neck” narrative markers; these allow bounded Continue clicks but do not start cooldown |
| `DEATH_PAGE_NEXT_SELECTOR` | `button.onward[data-page-next]` on a recognized death narrative page only |
| `DEATH_CONTINUE_SELECTOR` | Leave blank for the supplied result page, which links directly to Rotunda |
| `ROTUNDA_SELECTOR` | `a.onward[href="/rotunda"]`, the final result’s Continue link; excludes the header navigation icon |
| `COOLDOWN_SELECTOR` | `#cooldown-label`, verified after entering Rotunda and before signing out |
| `PROFILE_SELECTOR` | `button[popovertarget="profile"]` opens the profile popover |
| `SIGN_OUT_SELECTOR` | `button.text-link[type="submit"]:text-is("Sign out")` in the visible profile popover |
| `MFA_ORIGINAL_SENDER` | Confirmed sender: `hello@dragonkind.com` |

`MFA_SELECTOR` is confirmed as `#next-code`. Code entry automatically advances to the page containing “Brave Threshing.” Set `MFA_AUTO_SUBMIT=true` and `AUTH_READY_SELECTOR=a.button[href="/archway"]`; leave `MFA_SUBMIT_SELECTOR` blank. Selectors for the supplied story controls are now included in `.env.example`. Each labeled option is selected and its linked radio input verified before clicking “Next Scene.” The post-death controls supplied by the user are now included: Continue, Rotunda, Profile, Sign out. The supplied terminal retry message now identifies the result, and `#cooldown-label` confirms the site cooldown after entering Rotunda. Death prose can vary; only the supplied narrative markers are automatically advanced. Other unrecognized endings pause for review. No live attempt has yet verified these selectors together. Existing `.env` files are not automatically updated from `.env.example`; copy the changed settings manually while preserving your password.

Selectors accept Playwright syntax, including CSS and `text=...`. Inspect the actual site in browser developer tools. Do not invent selectors or use a broad `button` selector. A password step, iframe, custom code widget, differently structured story branches, or a logout confirmation may require adapting this prototype after live inspection.

The default code regex matches the supplied “Your confirmation code is …” wording and accepts six letters/digits with an optional middle hyphen (for example, `A1B-2C3`). It avoids treating ordinary six-letter words in the email as codes. Forwarded plain-text Gmail messages are supported; HTML-only forwards are rejected. Start with one account in `route.json` while validating the flow, then restore the other accounts. Use separate folders and no concurrent runners for the same state volume.

### Email submission

`LOGIN_FLOW=recorded` is the default and follows the supplied codegen recording: click **Welcome, Candidate**, click the **Email Address** textbox, fill the configured address, and click the **Send me a code** button. These controls are located by their exact accessible role/name. MFA uses the **Six-character code** textbox, and successful login requires the **Brave Threshing** link to appear and the code field to disappear. The automated runner still reads the current verification email from the configured inbox. After the code field disappears, recorded mode opens `/rotunda` as in the recording and requires the authenticated Brave Threshing link before proceeding; a URL change alone never counts as successful login.

The default `LOGIN_EMAIL_CASE=original` preserves the configured email spelling/case. Existing `.env` files should set `LOGIN_FLOW=recorded` and `LOGIN_EMAIL_CASE=original` to match the successful recording. The CSS login selector settings and paced keyboard entry remain available with `LOGIN_FLOW=custom`; `EMAIL_TYPING_DELAY_MS` only applies in that mode. There is no fixed page-loading or post-entry sleep, and the obsolete `EMAIL_SUBMIT_DELAY_SECONDS` setting is ignored. Both modes wait for the control to be rendered/actionable before clicking.

Native form submission waits for DOM readiness and reports its HTTP status. An HTTP 422 response closes the browser, opens a fresh browser/form, and retries once, refreshing the inbox UID boundary before each submission. A second rejection, HTTP 429, or other HTTP 4xx/5xx response pauses and saves the rejection-page screenshot. The Docker HTTP 422 rejection remains unresolved in live testing; the recorded locator/input changes have only been validated with local browser fixtures.

### Slow intro and page loads

The illustrated intro can appear before the login controls become visible. The login flow waits for visible controls and checks that their ancestors are no longer transparent before typing/clicking, instead of treating an HTTP 200 response or `DOMContentLoaded` as readiness. `PAGE_LOAD_TIMEOUT_SECONDS=300` controls each browser navigation/action/readiness wait, including login and story scenes. The bot proceeds as soon as the required controls appear; it does not sleep for the full timeout. Each browser navigation/action/readiness wait is capped at five minutes (300 seconds), even if a larger value is configured. Smaller positive values are supported. Existing `.env` files should be updated to `PAGE_LOAD_TIMEOUT_SECONDS=300` to use the full cap. It prints a loading message before the initial wait. A timeout still pauses the runner; a loading screen alone does not establish a new entry button or a broken selector.

## Build and run (both systems)

Run these commands in Terminal on macOS or PowerShell on Windows, from the project folder, after saving all required settings:

```sh
docker compose build
docker compose run --rm dragonbot python -m dragonbot check
```

Only proceed if both commands succeed. `check` checks that required settings are present; it does not test the Gmail password, mail delivery, or live website selectors. A successful check does not establish readiness for unattended use.

For the first live trial, temporarily keep just one account in `route.json`. Run in the foreground so you can watch for the verification email request:

```sh
docker compose up
```

Forward the newly requested email if it is not forwarded automatically. Confirm one full login, expected story choices, death detection, logout, and email notification before restoring all eight accounts. A mismatch pauses the app for review rather than guessing another action.

Press **Control+C** to stop foreground operation on either system. After live validation, background operation uses:

```sh
docker compose up -d
docker compose logs -f
```

When following background logs, Control+C exits the log viewer and leaves the service running. To stop the service:

```sh
docker compose down
```

Keep Docker Desktop running and the computer awake for scheduling to continue. The service does not automatically restart after a crash or reboot; restart it explicitly and review any interrupted-run pause. After code or dependency changes, rebuild with `docker compose build` before starting again.

### Move from Mac to Windows (or back)

Stop the service on the old computer with `docker compose down`. Copy the project and `data/` privately to the new computer; `data/state.json` contains cooldowns and pauses. Create `.env` securely on the new computer, install Docker Desktop, and repeat build/check/start. Do not run both copies at once. Git does not transfer ignored `.env` or `data/` files. Running containers do not transfer between devices; each computer builds its own image.

### Common setup problems

- **Cannot connect to the Docker daemon:** open Docker Desktop and wait for its engine to start.
- **No compose.yaml / no configuration file:** change into the folder containing `compose.yaml`.
- **Missing .env:** follow the creation command for your operating system and check that Windows did not add `.txt`.
- **Missing configuration:** fill in every required selector and the genuine verification sender in `.env`.
- **Gmail login rejected:** check the app password, two-step verification, and account policy. Never put the password in logs or support messages.
- **Timed out waiting for a code:** check forwarding, original From/To headers, sender configuration, and the code format. Forward the current email after its request, not an old one.
- **Image download blocked:** the machine needs access to Microsoft's container registry and its image download hosts, plus the Python package registry during build. The cloud instance also needs website access; saved network settings do not prove access has been applied.

## Review a pause

Read `data/state.json`, the notification, and the account's `data/*-paused.png`. Paused screenshots finish CSS animations so a rejection message is not captured as an empty frame. Screenshots can contain account or story details; keep this folder private. Email updates contain the account, reason, and route step, but no screenshot or login code.

No automatic resume from the middle of a story is implemented. Resolve the event manually and reset the site's run to the initial route state first. Stop the service with `docker compose down`, then explicitly clear the selected account's pause:

```sh
docker compose run --rm dragonbot python -m dragonbot reset --account rljr.code@gmail.com --confirm-site-reset
docker compose up
```

This keeps any existing cooldown. Other configured accounts with a global-scope pause keep the runner paused until reviewed and reset. Accounts removed from the active `route.json` list retain their records and cooldowns but do not block the selected accounts. Account-only shadow pauses stay account-only. Re-adding an account with an unresolved global pause blocks the runner again. Do not reset merely to retry an unexplained page mismatch. Check pending mail notifications before resetting.

## Development checks (optional)

These tests use local browser fixtures and do not log into the real site or send email. To run them without Docker, install Python 3.12 or later first.

macOS Terminal:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m unittest discover -s tests -v
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests can also use an existing `chromium` executable on PATH or the browser at `TEST_CHROMIUM_PATH`. Use the existing checkout; a separate Git worktree is unnecessary.

## Diagnose a rejected login without sending another code

```sh
docker compose run --rm dragonbot python -m dragonbot diagnose-login --account leggejr.co@gmail.com
```

This opens the public login page, uses the same email typing and arrow-click functions as the runner, and intercepts the outgoing `/enter` POST before it reaches the server. All non-GET/HEAD requests are blocked during the diagnostic, and service workers are disabled so they cannot bypass that interception. No verification email is requested, game choices are not made, and saved pauses/cooldowns are untouched. Output includes the actual submitted `reader` email, form-field names, and booleans for cookie/CSRF/header presence. Token values, cookie values, other form values, and credentials are excluded. Compare the email and field names with a successful manual-browser submission. This does not reproduce a server response because the POST is deliberately blocked.

Public-page inspection confirmed `POST /enter` with the `reader` field and a hidden `_csrf_token`. The site sends `Referrer-Policy: no-referrer`, so an absent Referer header in diagnostics is expected; the app preserves that policy. Rendering/readiness changes and a bounded fresh-form retry have not yet been verified against the live address rejection.

## Record a manual walkthrough on macOS

Stop the bot first. Codegen runs a visible browser and Playwright Inspector on the Mac desktop, outside the headless Docker container. Python 3 is required; install it from python.org if `python3` is unavailable.

```bash
cd "$HOME/Downloads/dragonkind-git"
docker compose down
git pull --ff-only
bash scripts/record-mac.sh
```

The script prepares an ignored local `.venv`, installs the pinned Playwright version and Chromium, and starts codegen. Record the welcome screen, email entry, and arrow click. In Inspector, turn **Record off before entering MFA**, log in manually, then turn Record back on for the story, choices, death pages, Rotunda, Profile, and Sign out. If login is rejected in this visible browser too, stop and report that outcome. This is a manual live walkthrough and story choices can consume an account's available attempt.

Close the recorder browser when finished. Generated Python is saved in a timestamped `data/recording-*.py` file. Review it and remove verification-code literals before sharing it. Recording files stay local: `data/` is excluded from Git and Docker builds. Do not run the generated script automatically; it needs review and the scheduler/MFA logic added around the recorded actions. Codegen records locators and actions, not a full network diagnosis.

The automation now launches and closes a separate Chromium process for every account run. An HTTP 422 retry closes the old context and browser before launching its replacement. Browser shutdown runs on success, pause, and failure. The lightweight scheduler stays alive between eligible runs to enforce cooldowns; no game browser is kept open while it waits. `docker compose down` stops the scheduler/container completely. Closing the browser does not erase the server's account state or the local cooldown/pause records.

## Replay the recorded login on your Mac

To reproduce the visible Chromium login from codegen, stop Docker and run:

```bash
cd "$HOME/Downloads/dragonkind-git"
docker compose down
git pull --ff-only
bash scripts/replay-login-mac.sh ralphleggejr@gmail.com
```

This installs/uses native Playwright in `.venv`, opens a new visible browser, performs the recorded welcome → click email field → fill email → click arrow sequence, and waits for the code field. Enter the verification code directly in that browser. The replay waits for the code field to disappear, visits the recorded `/rotunda` URL when needed, and confirms authenticated UI (Brave Threshing, Profile, a cooldown, or an already-started game) before closing the entire browser. It stops before clicking any story choices. It does not load `.env`, read Gmail, record the code, save browser storage, reset the site's state, or change `data/state.json`. Supply another authorized account as the script's argument if needed. The normal Docker runner keeps automatic forwarded-email MFA and the full game route.

The replay requests a real login code. If it gets HTTP 422, it reports that response and closes; it does not retry. Its native desktop execution still needs validation on your Mac. This test separates visible Mac Chromium from the Docker runtime while keeping the recorded login actions consistent.

## Test automatic forwarded-email MFA in a visible Mac browser

After a successful code request/forwarding test, test inbox matching and automatic code entry without starting a game:

```bash
cd "$HOME/Downloads/dragonkind-git"
docker compose down
git pull --ff-only
bash scripts/test-login-mac.sh ralphleggejr@gmail.com
```

This launcher installs native Playwright and python-dotenv in `.venv`, opens visible Chromium, and reads mail settings from the local `.env` without overriding existing exported values. It establishes a fresh inbox UID boundary, requests one login code, waits for the matching forwarded email, and fills MFA automatically. Leave the browser untouched during this test; do not enter the code or click Brave Threshing yourself. The browser closes after authenticated UI is confirmed. A cooldown label or an already-started game can also confirm authentication, without starting/restarting the story. It does not modify pause/cooldown state, persist browser storage, record codes, or make game choices. Native Mac success does not establish Docker readiness.

The manual replay now reports user-closed windows as a cancelled test instead of a TargetClosedError traceback. Navigation to the recorded Rotunda URL happens only when the code field has disappeared and the browser has not already reached Rotunda, Archway, or Play; authenticated UI markers are still required. If you manually click through while replay is waiting, it can recognize authenticated game UI, but it never clicks those story controls itself.

## Run all eight accounts in the verified Mac browser mode

`route.json` lists these eight addresses in rotation order: `ralphleggejr@gmail.com`, `ralphleggejr.apple@gmail.com`, `leggetechsolutions@gmail.com`, `leggefamilyco@gmail.com`, `rljr.code@gmail.com`, `rlegge.code@gmail.com`, `leggejr.co@gmail.com`, and `10249southlawncir@gmail.com`.

If your local `route.json` still has the single-account test configuration, preserve that edit in a Git stash before pulling the new eight-account file. Keep the stash; do not pop it over the new configuration:

```bash
cd "$HOME/Downloads/dragonkind-git"
docker compose down
git stash push -m "Preserve single-account test configuration" -- route.json
git pull --ff-only
bash scripts/run-mac.sh --retry-login-errors
```

The launcher reads your local `.env` and runs the full scheduler with native **visible Chromium**, matching the login/MFA test that succeeded on your Mac. It uses recorded login actions and the configured email's original case. It runs one account at a time, obtains each account's MFA from the `rljr.code@gmail.com` inbox, follows the route, and closes Chromium completely after each account run and before a rejected-login retry. Keep forwarding enabled for the other seven accounts; the inbox account receives its own codes directly. Keep the Mac awake and leave automation browser windows untouched. Stop the scheduler with Control+C. Do not run Docker or a second native scheduler at the same time.

`--retry-login-errors` only releases saved step-0 pauses explicitly identified as HTTP 422 `LoginRequestRejected` failures from the old login flow. It retains the reason as history and preserves all cooldown deadlines. Shadow pauses, unknown timeouts, and game-stage failures are not cleared. Omit the flag on later launches if no such retries are needed. Do not delete `data/state.json`.

If a logged-in account already shows a site cooldown, the runner signs out, saves that account's advertised `data-countdown`/`datetime` deadline when available (otherwise a conservative four-hour delay), and moves on. An already-started game is paused for that account's review rather than guessing a route position. Confirmed bot deaths retain their separate four-hour cooldowns. The shadow checkpoint pauses only that account by default; other unexpected failures still pause the scheduler for review.

Native automatic login is verified for one account. The full story route, all eight accounts, and unattended operation still need live validation; watch the first rotation. Docker deployment remains supported by its existing commands but its login rejection has not yet been resolved.

If the browser closes and the Terminal prompt returns after the first account, inspect `data/state.json`. A global pause stops the remaining accounts even when all eight addresses are configured. The console now prints the pause reason and distinguishes entering MFA, waiting for the verification form to close, waiting for authenticated controls, and waiting for the story start/cooldown panel. `Login confirmed (authenticated)` means that a logged-in control such as Profile was found; the scheduler still needs Brave Threshing or the site's cooldown panel before proceeding.

Failures before the story starts also save `login_diagnostic` in that account's state and print it to the console. This contains only the known page path, document readiness, and counts/visibility of known controls. It excludes query strings, input values, verification codes, cookies, tokens, and page text. Share this diagnostic and the reported stage when troubleshooting. Do not delete the state or automatically clear unknown timeouts; retain cooldowns and review the cause before resetting the affected account. A successful login-only test does not clear an existing scheduler pause.
