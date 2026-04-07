"""
YT-Music-Downloader - Authentication Setup Helper.
This script guides you through capturing your browser's session headers 
to access your private library without needing a Google Cloud Project.
"""

import os
import sys
from ytmusicapi import setup

def setup_headers():
    """
    Uses the ytmusicapi library to generate 'headers.json'.
    This file is essential for the sync script to bypass login screens.
    """
    print("\n" + "="*50)
    print("   YT MUSIC AUTHENTICATION SETUP (BROWSER HEADERS)")
    print("="*50)
    print("\nThis script will generate 'headers.json'. Follow these steps accurately:")
    
    steps = [
        "1. Open [bold]music.youtube.com[/bold] in your browser and ensure you are logged in.",
        "2. Press [bold]F12[/bold] (Developer Tools) and go to the [bold]'Network'[/bold] tab.",
        "3. Refresh the page or click on your 'Library'.",
        "4. Look for a request named [bold]'browse'[/bold] (Type: fetch/XHR).",
        "5. [bold]Right-click[/bold] that request -> [bold]Copy[/bold] -> [bold]Copy request headers[/bold].",
        "6. Paste the headers into the terminal below (Ctrl+V) and press Enter twice."
    ]
    
    for step in steps:
        print(step.replace("[bold]", "\033[1m").replace("[/bold]", "\033[0m"))
    
    print("-" * 50)
    
    try:
        # This interactive call asks for headers and saves them to 'headers.json'
        setup(filepath="headers.json")
        print("\n\033[92m[SUCCESS]\033[0m 'headers.json' has been created!")
        print("You can now run 'python sync.py' to start downloading.")
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n\033[91m[ERROR]\033[0m Setup failed: {e}")

if __name__ == "__main__":
    setup_headers()
