import os
import sys
import json
import urllib.request
import urllib.error

# Client-side Python offboarding script template
# Ensure we use environment variables and do not hardcode credentials
API_TOKEN = os.environ.get("ITAMBOX_API_TOKEN")
BASE_URL = os.environ.get("ITAMBOX_BASE_URL", "http://localhost:8000")

def offboard_user(user_id):
    if not API_TOKEN:
        print("Error: ITAMBOX_API_TOKEN environment variable is not set.", file=sys.stderr)
        return False

    headers = {
        "Authorization": f"Token {API_TOKEN}",
        "Content-Type": "application/json"
    }

    # Step 1: Locate user's active asset assignments
    url = f"{BASE_URL}/api/v1/assets/assignments/?assigned_user_id={user_id}&is_active=true"
    req = urllib.request.Request(url, headers=headers)
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
            assignments = data.get("results", [])
    except urllib.error.URLError as e:
        print(f"API Error checking assignments: {e}", file=sys.stderr)
        return False

    # Step 2: Check in each asset to a location
    for assignment in assignments:
        asset_id = assignment.get("asset")
        checkin_url = f"{BASE_URL}/api/v1/assets/{asset_id}/checkin/"
        checkin_payload = json.dumps({
            "location": 1,
            "notes": "Automated offboarding check-in"
        }).encode('utf-8')
        
        checkin_req = urllib.request.Request(checkin_url, data=checkin_payload, headers=headers, method='POST')
        try:
            with urllib.request.urlopen(checkin_req) as response:
                pass
        except urllib.error.URLError as e:
            print(f"API Error checking in asset {asset_id}: {e}", file=sys.stderr)
            return False

    # Step 3: Deactivate user
    deactivate_url = f"{BASE_URL}/api/v1/organization/assetholders/{user_id}/"
    deactivate_payload = json.dumps({
        "status": "inactive"
    }).encode('utf-8')
    
    deactivate_req = urllib.request.Request(deactivate_url, data=deactivate_payload, headers=headers, method='PATCH')
    try:
        with urllib.request.urlopen(deactivate_req) as response:
            pass
    except urllib.error.URLError as e:
        print(f"API Error deactivating user: {e}", file=sys.stderr)
        return False

    return True

if __name__ == "__main__":
    if len(sys.argv) > 1:
        offboard_user(sys.argv[1])
    else:
        print("Usage: python offboard_user.py <user_id>")
