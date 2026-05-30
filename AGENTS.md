# AGENTS.md

This file defines cross-agent rules for AI assistants working in this repository
(Copilot, Claude, Mistral, Qwen, Ollama-based agents, and similar tools).

## Mandatory Security Redaction Policy

When generating responses, docs, checklists, examples, logs, or scripts intended
for sharing, always anonymize infrastructure identifiers.

Always redact or replace with placeholders:
- Usernames
- Hostnames
- Domains and FQDNs
- IP addresses
- Absolute filesystem paths
- SSH ports when tied to a real host
- Secret names that reveal environment topology

Use placeholders such as:
- `<DEPLOY_USER>`
- `<VPS_HOST>`
- `<API_FQDN>`
- `<APP_DIR>`
- `<PUBLIC_IP>`
- `<REPO_URL>`

## Do Not Leak Real Values in Docs

- Never commit real infra values in Markdown runbooks.
- Never paste raw terminal outputs containing real infra identifiers into docs.
- If a real value is required for execution, keep it in local environment/secrets,
  not in tracked documentation.

## Scope

This policy applies to all new and updated documentation files in this repository,
including deployment guides and troubleshooting checklists.
