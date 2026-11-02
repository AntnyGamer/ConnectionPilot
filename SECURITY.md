# Security and platform risk — ConnectionPilot v1

## LinkedIn terms and account exposure

This is an **unofficial** desktop program. LinkedIn prohibits third-party
software that automates invitations, even at low volumes. A rate limit,
private repository, dedicated browser profile, or cooldown does **not**
make use authorized. LinkedIn may impose account restrictions or permanently
suspend repeat violators. There is **no approved 500/day rate** or safe
minimum interval. This software neither bypasses verification screens nor
tries to disguise automation. The safest option for preserving account access
is to use LinkedIn's own connection controls or an approved integration.

Use it only if you understand these risks. This is not legal advice and there
is no guarantee against claims or liability arising from use.

## Sensitive local files — do not publish

The Windows folder `%USERPROFILE%\\ConnectionPilot` contains your LinkedIn
browser profile (including authentication cookies), profile information,
and invitation history. **Never copy this folder into the source checkout,
upload it to GitHub, send it to support, or share a screenshot containing
secrets.** The application intentionally keeps it separate from the source
folder. The repository `.gitignore` excludes common local and secret files,
but `.gitignore` cannot protect data that someone deliberately stages by force.

Use a separate Windows login with a password, BitLocker or device encryption,
Windows Update, and an up-to-date Chrome or Edge. The Google password is entered
only on Google's website, not into the app. Signing into a browser creates
persistent local session data; stop using the app and sign out of LinkedIn
in that dedicated profile to revoke that local session.

The app doesn't need your GitHub token. Do **not** place account credentials in
source files or commit history. If you publish the source publicly, everyone can inspect the code. Never
include local browser profiles, cookies, tokens, invitation history or personal
data. GitHub visibility does not alter LinkedIn's rules or eliminate legal
exposure.

## Application security limits

- The browser is visible, checks exact HTTPS LinkedIn hosts, and stops on
  recognized verification or restriction pages. No CAPTCHA bypass or proxies.
- Unknown invitations are never automatically retried; ambiguous recipients,
  redirects and confirmation dialogs fail closed.
- Browser exceptions are shortened and sanitized before local diagnostic logs.
- Pinned Python dependency versions support reproducible installation. Periodic
  dependency security updates are still necessary.
- Automated tests use local simulations, not a live authorized LinkedIn session.

If you discover a vulnerability, report it privately to the repository owner;
do not post account cookies, session files or personal data in public issues.
