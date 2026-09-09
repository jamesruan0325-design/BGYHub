# Claude Code + BGYHub Quick Start

Connect Claude Code to BGYHub in a few minutes using an Anthropic-compatible API endpoint.

## What You Need

Before starting, make sure you have:

- Claude Code installed
- A BGYHub API key
- Available BGYHub credit

> Keep your API key private. Never commit it to GitHub or share it publicly.

## 1. Set the BGYHub API Endpoint

BGYHub provides an Anthropic-compatible API endpoint for Claude Code.

### macOS / Linux

Open Terminal and run:

```bash
export ANTHROPIC_BASE_URL="https://api.bgyhub.com"
export ANTHROPIC_AUTH_TOKEN="YOUR_BGYHUB_API_KEY"
```

Replace `YOUR_BGYHUB_API_KEY` with your actual BGYHub API key.

## 2. Start Claude Code

In the same Terminal session, navigate to your project:

```bash
cd your-project
```

Then start Claude Code:

```bash
claude
```
Claude Code should now send requests through BGYHub.

## 3. Verify Usage

After making a request, open your BGYHub Customer Portal.

You can monitor:

- Remaining balance
- Successful and failed requests
- Token consumption
- Model usage
- Recent requests
- Usage spend

If the request appears in Recent Requests, your connection is working.

## VS Code Extension

If you use the Claude Code VS Code extension, add the BGYHub endpoint and API key to your Claude Code environment variables.

Example:

{
  "claudeCode.environmentVariables": [
    {
      "name": "ANTHROPIC_BASE_URL",
      "value": "https://api.bgyhub.com"
    },
    {
      "name": "ANTHROPIC_AUTH_TOKEN",
      "value": "YOUR_BGYHUB_API_KEY"
    }
  ]
}

Restart or reload the Claude Code extension after changing the configuration.

## Troubleshooting

### Authentication Error

Check that:

- Your BGYHub API key is correct
- The key has not been disabled
- Your account has available credit

### Insufficient Balance

If your balance is exhausted, API requests will stop until credit is added.

Your existing API key can continue working after the account is recharged.

### Requests Do Not Appear in the Portal

Confirm that `ANTHROPIC_BASE_URL` is set to:

https://api.bgyhub.com

Then restart Claude Code and try again.

## Beta

BGYHub is currently in Beta.

We are looking for developers who actively use Claude Code, Codex, AI agents, and other AI coding workflows.

If you encounter compatibility issues, we'd like to hear about them.

Support:

support@bgyhub.com

Website:

https://bgyhub.com

---

BGYHub — One API. Multiple Models. Built for Developers.
