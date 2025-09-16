import sys
import os
import subprocess

def compile_catalogs():
    po_path = os.path.join("locale", "de", "LC_MESSAGES", "django.po")
    mo_path = os.path.join("locale", "de", "LC_MESSAGES", "django.mo")
    
    print(f"Compiling translation catalog from '{po_path}'...")
    if not os.path.exists(po_path):
        print(f"Error: Textual catalog '{po_path}' does not exist.")
        sys.exit(1)
        
    try:
        # Run msgfmt.py directly using standard python interpreter
        result = subprocess.run(
            [sys.executable, "msgfmt.py", "-o", mo_path, po_path],
            check=True,
            capture_output=True,
            text=True
        )
        print("Success! Translation catalog compiled successfully.")
        print(f"Binary catalog saved to: {mo_path}")
    except subprocess.CalledProcessError as e:
        print("Error during catalog compilation:")
        print(e.stderr)
        sys.exit(1)

if __name__ == "__main__":
    compile_catalogs()
