from cloakbrowser import launch
from time import sleep

def access_novelupdates():
    print("Launching CloakBrowser...")
    # Launch CloakBrowser. 
    # humanize=True adds human-like interactions to bypass behavioral detection.
    # We set headless=False so you can visually see it bypass the Cloudflare challenge.
    browser = launch(headless=False, humanize=True)
    
    # Create a new page
    page = browser.new_page()
    
    print("Navigating to NovelUpdates...")
    page.goto("https://www.novelupdates.com/")
    
    # Wait a few seconds to let Cloudflare Turnstile/verification clear
    # NovelUpdates usually has a 3-5 second waiting room
    print("Waiting for Cloudflare verification...")
    page.wait_for_timeout(6000) # Wait 6 seconds
    
    # Get the title to verify we successfully bypassed the block
    title = page.title()
    print(f"Success! Page Title: {title}")
    
    # Optional: Take a screenshot to prove it bypassed the check
    page.screenshot(path="novelupdates.png")
    
    # Pause to let you inspect the page
    input("Press Enter to close the browser...")
    
    # Cleanly close the browser
    browser.close()

if __name__ == "__main__":
    access_novelupdates()