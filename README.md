# Dragonkind automation

Dockerized Python/Playwright runner for eight configured accounts. **Prototype: the live site's selectors, login flow, and death detection have not been validated.** The runner refuses to start until required configuration is supplied. It uses your explicit route rather than guessing which action is deadliest.

## Behavior

- Run accounts sequentially, requesting a fresh verification code for each login. Forward the full original email, including its original From/To headers, to `rljr.code@gmail.com` **after** the console asks for that account's code. If that account receives its code directly in this inbox, no forwarding is necessary. Do not forward stripped-down code-only messages or quoted email threads containing multiple codes.
- Only new inbox messages received after the request boundary are considered. Match the intended account, original sender, and one unique code. Inbox messages are read without marking them read or deleting them. Codes and credentials are not deliberately logged or persisted.
- Follow `route.json`. Text matching ignores case, spacing, curly apostrophes, and final punctuation. All choices require a unique exact match except the final “Hope for the best” phrase. Review the route against the site's real labels, especially “it's back.”
- At the configured checkpoint, pause that account if the third visible choice mentions “shadow.” Send an email update to `rljr.code@gmail.com`, save a local screenshot, close that browser context, and continue other accounts. This does not preserve a live browser; review may require manually signing in again.
- After a confirmed death, set that account's four-hour cooldown, sign out, verify the email login form, and continue the next eligible account. Cooldowns and pauses survive container restarts in `data/state.json`.
- Unexpected pages, ambiguous choices, timeouts, mail failures, or interrupted active runs pause the entire runner for review. It never solves CAPTCHAs or bypasses MFA. A failed notification leaves a persistent pending flag for retry on restart.

## Before you start

This is a prototype, not a ready-to-run live bot. Docker configuration and local browser simulations have been tested, but the Docker image build, real Gmail access, and actual website flow have not been verified. You must supply the website selectors and verification sender below before starting real account attempts.

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
| `CHOICES_SELECTOR` | Only story choices, in their displayed order; exclude menus/navigation |
| `DEATH_SELECTOR` | Element unique to the confirmed death screen |
| `SIGN_OUT_SELECTOR` | Logout control available on that death screen |
| `MFA_ORIGINAL_SENDER` | Confirmed sender: `hello@dragonkind.com` |

`MFA_SELECTOR` is confirmed as `#next-code`. Code entry automatically advances to the page containing “Brave Threshing.” Set `MFA_AUTO_SUBMIT=true` and `AUTH_READY_SELECTOR=a.button[href="/archway"]`; leave `MFA_SUBMIT_SELECTOR` blank. Story choices on subsequent pages, the death screen, and logout selectors are still unverified and must be supplied. Existing `.env` files are not automatically updated from `.env.example`; copy the changed settings manually while preserving your password.

Selectors accept Playwright syntax, including CSS and `text=...`. Inspect the actual site in browser developer tools. Do not invent selectors or use a broad `button` selector. A password step, iframe, custom code widget, differently structured story branches, or a logout confirmation may require adapting this prototype after live inspection.

The default code regex matches the supplied “Your confirmation code is …” wording and accepts six letters/digits with an optional middle hyphen (for example, `A1B-2C3`). It avoids treating ordinary six-letter words in the email as codes. Forwarded plain-text Gmail messages are supported; HTML-only forwards are rejected. Start with one account in `route.json` while validating the flow, then restore the other accounts. Use separate folders and no concurrent runners for the same state volume.

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

Read `data/state.json`, the notification, and the account's `data/*-paused.png`. Screenshots can contain account or story details; keep this folder private. Email updates contain the account, reason, and route step, but no screenshot or login code.

No automatic resume from the middle of a story is implemented. Resolve the event manually and reset the site's run to the initial route state first. Stop the service with `docker compose down`, then explicitly clear the selected account's pause:

```sh
docker compose run --rm dragonbot python -m dragonbot reset --account rljr.code@gmail.com --confirm-site-reset
docker compose up
```

This keeps any existing cooldown. Other paused accounts keep a global pause until reviewed and reset as well. Do not reset merely to retry an unexplained page mismatch. Check pending mail notifications before resetting.

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
