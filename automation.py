"""Visible browser workflow. Never bypasses challenges or LinkedIn limits."""
from __future__ import annotations

import re
import math
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from google_login import browser_profile, find_browser, SUPPORTED
from security import is_trusted_linkedin_page, safe_diagnostic

from logic import (has_stop_signal, in_work_hours, local_now, match_score,
                   clean_terms, names_match, normalize_profile, passes_profile_filters, search_result_name,
                   seconds_until_next_send, seconds_until_tomorrow, seconds_until_work_hours)

class StopRun(Exception):
    pass

class ProtectionScreen(Exception):
    pass


def browser_launch_settings(data_dir, channel):
    """Use the same independent browser profile for normal login and later work."""
    if channel != "chromium" and channel not in SUPPORTED:
        raise ValueError("Unknown browser channel")
    profile = (browser_profile(Path(data_dir), channel) if channel in SUPPORTED
               else Path(data_dir) / "browser-profile")
    return str(profile), ({"channel": channel} if channel in SUPPORTED else {})



def choose_browser_channel(requested, bundled_chromium):
    """Use installed Chrome/Edge when standalone builds lack Playwright Chromium."""
    if requested != "chromium":
        return requested
    if Path(bundled_chromium).is_file():
        return "chromium"
    try:
        channel, _ = find_browser()
        return channel
    except FileNotFoundError as exc:
        raise StopRun("No browser is available. Install Chrome or Edge, use Sign in "
                      "with Google, and then start the campaign again.") from exc


def check_protection(page):
    """Check page and common dialogs, fail closed when any account warning appears."""
    if not is_trusted_linkedin_page(page.url):
        raise ProtectionScreen("Browser left the verified LinkedIn HTTPS site; campaign stopped")
    path = urlsplit(page.url).path.casefold()
    if any(path == prefix or path.startswith(prefix + "/") for prefix in
           ("/login", "/authwall", "/checkpoint", "/challenge", "/uas", "/login-submit")):
        raise ProtectionScreen("LinkedIn displayed a sign-in, checkpoint or verification page")
    # A member's biography, activity, or job history can legitimately mention
    # "unusual activity", "captcha", or "security verification". Only treat
    # visible site-warning surfaces as account restrictions, not all page text.
    warning_surfaces = page.locator(
        '[role="alert"], [role="alertdialog"], [role="dialog"], '
        '[aria-live="assertive"], .artdeco-toast-item, .artdeco-inline-feedback, '
        'body > p, body > h1, body > h2'
    )
    for surface in warning_surfaces.all()[:50]:
        if surface.is_visible() and has_stop_signal(surface.inner_text(timeout=3000)):
            raise ProtectionScreen("LinkedIn displayed a limit, restriction or verification warning")
    # Some warnings have no dialog/alert role. A short standalone notice
    # may be the entire main region; do not scan a full profile biography.
    main = page.locator("main")
    if main.count():
        # The full main region can be enormous. Its total size matters here,
        # not the biography itself, so transfer at most 501 characters.
        # Simple test doubles implement inner_text without evaluate.
        try:
            text = main.first.evaluate("""el => {
                const t = el.innerText || '';
                return t.length > 500 ? t.slice(0, 500) + '…' : t;
            }""", timeout=3000).strip()
        except AttributeError:
            text = main.first.inner_text(timeout=3000).strip()
        if normalize_profile(page.url) is None:
            first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
            warning = len(text) <= 500 and has_stop_signal(first_line)
        else:
            explicit = ("you've reached the invitation limit",
                        "you've reached the weekly invitation limit",
                        "invitation limit reached", "you're out of invitations",
                        "your account has been restricted",
                        "your account has been temporarily restricted", "too many invitations")
            warning = len(text) <= 250 and any(
                line.strip().casefold().replace("’", "'").startswith(explicit)
                for line in text.splitlines()
            )
        if warning:
            raise ProtectionScreen("LinkedIn displayed a limit, restriction or verification warning")


def wait_or_stop(seconds, stop_event, pause_event, status):
    """Responsive wait that can pause without sending invitations."""
    until = time.monotonic() + seconds
    last_bucket = None
    while not stop_event.is_set():
        if pause_event.is_set():
            status("Paused. No requests will be sent.")
            paused_at = time.monotonic()
            while pause_event.is_set() and not stop_event.is_set():
                stop_event.wait(.5)
            # Pausing does not consume the remaining cooldown.
            until += time.monotonic() - paused_at
            continue
        remaining = until - time.monotonic()
        if remaining <= 0:
            return
        if remaining < 1:
            bucket = ("seconds", "less than 1 second")
        elif remaining < 60:
            bucket = ("seconds", f"{math.ceil(remaining)} second(s)")
        else:
            bucket = ("minutes", f"{math.ceil(remaining / 60)} minute(s)")
        if bucket != last_bucket:
            status(f"Next action in {bucket[1]}. You can pause or stop.")
            last_bucket = bucket
        stop_event.wait(min(1, remaining))
    raise StopRun()


def collect_results(page, store, searches, match_terms, stop_event, notify, target, filters=None, pause_event=None):
    """Collect a small batch from LinkedIn's own People search, using visible cards."""
    # Only one page per search per pass; state stored to avoid re-reading page one repeatedly.
    added = 0
    minimum_score = (filters or {}).get("min_score", 1)
    exclusions = None
    for search in searches:
        if stop_event.is_set():
            raise StopRun()
        if pause_event is not None and pause_event.is_set():
            return added
        key = "page:" + search
        try:
            page_no = int(store.setting(key, "1"))
        except (TypeError, ValueError, OverflowError):
            page_no = 1
        if page_no < 1:
            page_no = 1
        if page_no > 15:
            continue
        search_url = "https://www.linkedin.com/search/results/people/?" + urlencode({
            "keywords": search, "origin": "GLOBAL_SEARCH_HEADER", "page": page_no})
        notify(f"Looking at page {page_no} for: {search}")
        page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        # Wait for actual search results below instead of spending 1.7 seconds
        # on every page even when the cards are already available.
        check_protection(page)
        if "/login" in page.url.lower() or "/authwall" in page.url.lower():
            raise StopRun("LinkedIn has logged you out; sign in and restart.")
        # Profile links inside visible main-content search results.
        anchors = page.locator('main a[href*="/in/"]')
        # A slow-loading result list is not proof that the search is exhausted.
        anchor_count = anchors.count()
        if not anchor_count:
            try:
                anchors.first.wait_for(state="attached", timeout=6000)
                anchor_count = anchors.count()
            except Exception:
                notify(f"No profile links appeared for '{search}'; retaining page {page_no} for a future retry.")
                continue
        seen_this_page = set()
        valid_profile_links = set()
        named_profile_links = set()
        extraction_errors = 0
        # Scan the *entire* displayed results page rather than silently
        # discarding links past an arbitrary first-80 cutoff.
        paused_during_page = False
        for i in range(anchor_count):
            if stop_event.is_set():
                raise StopRun()
            if pause_event is not None and pause_event.is_set():
                paused_during_page = True
                break
            anchor = anchors.nth(i)
            try:
                if not anchor.is_visible(timeout=1000):
                    continue
                url = normalize_profile(anchor.get_attribute("href") or "")
                if not url:
                    continue
                valid_profile_links.add(url)
                if url in seen_this_page:
                    continue
                name = search_result_name(anchor.inner_text(timeout=1200) or "")
                if not name:
                    name = search_result_name("", anchor.get_attribute("aria-label") or "")
                # Ignore generic profile actions before reading surrounding
                # card text. They are not identified people.
                if not name:
                    continue
                named_profile_links.add(url)
                # An explicit first-degree relationship on the result card
                # means this person is already connected. Avoid opening them.
                # A plain "1st" in unrelated profile text is not sufficient.
                # Search UI changes frequently. Use nearest card with enough text.
                snippet = anchor.evaluate("""el => {
                  let n=el; for(let j=0;j<6 && n;j++, n=n.parentElement){
                    const t=(n.innerText||'').trim();
                    if(t.length>=60 && t.length<1200) return t;
                  }
                  return (el.innerText||'').trim();
                }""") or ""
                if re.search(r"\b1st[-\s]+degree(?:[-\s]+connection)?\b", snippet, re.I):
                    continue
                score = match_score(snippet, match_terms)
                if score < max(0, int(minimum_score)):
                    continue
                if exclusions is None:
                    exclusions = clean_terms((filters or {}).get("exclude"))
                if exclusions:
                    folded_snippet = snippet.casefold()
                    if any(term in folded_snippet for term in exclusions):
                        continue
                seen_this_page.add(url)
                if store.add(url, name, snippet, score):
                    added += 1
            except Exception:
                # Retain the current page number: otherwise a temporary DOM or
                # database error can silently discard people we never examined.
                extraction_errors += 1
                continue
        if paused_during_page:
            # Keep this page pending: already collected leads are deduplicated
            # and the rest can be inspected safely after Resume.
            return added
        if extraction_errors:
            notify(f"Could not read {extraction_errors} search result(s) on page {page_no}; "
                   "keeping this page for another attempt.")
        elif named_profile_links:
            # A page with only non-matching candidates must still advance.
            # Count member names BEFORE applying filters, so filtered-out
            # profiles cannot trap discovery on the same search page.
            store.set_setting(key, page_no + 1)
        elif valid_profile_links:
            notify(f"Profile URLs appeared for '{search}', but no member names could be read; "
                   f"retaining page {page_no} rather than silently losing candidates.")
        else:
            notify(f"No usable profile links found for '{search}'. Search layout may have changed.")
        notify(f"Queued {added} new matches this pass.")
        # Stop once we have enough to process for the current day; later passes discover more.
        if store.counts().get("queued", 0) >= min(30, target):
            break
    return added


def _profile_intro(page, expected_name):
    """Resolve the actual profile introduction across legacy and newer LinkedIn layouts.

    Never use an arbitrary sidebar heading or recommendation card as identity.
    """
    main = page.locator("main").first
    if not main.count():
        return None, "No LinkedIn main profile region found."

    # A genuine member title provides a second identity check. Generic
    # LinkedIn titles are ignored, but a named title must not contradict
    # the discovered member even if a recommendation has the same name.
    title = (page.title() or "").strip()
    title_match = re.fullmatch(r"(.+?)\s*[|–-]\s*LinkedIn", title, flags=re.I)
    title_name = ""
    if title_match:
        title_name = re.split(r"\s+[-–—]\s+", title_match.group(1).strip(), maxsplit=1)[0].strip()
        if title_name.casefold() in {"linkedin", "profile", "linkedin member"}:
            title_name = ""
        elif not names_match(expected_name, title_name):
            return None, "Name mismatch: page title identifies another member; nothing clicked."

    matching = []
    for selector in ('h1, h2, [role="heading"]', 'h3'):
        matching = [heading for heading in main.locator(selector).all()[:60]
                    if not heading.locator("xpath=ancestor::aside").count()
                    and heading.is_visible()
                    and names_match(expected_name, heading.inner_text(timeout=3000).strip())]
        if matching:
            break
    if not matching and title_name:
        # Newer layouts sometimes place the name in plain text rather
        # than a heading. Require title corroboration and one candidate.
        for selector, limit in (("p", 80), ("span", 120)):
            matching = [node for node in main.locator(selector).all()[:limit]
                        if not node.locator("xpath=ancestor::aside").count()
                        and node.is_visible()
                        and names_match(expected_name, node.inner_text(timeout=2000).strip())]
            if matching:
                break
    if len(matching) != 1:
        return None, "Name mismatch: couldn't identify one verified profile heading; nothing clicked."
    name_heading = matching[0]

    # Search outward from the verified name for the nearest *direct* Connect
    # control. A More-only inner wrapper is not the whole action panel: newer
    # layouts can put the visible Connect button in a sibling wrapper.
    current = name_heading.locator('xpath=..')
    fallback = None
    for _ in range(12):
        if not current.count():
            break
        if current.evaluate('el => el.tagName.toLowerCase() === "main"'):
            break
        actions = current.get_by_role("button", name=re.compile(
            r"^(?:Connect(?: with .+)?|Pending|Message|More(?: actions)?|Remove connection|Invite .* to connect)$", re.I))
        if any(action.is_visible() for action in actions.all()):
            # Never widen the panel into a sidebar or another person's card.
            # If there was a verified smaller panel, its More action is still
            # a safe fallback; an unrelated Connect action is not.
            headings = current.locator('h1, h2, h3, [role="heading"]').all()[:50]
            if any(h.is_visible() and not names_match(expected_name, h.inner_text(timeout=2000).strip())
                   for h in headings):
                return (fallback, None) if fallback is not None else (
                    None, "Action panel also contains other headings; nothing clicked.")
            if current.locator("aside").count():
                return (fallback, None) if fallback is not None else (
                    None, "Action panel contains a sidebar; nothing clicked.")

            connects = current.get_by_role("button", name=re.compile(
                r"^(?:Connect(?: with .+)?|Invite .* to connect)$", re.I))
            direct = [button for button in connects.all() if button.is_visible()]
            if len(direct) > 1:
                return None, "Multiple visible Connect controls; nothing clicked."
            if len(direct) == 1:
                return current, None
            # Pending/connected is definitive; do not widen to another Connect.
            if find_first_action(current, "Pending") or find_first_action(current, "Remove connection"):
                return current, None
            if fallback is None:
                fallback = current
            # Widen only for a verified More panel. A Message-only panel
            # could sit beside another person's recommendation card.
            if not find_first_action(current, "More") and not find_first_action(fallback, "More"):
                return current, None
        current = current.locator('xpath=..')
    if fallback is not None:
        return fallback, None
    return None, "Verified profile name, but couldn't locate its own action panel. Nothing clicked."


def find_first_action(intro, name):
    """Only accept one explicit action in the verified person's introduction."""
    pattern = (re.compile(r"^(?:Connect(?: with .+)?|Invite .* to connect)$", re.I) if name == "Connect"
               else re.compile(r"^" + re.escape(name) + (r"(?: actions)?$" if name == "More" else r"$"), re.I))
    buttons = intro.get_by_role("button", name=pattern)
    matches = [button for button in buttons.all() if button.is_visible()]
    return matches[0] if len(matches) == 1 else None


def invitation_dialog_action(dialog):
    """Identify a send control only inside a verified invitation dialog.

    Never click a generic Send control from a chat or message composer.
    Return (button, reason), with None for unsupported/ambiguous dialogs.
    """
    content = dialog.inner_text(timeout=3000)
    if re.search(r"How do you know|don.t know each other|enter.*email|email address|verify.*relationship", content, re.I):
        return None, "LinkedIn requires relationship or email confirmation; skipped automatically."
    patterns = (
        re.compile(r"^Send without (?:a )?note$", re.I),
        re.compile(r"^Send invitation$|^Send now$|^Send request$", re.I),
    )
    for pattern in patterns:
        choices = [b for b in dialog.get_by_role("button", name=pattern).all()
                   if b.is_visible() and b.is_enabled()]
        if len(choices) == 1:
            return choices[0], ""
        if len(choices) > 1:
            return None, "Multiple invitation send controls; left unconfirmed and continued."
    # Some invitation modals label the action simply 'Send'. Require clear
    # invitation context so we never send an unrelated direct message.
    if re.search(r"\b(invitation|invite|connect)\b", content, re.I) and not re.search(
        r"\b(message|inmail|chat|compose)\b", content, re.I
    ):
        choices = [b for b in dialog.get_by_role("button", name=re.compile(r"^Send$", re.I)).all()
                   if b.is_visible() and b.is_enabled()]
        if len(choices) == 1:
            return choices[0], ""
    return None, "Unrecognized invitation dialog; set aside automatically without another click."


def send_one(page, profile, store, notify, filters=None, match_terms=None,
             stop_event=None, pause_event=None, pre_click_check=None,
             pre_confirm_check=None):
    """Return sent/skipped/uncertain; never retry a potentially successful invitation."""
    url = profile["url"]
    notify("Checking " + profile["name"] + " …")
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    # Avoid a fixed per-profile delay. If LinkedIn is still rendering the
    # verified introduction, retry the safe inspection without clicking.
    check_protection(page)
    if "/login" in page.url.lower() or "/authwall" in page.url.lower():
        raise StopRun("Please sign in again in the browser before restarting.")
    # Never click actions on a redirect, even when it leads outside /in/.
    # A redirect to a feed, a company, or a different member is not the
    # intended recipient. Compare normalized URLs to tolerate query strings.
    if normalize_profile(page.url) != url:
        return "skipped", "Opened URL does not match the discovered profile; nothing clicked."
    header, error = _profile_intro(page, profile["name"])
    if header is None and not error.startswith("Name mismatch: page title"):
        for _ in range(5):
            page.wait_for_timeout(400)
            check_protection(page)
            if normalize_profile(page.url) != url:
                return "skipped", "Profile navigated during introduction loading; nothing clicked."
            header, error = _profile_intro(page, profile["name"])
            if header is not None:
                break
    if header is None:
        return "skipped", error
    # Check relationship before applying interest filters. Existing
    # connections should always be skipped, not relabeled as filtered.
    intro_text = header.inner_text(timeout=5000)
    degree_text = re.compile(r"^1st(?:[-\s]*degree(?:[-\s]*connection)?)?$", re.I)
    degree_badges = header.get_by_text(degree_text)
    first_degree = bool(re.search(r"\b1st(?:[-\s]*degree(?:[-\s]*connection)?)?\b", intro_text, re.I))
    if first_degree or any(badge.is_visible() for badge in degree_badges.all()[:30]):
        return "skipped", "Already a first-degree connection; no invitation needed."
    for label in ("Pending", "Remove connection"):
        if find_first_action(header, label):
            return "skipped", f"Already connected or invitation pending ({label})."
    if filters is not None:
        try:
            # Check the selected person's actual visible profile before inviting.
            # Absence of evidence causes a skip, never a guessed match.
            main_region = page.locator("main")
            # Preserve the existing 12,000-character filter window without
            # transferring the remainder of a potentially huge profile.
            try:
                visible = main_region.evaluate(
                    "el => (el.innerText || '').slice(0, 12000)", timeout=8000)
            except AttributeError:
                visible = main_region.inner_text(timeout=8000)[:12000]
            passed, reason = passes_profile_filters(
                visible, intro_text[:2000], filters, match_terms)
            if not passed:
                return "filtered", reason
        except Exception:
            return "filtered", "Profile information unavailable; could not verify filters."

    # Message may coexist with Connect (e.g. open-profile messaging),
    # so it isn't proof of an existing first-degree connection.
    connect = find_first_action(header, "Connect")
    if not connect:
        more = find_first_action(header, "More")
        if more:
            more.click()
            # The menu can render asynchronously; do not mislabel a slow
            # Connect entry as ambiguous because a fixed delay elapsed.
            # Only accept an explicitly labeled menu item once visible.
            menu_action = re.compile(r"^(?:Connect|Invite .* to connect)$", re.I)
            # Some LinkedIn menus expose their option as a button rather than
            # an ARIA menuitem. Only include buttons inside a real menu.
            candidates = page.get_by_role("menuitem", name=menu_action).or_(
                page.get_by_role("menu").get_by_role("button", name=menu_action))
            try:
                candidates.first.wait_for(state="visible", timeout=1600)
            except Exception:
                pass
            check_protection(page)
            visible = [c for c in candidates.all() if c.is_visible()]
            if len(visible) == 1:
                connect = visible[0]
            else:
                return "skipped", "No unambiguous Connect menu action."
        else:
            return "skipped", "No Connect option in verified profile introduction."

    # A pre-existing notice isn't evidence that *this* invitation was sent.
    sent_pattern = re.compile(r"Invitation sent|Your invitation has been sent|Invitation pending", re.I)
    existing_notice = page.get_by_text(sent_pattern)
    had_notice = any(node.is_visible() for node in existing_notice.all())
    # A late navigation or cross-site redirect must never initiate an invitation.
    if not is_trusted_linkedin_page(page.url) or normalize_profile(page.url) != url:
        return "skipped", "Profile navigated away before the Connect click; nothing clicked."
    # A Pause or Stop request received during preflight must prevent sending.
    # Leave the record queued, rather than labeling an unclicked invitation as sent.
    if stop_event is not None and stop_event.is_set():
        raise StopRun()
    if pause_event is not None and pause_event.is_set():
        return "paused", "Paused before sending; candidate remains queued."
    # Recheck the sending window, quota and cooldown AFTER page loading.
    # Loading a slow profile could otherwise allow sending beyond the cap.
    if pre_click_check is not None and not pre_click_check():
        return "deferred", "Sending conditions changed during preflight; candidate remains queued."
    # An already-disabled or vanished control cannot initiate a request.
    # Check before writing an attempt so a harmless no-op does not consume
    # the user's goal, cap or cooldown.
    if not connect.is_visible() or not connect.is_enabled():
        return "skipped", "Connect action is unavailable; nothing clicked."
    # Persist uncertainty immediately before the first potentially sending click.
    # A page-load failure before this point leaves the person safely queued.
    store.mark(url, "uncertain", "Connect action initiated; never retry automatically.")
    try:
        connect.click(timeout=7000)
        page.wait_for_timeout(700)
        check_protection(page)
        # Wait briefly for asynchronously rendered confirmation controls. A
        # direct Connect click can also send immediately without a dialog.
        visible_dialogs = []
        for _ in range(5):
            dialogs = page.get_by_role("dialog")
            visible_dialogs = [d for d in dialogs.all() if d.is_visible()]
            if visible_dialogs or find_first_action(header, "Pending"):
                break
            page.wait_for_timeout(300)
        check_protection(page)
        if len(visible_dialogs) > 1:
            return "uncertain", "Multiple visible dialogs; set aside automatically without a second click."
        if visible_dialogs:
            d = visible_dialogs[0]
            txt = d.inner_text(timeout=3000)
            if has_stop_signal(txt):
                raise ProtectionScreen("LinkedIn displayed an invitation warning")
            action, reason = invitation_dialog_action(d)
            if not action:
                if reason.startswith("LinkedIn requires"):
                    return "skipped", reason
                return "uncertain", reason
            # Honor Pause/Stop before the final action; do not retry an
            # invitation whose outcome might be uncertain after interruption.
            if ((stop_event is not None and stop_event.is_set()) or
                    (pause_event is not None and pause_event.is_set())):
                return "uncertain", "Confirmation interrupted; set aside automatically to prevent duplicates."
            # The confirmation must still belong to the original verified profile.
            if not is_trusted_linkedin_page(page.url) or normalize_profile(page.url) != url:
                return "uncertain", "Unexpected page navigation before confirmation; no second click."
            check_protection(page)
            # The dialog may have remained open past the sending window.
            # The first click is already recorded, so don't apply the
            # pre-click cooldown check here or try this invitation again.
            if pre_confirm_check is not None and not pre_confirm_check():
                return "uncertain", "Sending window or daily cap changed before final confirmation; no second click."
            action.click(timeout=7000)
        # A visible Pending state or a new confirmation needs no fixed wait.
        # Keep the previous 900ms maximum for asynchronous UI updates.
        pending = None
        new_notice = False
        sent_notice = page.get_by_text(sent_pattern)
        for attempt in range(4):
            pending = find_first_action(header, "Pending")
            new_notice = not had_notice and any(
                node.is_visible() for node in sent_notice.all())
            if pending or new_notice:
                break
            if attempt < 3:
                page.wait_for_timeout(300)
        check_protection(page)
        if pending or new_notice:
            return "sent", "Invitation confirmed by LinkedIn UI."
        return "uncertain", "Clicked invitation action but could not verify sending. Set aside automatically; no retry."
    except ProtectionScreen:
        raise
    except Exception as exc:
        return "uncertain", "UI error after click: " + safe_diagnostic(exc, 180)


def run_campaign(store, config, stop_event: threading.Event, pause_event: threading.Event, notify, on_update):
    """Run in a background thread. One click launches one visible browser session."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise StopRun("Playwright not installed. Run Launch Windows.cmd to install it.")
    data_dir = Path(config["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        requested_channel = config.get("browser_channel", "chromium")
        channel = choose_browser_channel(requested_channel, p.chromium.executable_path)
        if channel != requested_channel:
            notify("Playwright Chromium is not installed. Using installed Chrome/Edge instead; "
                   "sign in with the Google sign-in button first if this browser profile is new.")
        try:
            profile, options = browser_launch_settings(data_dir, channel)
        except ValueError as exc:
            raise StopRun("Unknown sign-in browser. Please use the Google sign-in button again.") from exc
        notify("Opening the saved " + (channel if channel in SUPPORTED else "Chromium") + " browser profile.")
        try:
            browser = p.chromium.launch_persistent_context(
                profile, headless=False, **options,
                viewport={"width": 1280, "height": 900}, locale="en-US")
        except Exception as exc:
            if channel in SUPPORTED:
                raise StopRun("Could not open the Google sign-in profile. Close the Chrome/Edge "
                              "window you used to sign in, then try Start again. " + safe_diagnostic(exc, 200)) from exc
            raise
        try:
            page = browser.pages[0] if browser.pages else browser.new_page()
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=45000)
            if channel in SUPPORTED:
                # Google sign-in must happen in the NORMAL browser, not the controlled one.
                # Only the already-established LinkedIn session is reused here.
                for _ in range(10):
                    if stop_event.is_set():
                        raise StopRun()
                    if "/feed" in page.url and "/login" not in page.url:
                        break
                    stop_event.wait(1)
                else:
                    raise StopRun("Not signed in. Close this browser, click 'Sign in with Google' "
                                  "in ConnectionPilot, finish signing in in normal Chrome/Edge, "
                                  "close that login window, then click Start campaign.")
            else:
                notify("If this is your first run, sign in to LinkedIn in the opened browser.")
                for _ in range(360):
                    if stop_event.is_set():
                        raise StopRun()
                    if "/feed" in page.url and "/login" not in page.url:
                        break
                    stop_event.wait(1)
                else:
                    raise StopRun("Login not completed. Sign in and click Start again.")
            check_protection(page)
            notify("Signed in. Campaign is running.")
            on_update()
            searches = [s.strip() for s in config["searches"] if s.strip()]
            match_terms = [s.strip() for s in config["match_terms"] if s.strip()]
            filters = config.get("filters", {})
            discovery_failures = 0
            while not stop_event.is_set():
                if pause_event.is_set():
                    wait_or_stop(2, stop_event, pause_event, notify)
                    continue
                counts = store.counts()
                attempted = store.total_attempts()
                if attempted >= config["target"]:
                    notify(f"Goal reached: {attempted} total invitation attempt(s), including "
                           f"{counts.get('sent', 0)} confirmed and {counts.get('uncertain', 0)} "
                           "unconfirmed. Some clicks may have been rejected by LinkedIn.")
                    return
                now = local_now()
                if not in_work_hours(now, config["start_hour"], config["end_hour"]):
                    wait_or_stop(seconds_until_work_hours(now, config["start_hour"], config["end_hour"]),
                                 stop_event, pause_event, notify)
                    continue
                today = store.attempts_today()
                if today >= config["daily_limit"]:
                    notify(f"Daily cap of {config['daily_limit']} reached. Waiting for tomorrow.")
                    wait_or_stop(seconds_until_tomorrow(now) + 60, stop_event, pause_event, notify)
                    continue
                remaining = seconds_until_next_send(now, store.last_attempt_at(), config["cooldown"])
                if remaining:
                    wait_or_stop(remaining, stop_event, pause_event, notify)
                    continue
                profile = store.next_profile()
                if not profile:
                    notify("Finding new matching professionals…")
                    before_pages = [store.setting("page:" + term, "1") for term in searches]
                    new_count = collect_results(page, store, searches, match_terms, stop_event, notify,
                                                config["target"], filters, pause_event)
                    on_update()
                    if pause_event.is_set():
                        continue
                    if not new_count:
                        after_pages = [store.setting("page:" + term, "1") for term in searches]
                        if after_pages != before_pages:
                            # Pages with only previously seen or nonmatching profiles
                            # must not prevent discovery on subsequent result pages.
                            discovery_failures = 0
                            notify("No new matches on this page; checking later search pages.")
                            continue
                        discovery_failures += 1
                        if discovery_failures >= 3:
                            raise StopRun("No matching results after three discovery attempts. "
                                          "The search layout may have changed or results may be exhausted.")
                        notify(f"Search temporarily returned no new profiles; retry "
                               f"{discovery_failures}/2 in 15 seconds.")
                        wait_or_stop(15, stop_event, pause_event, notify)
                        continue
                    discovery_failures = 0
                    profile = store.next_profile()
                if not profile:
                    raise StopRun("Could not select a queued candidate.")
                if pause_event.is_set():
                    continue
                # send_one marks the candidate uncertain immediately before the
                # first potentially sending click, not during harmless preflight.
                def can_send_now():
                    current = local_now()
                    return (in_work_hours(current, config["start_hour"], config["end_hour"])
                            and store.attempts_today() < config["daily_limit"]
                            and seconds_until_next_send(current, store.last_attempt_at(), config["cooldown"]) == 0)

                def can_confirm_now():
                    current = local_now()
                    # The invitation attempt has already entered the ledger.
                    # Rechecking the first-click cooldown would always reject
                    # this same attempt. Don't allow a *second* send after
                    # crossing the configured time window or daily cap.
                    return (in_work_hours(current, config["start_hour"], config["end_hour"])
                            and store.attempts_today() <= config["daily_limit"])

                try:
                    result, reason = send_one(page, profile, store, notify, filters, match_terms,
                                              stop_event, pause_event, can_send_now, can_confirm_now)
                except (StopRun, ProtectionScreen):
                    raise
                except Exception as exc:
                    # Navigation and DOM failures before the sending click
                    # should not require manual intervention. Preserve any
                    # existing uncertain ledger entry if the click began.
                    if store.has_attempt(profile["url"]):
                        result = "uncertain"
                        reason = "Browser error after an attempted click; set aside without retry: " + safe_diagnostic(exc, 150)
                    else:
                        retries = store.record_inspection_failure(profile["url"])
                        if retries < 3:
                            notify(f"Profile inspection failed before any invitation action "
                                   f"({retries}/3); retrying in 10 seconds: {safe_diagnostic(exc, 120)}")
                            wait_or_stop(10, stop_event, pause_event, notify)
                            continue
                        result = "skipped"
                        reason = "Could not inspect profile after three pre-click attempts: " + safe_diagnostic(exc, 150)
                if result in ("paused", "deferred"):
                    continue
                store.mark(profile["url"], result, reason)
                store.log(result, profile["name"] + " | " + reason)
                on_update()
                notify(f"{result.upper()}: {profile['name']}. {reason}")
                if result == "uncertain":
                    # Attempt ledger and queue selection make this a permanent
                    # non-retry. Continue with a different candidate instead
                    # of stopping for a manual confirmation.
                    notify("Unconfirmed attempt set aside; continuing automatically with another profile.")
                if result in ("skipped", "filtered"):
                    # Throttle skips too, so search/profile navigation isn't rapid-fire.
                    wait_or_stop(12, stop_event, pause_event, notify)
                    continue
            raise StopRun()
        finally:
            browser.close()
