import os
import asyncio
from apify_client import ApifyClient
from app.config.settings import settings

def check_apify():
    client = ApifyClient(settings.APIFY_TOKEN)
    
    # Get the latest run of practicaltools/contact-details-scraper
    run = client.actor("practicaltools/contact-details-scraper").last_run().get()
    if run:
        print(f"Found run: {run.get('id')}")
        ds_id = run.get("defaultDatasetId")
        print(f"Dataset ID: {ds_id}")
        
        items = list(client.dataset(ds_id).iterate_items())
        print(f"Number of items: {len(items)}")
        
        if items:
            print("Sample item keys:", items[0].keys())
            import json
            print("Sample item 1:", json.dumps(items[0], indent=2))
            
if __name__ == "__main__":
    check_apify()
