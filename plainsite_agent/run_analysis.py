"""Run one PlainSite analysis or draft on Claude Managed Agents.

Examples:

    python run_analysis.py --entry 427656622 --style "Brief Analysis"
    python run_analysis.py --entry 308863176 --style "Judicial Analysis" --budget 8
    python run_analysis.py --draft 6
    python run_analysis.py --submit-from runs/sesn_01...   # submit a run you reviewed earlier

Each run starts a session with the agent and environment created by `ant apply`,
streams the agent's progress, then downloads its files into runs/<session id>/.
The agent only ever dry-runs. This script does the real submit, and only after
you type "submit".

Needs ANTHROPIC_API_KEY (or an `ant auth login` profile) for the session, and
PLAINSITE_KEY locally for the final submit.
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import anthropic

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK_FILE = os.path.join(HERE, "claude-lock.json")
TOOLS_DIR = os.path.join(HERE, "tools")
PLAINSITE = "https://www.plainsite.org/control/"
MA_BETA = "managed-agents-2026-04-01"


# ---------------------------------------------------------------- setup lookups

def resource_ids() -> tuple[str, str, str]:
    """Agent, environment and vault IDs, from env vars or claude-lock.json."""
    lock = {}
    if os.path.exists(LOCK_FILE):
        with open(LOCK_FILE) as f:
            lock = json.load(f).get("resources", {})

    def pick(env_name: str, path: str) -> str:
        value = os.environ.get(env_name) or lock.get(path, {}).get("id")
        if not value:
            sys.exit(f"Missing {env_name}: run `ant apply` (see README.md) or set it.")
        return value

    return (
        pick("PLAINSITE_AGENT_ID", "./agents/plainsite-analyst.md"),
        pick("PLAINSITE_ENV_ID", "./environments/plainsite-research.yaml"),
        pick("PLAINSITE_VAULT_ID", "./vaults/plainsite.yaml"),
    )


def upload_tools(client: anthropic.Anthropic) -> list[dict]:
    """Upload the helper scripts and mount them read-only at /workspace/tools/."""
    resources = []
    for name in sorted(os.listdir(TOOLS_DIR)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(TOOLS_DIR, name), "rb") as f:
            uploaded = client.beta.files.upload(file=(name, f, "text/x-python"), expires_in_seconds=7 * 86400)
        resources.append({"type": "file", "file_id": uploaded.id, "mount_path": f"/workspace/tools/{name}"})
    return resources


def kickoff_text(args: argparse.Namespace) -> str:
    if args.draft:
        job = (f"Write PlainSite draft {args.draft}. Start with "
               f"drafts.js?method=get&draftid={args.draft} and the draft instructions for its kind.")
    else:
        job = (f"Write a {args.style} of PlainSite docket entry {args.entry}. Start with "
               f"summaries.js?method=get&style={urllib.parse.quote(args.style)}&entryid={args.entry}&docket=0 "
               f"and the instructions for that style.")
    if args.notes:
        job += f"\n\nNotes from the operator:\n{args.notes}"
    return job + "\n\nFinish with a dry run and the four files in /mnt/session/outputs/. Do not submit for real."


# ---------------------------------------------------------------- session loop

def answer_paused_call(client, session_id: str, event, unattended: bool) -> None:
    """Answer a tool call the server paused for review (`auto` with no determination)."""
    summary = json.dumps(getattr(event, "input", {}), default=str)[:400]
    print(f"\n[approval needed] {event.name}: {summary}")
    if unattended:
        allow = False
        print("  denied (unattended run)")
    else:
        allow = input("  Allow this call? [y/N] ").strip().lower() == "y"
    decision = {"type": "user.tool_confirmation", "tool_use_id": event.id, "result": "allow" if allow else "deny"}
    if not allow:
        decision["deny_message"] = "The operator declined this call. Find another way or explain why it is needed."
    client.beta.sessions.events.send(session_id=session_id, events=[decision])


def handle(client, session_id: str, event, unattended: bool) -> str | None:
    """Process one event. Returns 'done' or 'budget' when the loop should stop."""
    etype = event.type
    if etype == "agent.message":
        for block in event.content:
            if block.type == "text":
                print(block.text, end="", flush=True)
    elif etype in ("agent.tool_use", "agent.mcp_tool_use"):
        detail = getattr(event, "input", {}) or {}
        hint = detail.get("command") or detail.get("url") or detail.get("query") or detail.get("file_path") or ""
        print(f"\n  > {event.name} {str(hint)[:140]}", flush=True)
        if getattr(event, "evaluated_permission", None) == "ask":
            answer_paused_call(client, session_id, event, unattended)
    elif etype == "session.error":
        print(f"\n[session error] {getattr(event, 'error', event)}")
    elif etype == "session.status_idle":
        reason = event.stop_reason.type
        if reason == "requires_action":
            return None
        return "budget" if reason == "budget_reached" else "done"
    elif etype == "session.status_terminated":
        return "done"
    return None


def run_session(client, session_id: str, unattended: bool, first_events: list[dict]) -> str:
    """Stream until the session settles, reconnecting if the stream drops."""
    seen: set[str] = set()
    pending_kickoff = first_events
    while True:
        try:
            with client.beta.sessions.events.stream(session_id=session_id) as stream:
                if pending_kickoff:  # open the stream first, then send, so nothing is missed
                    client.beta.sessions.events.send(session_id=session_id, events=pending_kickoff)
                    pending_kickoff = []
                else:  # reconnect: catch up on anything that happened while disconnected
                    for event in client.beta.sessions.events.list(session_id=session_id):
                        if event.id in seen:
                            continue
                        seen.add(event.id)
                        outcome = handle(client, session_id, event, unattended)
                        if outcome:
                            return outcome
                for event in stream:
                    if event.id in seen:
                        continue
                    seen.add(event.id)
                    outcome = handle(client, session_id, event, unattended)
                    if outcome:
                        return outcome
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as err:
            print(f"\n[stream dropped: {err}; reconnecting]")
            time.sleep(3)


# ---------------------------------------------------------------- outputs and submit

def download_outputs(client, session_id: str, out_dir: str) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    names: list[str] = []
    for _ in range(5):  # files can take a few seconds to appear after the session idles
        files = client.beta.files.list(scope_id=session_id, betas=[MA_BETA])
        for f in files.data:
            client.beta.files.download(f.id).write_to_file(os.path.join(out_dir, f.filename))
            names.append(f.filename)
        if names:
            break
        time.sleep(3)
    return names


def post_plainsite(endpoint: str, fields: dict, dry_run: bool) -> dict:
    key = os.environ.get("PLAINSITE_KEY")
    if not key:
        sys.exit("Set PLAINSITE_KEY locally to submit.")
    form = {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in fields.items()}
    if dry_run:
        form["dryrun"] = "1"
    request = urllib.request.Request(
        PLAINSITE + endpoint + "?method=submit",
        data=urllib.parse.urlencode(form).encode(),
        headers={"X-PlainSite-Key": key},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode())


def review_and_submit(out_dir: str) -> None:
    payload_path = os.path.join(out_dir, "payload.json")
    report_path = os.path.join(out_dir, "report.md")
    if os.path.exists(report_path):
        print("\n" + "=" * 70 + "\n" + open(report_path).read())
    if not os.path.exists(payload_path):
        print(f"\nNo payload.json in {out_dir}; nothing to submit.")
        return
    with open(payload_path) as f:
        payload = json.load(f)
    endpoint, fields = payload["endpoint"], payload["fields"]
    if endpoint not in ("summaries.js", "drafts.js"):
        sys.exit(f"Unexpected endpoint in payload.json: {endpoint}")

    print("\nRe-running the dry run locally with the exact payload...")
    check = post_plainsite(endpoint, fields, dry_run=True)
    summary = {k: check[k] for k in check if k not in ("linked", "citations", "placeholders")}
    print(json.dumps(summary, indent=1)[:2500])

    answer = input(f"\nReview {out_dir} first. Type 'submit' to publish, anything else to stop: ").strip()
    if answer != "submit":
        print(f"Not submitted. Later: python run_analysis.py --submit-from {out_dir}")
        return
    result = post_plainsite(endpoint, fields, dry_run=False)
    print(json.dumps({k: result[k] for k in result if k not in ("linked", "citations")}, indent=1)[:2500])


# ---------------------------------------------------------------- main

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    job = parser.add_mutually_exclusive_group(required=True)
    job.add_argument("--entry", help="docket entry ID to analyze")
    job.add_argument("--draft", help="pending draft ID to write")
    job.add_argument("--submit-from", help="a runs/<session> folder to review and submit")
    parser.add_argument("--style", default="Judicial Analysis",
                        choices=["Judicial Analysis", "Brief Analysis", "Complaint Analysis", "Plain English"])
    parser.add_argument("--notes", default="", help="extra instructions for this run")
    parser.add_argument("--budget", type=float, default=10.0, help="hard spend cap for the session, in USD")
    parser.add_argument("--unattended", action="store_true", help="deny any call the server pauses for review")
    args = parser.parse_args()

    if args.submit_from:
        review_and_submit(args.submit_from)
        return

    client = anthropic.Anthropic()
    agent_id, env_id, vault_id = resource_ids()
    title = f"Draft {args.draft}" if args.draft else f"{args.style}: entry {args.entry}"
    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=env_id,
        vault_ids=[vault_id],
        resources=upload_tools(client),
        title=title,
        budget={"type": "limit", "max_list_cost": {"amount": str(int(round(args.budget * 100))), "currency": "USD"}},
    )
    print(f"Session {session.id}\nWatch live: https://platform.claude.com/workspaces/default/sessions/{session.id}\n")

    kickoff = [{"type": "user.message", "content": [{"type": "text", "text": kickoff_text(args)}]}]
    outcome = run_session(client, session.id, args.unattended, kickoff)
    while outcome == "budget":
        current = client.beta.sessions.retrieve(session_id=session.id)
        spent = current.usage.list_cost.amount if current.usage and current.usage.list_cost else "?"
        print(f"\n[budget reached: {spent} cents spent]")
        more = "" if args.unattended else input("Raise the cap to how many USD? (blank to stop) ").strip()
        if not more:
            break
        client.beta.sessions.update(
            session_id=session.id,
            budget={"type": "limit", "max_list_cost": {"amount": str(int(round(float(more) * 100))), "currency": "USD"}},
        )
        outcome = run_session(client, session.id, args.unattended, [])

    final = client.beta.sessions.retrieve(session_id=session.id)
    usage = final.usage
    if usage:
        cost = f"${int(usage.list_cost.amount) / 100:.2f}" if usage.list_cost else "not yet reported"
        print(f"\n\nUsage: {usage.input_tokens} input, {usage.cache_read_input_tokens} cached, "
              f"{usage.output_tokens} output tokens; list cost {cost}")

    out_dir = os.path.join(HERE, "runs", session.id)
    names = download_outputs(client, session.id, out_dir)
    print(f"Downloaded {', '.join(names) or 'nothing'} to {out_dir}")
    if not args.unattended:
        review_and_submit(out_dir)


if __name__ == "__main__":
    main()
