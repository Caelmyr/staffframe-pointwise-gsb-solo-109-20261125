"""Browser-level checks for the curved-line UI. Requires a running server and Playwright."""

import sys

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8123"


def run():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(BASE + "/")
        page.click("#practice")
        expect(page.locator("#workspace")).to_be_visible()
        page.wait_for_selector("canvas#capture")

        # Straight mode shows the manual red dashed lines by default.
        assert page.locator("input[name=staff_mode][value=straight]").is_checked()
        before_pitch = page.locator("#pitch").inner_text()

        # Preview curved lines without saving.
        rev_before = page.locator("#origin").inner_text()
        page.click("#trace-preview")
        expect(page.locator("#trace-status")).to_contain_text("预演")
        # The radio still reflects the saved (straight) mode; canvas shows curves.
        assert page.locator("input[name=staff_mode][value=straight]").is_checked()
        page.wait_for_function(
            "document.getElementById('trace-status').textContent.includes('待核 61 列')")

        # Adopt traced mode and save; reopening keeps it.
        page.check("input[name=staff_mode][value=traced]")
        page.click("#save")
        expect(page.locator("#notice")).to_contain_text("已采用墨迹弯线")
        assert page.locator("input[name=staff_mode][value=traced]").is_checked()
        expect(page.locator("#trace-status")).to_contain_text("待核 61 列")
        # Reopen from the library, exactly like returning to a saved card.
        card_id = page.evaluate("state.card.id")
        page.reload()
        page.select_option("#library", card_id)
        expect(page.locator("#workspace")).to_be_visible()
        assert page.locator("input[name=staff_mode][value=traced]").is_checked()
        expect(page.locator("#trace-status")).to_contain_text("待核 61 列")

        # Probe a review column: no pitch name; a normal column gets one.
        page.fill("#probe-x", "530")
        page.fill("#probe-y", "140")
        page.click("#probe-form button")
        expect(page.locator("#probe-result")).to_contain_text("待核")
        page.fill("#probe-x", "360")
        page.fill("#probe-y", "180")
        page.click("#probe-form button")
        expect(page.locator("#probe-result")).to_contain_text("C5")

        # Switch back to straight lines; the reading returns to the manual geometry.
        page.check("input[name=staff_mode][value=straight]")
        page.click("#save")
        expect(page.locator("#notice")).to_contain_text("手填直线")
        assert page.locator("#trace-status").inner_text() == ""

        assert not errors, errors
        print("UI checks passed; initial straight pitch was", before_pitch)
        browser.close()


if __name__ == "__main__":
    run()
