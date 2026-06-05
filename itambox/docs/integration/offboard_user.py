#!/usr/bin/env python3
"""
ITAMbox User Offboarding Script

Reads environment variables ITAMBOX_API_TOKEN and ITAMBOX_BASE_URL,
accepts a user ID as a command-line argument, and performs:
1. Query all active asset assignments for the user (with pagination).
2. Check in each assigned asset.
3. Delete the user's asset holder profile.
"""

import json
import os
import sys
import urllib.error
import urllib.request


def get_env_or_exit(var_name: str, default: str = None) -> str:
    """Get an environment variable or return a default, exit if required and missing."""
    value = os.environ.get(var_name, default)
    if value is None:
        print(f"Error: Environment variable {var_name} is required but not set.", file=sys.stderr)
        sys.exit(1)
    return value


def make_api_request(url: str, method: str = "GET", data: dict = None, token: str = None) -> dict:
    """
    Make an HTTP request to the ITAMbox API.
    Returns the parsed JSON response.
    Raises SystemExit on failure.
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Token {token}",
    }

    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req) as response:
            response_data = response.read().decode("utf-8")
            if response_data:
                return json.loads(response_data)
            return {}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        print(f"HTTP Error {e.code} for {method} {url}: {error_body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"URL Error for {method} {url}: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"JSON decode error for {method} {url}: {e}", file=sys.stderr)
        sys.exit(1)


def get_all_assignments(base_url: str, user_id: str, token: str) -> list:
    """
    Retrieve all active asset assignments for the given user, handling pagination.
    Returns a list of assignment objects.
    """
    assignments = []
    url = f"{base_url}/api/assets/asset-assignments/?assigned_user_id={user_id}&is_active=true"

    while url:
        print(f"Fetching assignments from: {url}")
        response = make_api_request(url, method="GET", token=token)
        results = response.get("results", [])
        assignments.extend(results)
        url = response.get("next")
        if url:
            print(f"  Found {len(results)} assignments, following next page...")

    print(f"Total active assignments retrieved: {len(assignments)}")
    return assignments


def checkin_asset(base_url: str, asset_id: int, token: str) -> None:
    """Check in a single asset by its ID."""
    url = f"{base_url}/api/assets/assets/{asset_id}/checkin/"
    payload = {"notes": "Automated offboarding check-in"}
    print(f"  Checking in asset ID {asset_id}...")
    make_api_request(url, method="POST", data=payload, token=token)
    print(f"  Asset ID {asset_id} checked in successfully.")


def delete_asset_holder(base_url: str, user_id: str, token: str) -> None:
    """Delete/deactivate the user's asset holder profile."""
    url = f"{base_url}/api/organization/asset-holders/{user_id}/"
    print(f"Deleting asset holder profile for user ID {user_id}...")
    make_api_request(url, method="DELETE", token=token)
    print(f"Asset holder profile for user ID {user_id} deleted successfully.")


def main():
    # Read configuration
    token = get_env_or_exit("ITAMBOX_API_TOKEN")
    base_url = os.environ.get("ITAMBOX_BASE_URL", "http://localhost:8000").rstrip("/")

    # Validate command line argument
    if len(sys.argv) != 2:
        print("Usage: python offboard_user.py <user_id>", file=sys.stderr)
        sys.exit(1)

    user_id = sys.argv[1]
    if not user_id.isdigit():
        print(f"Error: user_id must be a numeric value, got '{user_id}'", file=sys.stderr)
        sys.exit(1)

    print(f"Starting offboarding for user ID: {user_id}")
    print(f"Using base URL: {base_url}")

    # Step 1: Get all active assignments
    print("\n--- Step 1: Retrieving active asset assignments ---")
    assignments = get_all_assignments(base_url, user_id, token)

    # Step 2: Check in each asset
    print("\n--- Step 2: Checking in assigned assets ---")
    for assignment in assignments:
        asset = assignment.get("asset", {})
        asset_id = asset.get("id")
        if asset_id is not None:
            checkin_asset(base_url, asset_id, token)
        else:
            print(f"  Warning: Assignment {assignment.get('id')} has no valid asset ID, skipping.")

    # Step 3: Delete the asset holder profile
    print("\n--- Step 3: Deleting asset holder profile ---")
    delete_asset_holder(base_url, user_id, token)

    print("\nOffboarding completed successfully.")


if __name__ == "__main__":
    main()
