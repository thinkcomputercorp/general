# PlainSite Analyst on Claude Managed Agents

This runs the PlainSite analysis work (Judicial, Brief and Complaint Analyses, Plain English summaries and draft responses) as a Claude Managed Agents session paid from API credits. Anthropic hosts the agent loop and a sandbox. The agent reads the record, verifies its sources, drafts, and dry-runs the submit. A person reviews the result, and `run_analysis.py` publishes it only after you type `submit`.

| File | What it is |
|---|---|
| `agents/plainsite-analyst.md` | The agent: model (Claude Opus 5.5, effort `high`), tools, and the system prompt |
| `environments/plainsite-research.yaml` | The sandbox, with networking limited to PlainSite and case-law and statute sites |
| `vaults/plainsite.yaml` | The vault that holds the PlainSite key (the key itself is never in this repo) |
| `setup_credentials.py` | One-time: puts `PLAINSITE_KEY` into the vault |
| `run_analysis.py` | Runs one job, streams progress, downloads the files, and asks before submitting |
| `tools/` | The helper scripts used for verification (`cap.py`, `findq.py`, `cls.py`), mounted at `/workspace/tools/` |

## One-time setup

1. **API key.** In the Claude Console, under the Think Computer Corporation organization, create an API key and `export ANTHROPIC_API_KEY=...`. The $100 credit applies to sessions run with it until October 31, 2026.
2. **Install** Python 3.10 or later, then `pip install -r requirements.txt`. Install the `ant` CLI, version 1.34.0 or later; on macOS: `brew install anthropics/tap/ant`.
3. **Create the agent, environment and vault.** From this directory:

   ```sh
   ant apply --dry-run -v agents/plainsite-analyst.md environments/plainsite-research.yaml vaults/plainsite.yaml
   ant apply agents/plainsite-analyst.md environments/plainsite-research.yaml vaults/plainsite.yaml
   ```

   The first command shows what will be created and changes nothing. The second asks for confirmation, creates the three resources, and writes their IDs to `claude-lock.json`. Commit that file: without it, the next `ant apply` creates duplicates.
4. **Store the PlainSite key:** `PLAINSITE_KEY=... python setup_credentials.py`. It is substituted only in request headers sent to www.plainsite.org.

To change the prompt, tools or model later, edit the file and run the same `ant apply` command again. The agent gets a new version, and the next session uses it.

## Running a job

```sh
python run_analysis.py --entry 427656622 --style "Brief Analysis"
python run_analysis.py --entry 309860375 --style "Judicial Analysis" --notes "Focus on the Signal messages ruling."
python run_analysis.py --draft 7
```

The script prints a Console link where you can watch the session, streams the agent's steps, and stops when the agent finishes. It then:

1. downloads `analysis.html` (or `draft.html`), `payload.json`, `dryrun.json` and `report.md` to `runs/<session id>/`;
2. shows `report.md` and the token usage and cost;
3. re-runs the dry run locally with the exact payload;
4. waits for you to type `submit`. Anything else stops without publishing.

To submit a run later, after reading the files: `python run_analysis.py --submit-from runs/<session id>`. That needs `PLAINSITE_KEY` set locally.

Options:
- `--budget 15`: a hard spend cap for the session in dollars (default $10). At the cap the session pauses, and the script asks whether to raise it.
- `--unattended`: deny any tool call the server pauses for review, and skip the submit prompt.

## Cost

Claude Opus 5.5 costs $4 per million input tokens, $20 per million output tokens, and $0.20 per million cached input tokens. Session runtime adds $0.08 an hour. Most of what each step re-reads is cached. These are rough estimates; the script prints the actual list cost of every run, so check the first few.
- An analysis: about $3–6.
- A long draft response: about $8–15.

To spend less, lower `effort` in `agents/plainsite-analyst.md` to `medium` for routine orders, or switch `id` to `claude-sonnet-5-5` ($2 / $10 per million).

## Safeguards

- **No real submits from the agent.** The system prompt forbids them and requires `dryrun=1`, and this script does the real submit only after you type `submit`. The prompt is not a technical barrier, though: the agent's key could still submit. For a hard guarantee, give the vault a PlainSite key that PlainSite only accepts for dry runs, and keep the full key in your local `PLAINSITE_KEY` for the final submit.
- **The key never enters the sandbox.** The agent sees a placeholder, and the real key is added only to headers sent to PlainSite.
- **Limited network.** The agent can reach only the hosts in `environments/plainsite-research.yaml`, which also bounds its `web_fetch` and `web_search` tools. To add a site, edit that file and re-run `ant apply`.
- **Server review of risky calls.** Tool calls run under the `auto` permission policy. The server runs safe calls, denies high-risk ones, and pauses unclear ones for you to approve.

## Not set up yet

- **PlainSite MCP server.** If it has a public HTTPS URL, add it to the agent under `mcp_servers` with an `mcp_toolset` entry. Add its token to the vault as an MCP credential, and set `allow_mcp_servers: true` in the environment. That would give the agent `getEntry` page images, for checking a brief's format by eye, and `getOpinion` for recent cases.
- **CourtListener token.** Its opinion-text endpoint needs one. Add it to the vault as an environment-variable credential (`COURTLISTENER_TOKEN`, allowed host `www.courtlistener.com`) and mention it in the prompt.
- **Nightly queue runs.** A scheduled deployment (`deployments/*.md` with a cron `schedule`) can start a session each night to work through the queue. Each run still ends at a dry run for you to review.
