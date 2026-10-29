"""Screenshots of Couchcast's real settings page, light and dark, at 1280x800 for the AMO listing.

    uv run --project e2e python e2e/screenshots.py              # writes amo/screenshots/

The helper runs against a throwaway home whose TV is called "Living Room". Marionette refuses to
navigate to extension pages, so the page is opened from Firefox's chrome context, which needs
geckodriver's --allow-system-access."""
import json, os, sys, tempfile, time
from pathlib import Path

from run import FIREFOX, prepare_extension, say
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import WebDriverWait

out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "amo" / "screenshots"
UUID = "5f0c2a1e-7d3b-4c8a-9e61-2b4d6f8a0c13"
out_dir.mkdir(parents=True, exist_ok=True)
home = Path(tempfile.mkdtemp(prefix="couchcast-shots-"))
(home / "config.json").write_text(json.dumps({"device": {"name": "Living Room", "identifier": "demo", "address": "192.0.2.10"}}))
os.environ["COUCHCAST_HOME"] = str(home)
extension = prepare_extension(pause_after_cast=True)

for theme in ("light", "dark"):
    options = Options()
    options.binary_location = FIREFOX
    options.add_argument("-headless")
    options.set_preference("media.volume_scale", "0.0")
    # A fixed internal id, so the settings page has a known address.
    options.set_preference("extensions.webextensions.uuids", json.dumps({"couchcast@vanities": UUID}))
    dark = theme == "dark"
    options.set_preference("ui.systemUsesDarkTheme", 1 if dark else 0)
    options.set_preference("layout.css.prefers-color-scheme.content-override", 0 if dark else 1)
    driver = webdriver.Firefox(options=options, service=Service(service_args=["--allow-system-access"]))
    try:
        driver.set_window_size(1280, 885)  # 85 px of browser chrome leaves a 1280x800 page
        driver.install_addon(str(extension), temporary=True)
        url = f"moz-extension://{UUID}/options.html"
        with driver.context(driver.CONTEXT_CHROME):
            driver.execute_script(
                "const win = Services.wm.getMostRecentWindow('navigator:browser');"
                "win.gBrowser.selectedTab = win.gBrowser.addTab(arguments[0], {triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal()});",
                url,
            )
            time.sleep(5)  # the page pings the helper and fills in its status
            browser = driver.execute_script("return Services.wm.getMostRecentWindow('navigator:browser').gBrowser.selectedBrowser")
            shot = out_dir / f"settings-{theme}.png"
            shot.write_bytes(browser.screenshot_as_png)
            say(f"{theme}: captured {url} -> {shot}")
    finally:
        driver.quit()
