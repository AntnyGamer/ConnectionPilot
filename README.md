<img src="assets/connectionpilot.png" alt="ConnectionPilot briefcase and arrow logo" width="96" />

# ConnectionPilot v1 — Windows

**Version label: v1.** This label remains v1 for future bug fixes; it is not an incrementing release number. This is a third-party tool and is not affiliated with or endorsed by LinkedIn.

## Important limitations

LinkedIn prohibits unauthorized automated invitations, regardless of delay or volume. Your account can be restricted or suspended. **The 500/day setting is only a local upper bound, not a LinkedIn-approved sending limit.** There is no known universal safe invitation cooldown, and **0.1 seconds does not make automated sending safe**. Actual sending against a live LinkedIn account is not verified and LinkedIn's changing interface may break the automation. Invitations also require recipients to accept; a request is not a confirmed connection.

## Install on Windows

**Recommended:** Download the latest branded `ConnectionPilot.exe` from [GitHub Releases](https://github.com/AntnyGamer/ConnectionPilot/releases/latest). Python is not required for the standalone EXE. The program is unsigned, and Windows may display a SmartScreen warning.

**Run the source instead:**

1. Install Python 3.11 or newer from https://www.python.org/downloads/windows/ (enable **Add Python to PATH**).
2. Extract the ZIP. Double-click `Launch_Windows.cmd` to install any missing dependencies and open the app.
3. If you use Google sign-in: click **Sign in with Google (Chrome / Edge)**, complete LinkedIn's **Continue with Google** in the normal browser window, then close every window of that *dedicated* browser profile before clicking **Start campaign**.
4. Enter your search phrases, matching terms and optional Advanced filters. Once signed in, one click on **Start campaign** begins the sending workflow; no further connection clicks are requested.
5. Keep the program and the browser running during sending hours (9 AM–7 PM, New York time).

The source ZIP is Python source, not a compiled `.exe`. The first source-based launch requires network access to install dependencies. The Chrome or Edge sign-in profile is kept separate from your regular browser profile. ConnectionPilot enables Chromium's browser sandbox explicitly when opening the automated browser rather than relying on Playwright's unsafe default launch setting.

## Settings

| Option | Default | Supported range |
|---|---|---|
| Campaign goal | 200 | 1–5,000 total confirmed or uncertain attempts |
| Daily invitation cap | 500 | 1–500 local attempt cap |
| Cooldown | 90 seconds | 0.1–21,600 seconds, including fractions |
| Sending window | 9 AM–7 PM Eastern | Fixed |
| Advanced filters | Minimum relevance score 25 | Location, role, organization, school, sector, exclusions, degree and score |

Search phrases and relevant profile terms are **empty on a first installation**. Once a campaign has been started, its saved phrases and terms reappear when the app is relaunched; installing a newer EXE does not clear them. Use **Clear campaign data** only if you intend to erase the local invitation history, which can allow previous recipients to be approached again.

Keywords separated by commas represent alternatives (OR) within one field; active fields must all match (AND). Matching uses visible profile text, not validated LinkedIn metadata. A search may miss qualified people or include irrelevant ones. LinkedIn's built-in filters may be more accurate. The program will not invent acquaintances or emails to bypass relationship verification.

## Safety and data integrity

- Sign-in happens in a separate local Chrome/Edge profile if you select the Google option. ConnectionPilot never asks for your Google password.
- It checks that each opened profile URL and visible identity match the discovered candidate before attempting an invitation. Redirects and ambiguous identities are skipped.
- If a click may have sent an invitation but the site does not confirm, the candidate is marked **Unconfirmed**; it is not retried automatically. Uncertain attempts count toward your goal, daily cap and cooldown.
- Unconfirmed attempts are automatically set aside and the campaign continues to another person without sending a duplicate. A separate, persistent invitation-attempt record preserves the original attempt time even when a profile's status changes. The application does not convert obsolete database formats.
- The application prevents a second copy from starting against the same data folder, so two sessions cannot select and invite the same candidate simultaneously.
- Pressing **Pause** before a sending action begins prevents that action. A second pause/stop check prevents the final confirmation click if a dialog is open; the candidate stays under **Unconfirmed**. An action that was already clicked may still finish. Stop/pause does not override LinkedIn's restrictions.
- It halts on recognized verification, invitation-limit or restriction screens; it does not solve challenges. Normal invitation confirmation modals are handled automatically when their controls are unambiguous; unsupported relationship or email verification is skipped without asking you to click. This does not guarantee unattended operation if LinkedIn logs you out or presents a mandatory challenge.
- Search-page extraction failures do not cause a partial page to be marked as complete. Empty search pages are retried rather than assumed exhausted; pages with visible but nonmatching candidates advance to the next page.
- The sending window, daily cap and cooldown are checked again just before the sending action, so slow page loads cannot bypass them.
- Invitations and local statuses are saved in `%USERPROFILE%\ConnectionPilot\connectionpilot.sqlite3`. Browser session profiles are inside `%USERPROFILE%\ConnectionPilot`. **Do not share the session files**.

**Data format:** The application uses its current SQLite schema directly. It does not run migration code for previous ConnectionPilot versions. Keep your local data folder if you want to retain existing settings and invitation records; unsupported older database formats are not automatically converted.

## Local checks

Run tests with:

```powershell
py -3 -m unittest discover -s tests -v
```

Tests use simulated LinkedIn pages, not an authenticated LinkedIn account. A passing local test suite is not proof that current LinkedIn live invitation sending works.

## Unattended invitation handling

- The program handles clearly identified **Send without a note**, **Send invitation**, **Send now**, and contextual **Send** confirmation buttons on its own.
- If LinkedIn asks for relationship or private-email proof that the program cannot truthfully supply, that candidate is skipped automatically. It never invents relationship details or email addresses.
- If an invitation might have been initiated but cannot be confirmed, the candidate is marked **Unconfirmed**, never automatically retried, and the campaign continues to another candidate. This is a safety tradeoff; it may mean fewer confirmed invitations than requested.
- The app stops on sign-in expiration, account limits, security/verification challenges, or explicit Stop. Such service-side requirements cannot be guaranteed away; it is not possible to promise 100% successful unattended invitations.

## Reliability fixes in this v1 build
- Temporary failures before a Connect click receive at most two additional inspections, spaced 10 seconds apart. The count persists between app restarts; uncertain invitation attempts are never retried automatically.
- Empty search pages are retried twice with a delay before discovery stops.
- The last invitation attempt is selected by offset-aware chronological time, so clock fall-back and imported records cannot pick an older invitation.
- A session that redirects to LinkedIn login/authwall is now recognized as a halt condition rather than continuing through other profiles.

## Additional deep-review reliability corrections

- When search phrases are removed or replaced, the program discards **unattempted queued and filtered** leads from previous searches, then restarts discovery under the new phrases. Reordering searches or adding new searches does not discard existing leads. Recorded invitations, uncertain outcomes and other history remain intact. An old lead can still appear again if it matches the new searches.
- The campaign goal uses the **total number of recorded Connect attempts**, including attempts later marked skipped because LinkedIn requested verification. They are also counted toward the daily cap and cooldown. This is deliberately conservative: an attempted click is not a confirmed delivery, and a campaign may stop before the requested number of confirmed invitations.
- The dashboard shows total recorded attempts so this conservative goal is understandable.
- All settings validation uses explicit error handling, even when Python is launched with optimizations enabled.

### Further v1 reliability fixes

- The launcher resolves dependency and program paths from its own folder, even if started from a different working directory.
- When a candidate appears in more than one search, a stronger match updates the queued candidate's score and summary without changing their name, attempt state, or original discovery time.
- Pause also stops search-result collection before the next candidate is inspected. If paused partway through a page, that page remains pending; already queued profiles are deduplicated on resume.

Additional checks in this v1 build:

- A delayed invitation-confirmation dialog will not be submitted after the configured sending window closes or the daily attempt cap is exceeded. The earlier Connect action remains in the attempt ledger to avoid duplicate retries.
- Malformed or out-of-range saved goal/cap values recover to documented defaults on startup, without changing valid saved values.
- Search results containing only generic action links are not treated as successfully identified people; the page is retained for another inspection rather than silently skipped.

These corrections do **not** establish live-site compatibility or create an approved invitation rate.

## Privacy, security, and GitHub source

Read [SECURITY.md](SECURITY.md) before using or publishing the program. The
program does **not** disguise automation; it is not LinkedIn-approved, and no
cooldown makes it compliant with LinkedIn's User Agreement. The only dependable
way to avoid automation-related account restrictions is not to use unauthorized
automation. Choosing a private GitHub repository helps avoid accidental public
source publication; it does not reduce LinkedIn's account or legal rules.

The application uses an exact LinkedIn HTTPS-origin check and fails closed if
a page unexpectedly redirects off-site. Errors saved in local diagnostic logs
are shortened and redact URLs and obvious credential fields. Dependencies are
pinned for reproducible installation; update them after security review.

Your login session, cookies and contact history live **outside the source
folder**, under `%USERPROFILE%\ConnectionPilot`. Never upload or share that
folder. `.gitignore` and `scripts/prepublish_check.py` provide extra checks
against accidental publication, but they cannot prevent someone from
intentionally copying secrets. An automated GitHub workflow runs offline
simulations on each source change; it never signs into LinkedIn.

The source is hosted in the **public** `AntnyGamer/ConnectionPilot` GitHub repository.
Never commit the local browser profile or SQLite database.

## CI reliability and GitHub protections

Run `python scripts/run_tests.py` for isolated, timeout-bounded test suites. A
process that prints `OK` but fails to exit is reported as a failure. GitHub
Actions runs the same runner with a job timeout. Dependabot and CodeQL
workflows supply automated dependency and source scanning; GitHub-hosted
results must be checked before requiring their statuses.

See [GitHub repository protection setup](.github/PROTECTIONS.md) for the
**manual GitHub Settings** steps needed to actually enforce protection on
`main`, and enable secret scanning and Dependabot alerts.

## Dependency versions (verified November 2, 2026)

- `playwright==1.64.1` — latest stable Python Playwright release, published October 22, 2026.
- `tzdata==2026.6` — latest published Python time zone data, released October 28, 2026.
- GitHub Actions `actions/checkout@v7` and `actions/setup-python@v7` — current major versions.
- `github/codeql-action@v4` — latest supported major version.

The Windows launcher reads the pins directly from `requirements.txt` so
Dependabot updates remain in sync with runtime installation. The Windows-only
PyInstaller build dependency is separately pinned in `requirements-build.txt`
so Dependabot can also monitor its updates. A Playwright
upgrade also installs its corresponding Chromium browser if it is missing.
Release checks are performed with offline tests; signed-in LinkedIn behavior
requires separate real-world verification.

## Branding and Windows executable

The official **briefcase-and-arrow** logo is shared by the README, the desktop
header/window, the Windows taskbar, the branded executable, and future releases.
The transparent 256×256 `assets/connectionpilot.png` is the main icon; the
multi-resolution `assets/connectionpilot.ico` supplies the Windows icon.
For small Windows taskbar icons, the ICO contains custom simplified 16, 20,
24, 32, 40, and 48px frames (plus 64, 128, and 256px frames). The app loads
`assets/connectionpilot-header.png` at its native 48px size instead of using
Tkinter's pixel-skipping resize, and applies the Windows ICO after the PNG so
it cannot be overwritten by a lower-resolution scaled taskbar image.

To regenerate the size-optimized ICO and header PNG from the approved primary
logo, install Pillow for development only (`python -m pip install Pillow`)
and run `python scripts/build_icons.py`. Pillow is not required to run the
application or its normal test suite.

If Windows still shows the previous blurred icon immediately after updating,
unpin the old shortcut and pin the new `ConnectionPilot.exe`. Windows caches
icons for existing taskbar pins; an old pin may retain an earlier image. The
1280×640 `assets/social-preview.png` is the matching repository preview.
GitHub's repository social preview must be uploaded separately in
**Settings → General → Social preview**; committing the file does not change
that GitHub setting.
The stable displayed version remains **v1**.

The repo also contains a **Windows-only** GitHub Actions build at
[Actions](https://github.com/AntnyGamer/ConnectionPilot/actions/workflows/windows-build.yml)
that packages a `ConnectionPilot.exe` (Python not required on the user's PC),
embeds the icon and version metadata, and verifies the executable. Download
`ConnectionPilot-v1-Windows-EXE` from the successful workflow's Artifacts.
This is an unsigned executable and Windows SmartScreen may warn; no code
signing certificate is bundled. The executable uses Chrome/Edge for Google
sign-in and needs the relevant browser available on your PC.

The normal ZIP with `Launch_Windows.cmd` is still supported for users
who prefer running Python source. The `.exe` build is generated on Windows
CI rather than cross-compiled on Linux; a successful hosted build must be
confirmed before claiming a tested executable download exists.

## Automatic GitHub Releases

After a successful Windows build on a commit merged to `main`, GitHub Actions
publishes the verified `ConnectionPilot.exe`, its SHA-256 checksum, and the matching PNG and ICO logo assets on the
[latest GitHub release](https://github.com/AntnyGamer/ConnectionPilot/releases/latest).
Download the EXE from the release page; the desktop version always remains **v1**.
Each release tag includes a short commit ID (`v1-build-...`) so release assets
remain tied to their exact audited source rather than silently overwriting a
previous release. Pull request builds run checks and produce test artifacts but
**never** publish a public release. Publishing uses a write-scoped token only
after a successful Windows test/build job.

A standalone EXE does not contain Playwright's separately distributed Chromium.
If that browser is absent on the user's machine, the program falls back to
installed Chrome or Edge and reuses its dedicated sign-in profile. For Google
sign-in, click the existing sign-in button first and complete authentication
in the regular browser window. This does not circumvent service restrictions.
