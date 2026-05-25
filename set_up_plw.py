from playwright.sync_api import sync_playwright
from time import sleep
import json
import random 

base_url = "https://docln.sbs/"

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
                user_data_dir="./hako_plw", 
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
    # when press enter, browser will close
    input("Press Enter to close the browser...")

print("Hoàn tất!")