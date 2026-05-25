from cloakbrowser import launch_persistent_context
from time import sleep
import json

base_url = "https://docln.sbs/"

context = launch_persistent_context(
            user_data_dir="./hako", 
            headless=False,
            humanize=True,
            viewport=None,
            ignore_default_args=["--no-sandbox", "--enable-automation"],
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
try:
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
    # when press enter, browser will close
    input("Press Enter to close the browser...")
finally:
    context.close()

print("\nHoàn tất!")