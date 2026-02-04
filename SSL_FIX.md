# SSL Certificate Fix for macOS

If you see `SSLCertVerificationError` when running WebSocket scripts, try these fixes:

## Option 1: Install Certificates (Recommended)

Run this command in your terminal:

```bash
cd /Users/aaravoswal/Documents/GitHub/nubra-test
source .venv/bin/activate
python3 -m pip install --upgrade certifi
```

Then find and run the Install Certificates script:

```bash
# Find Python's Install Certificates script
find /Applications -name "Install Certificates.command" 2>/dev/null

# Or if Python was installed via Homebrew:
/Applications/Python\ 3.*/Install\ Certificates.command
```

## Option 2: Set Environment Variables

Before running the script, set:

```bash
export SSL_CERT_FILE=$(python3 -c "import certifi; print(certifi.where())")
export REQUESTS_CA_BUNDLE=$SSL_CERT_FILE
```

Then run your script.

## Option 3: Use System Python Certificates

If the above don't work, you might need to update your system's certificate store or use a different Python installation.
