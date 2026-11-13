"""Research what hooks and formats are getting organic reach right now, within the campaign's rules.

Claude searches the web (articles, creator guides, platform trend reports, coverage of the brand's launch) and writes
a hook playbook. The playbook is cached per campaign and fed into clip selection.
"""

from __future__ import annotations

from pathlib import Path

import anthropic

from .campaign import Campaign
from .llm import MODEL, check_stop, fallback_kwargs

SYSTEM = """You are a short-form video strategist for a clipper: someone who edits official footage from a brand or \
creator into short vertical clips and is paid per view. Thousands of other clippers get the same footage and brief, \
so the clips that win are the ones that stop the scroll and look different from the pile, while staying fully \
within the campaign rules (a rule-breaking clip earns nothing and can get the account banned).

Research with web search before answering. Look for current, concrete evidence:
- What hook styles and on-screen text formats are getting reach on the target platforms now (the last few months).
- How this brand/creator, this launch, and this niche are being covered and talked about: what fans care about, \
the details that excite them, terms and names they use.
- What the obvious, saturated clip will be (what most clippers will post), so we can avoid it.
- Pacing, length, text placement and caption patterns that work for this niche on these platforms.

Prefer recent sources and say when evidence is thin or anecdotal. Never suggest anything the campaign bans \
(for example: exaggerated or rage-bait hooks, false claims, added music, AI voices, mislabelling a trailer as gameplay). \
Every hook idea must be something the official footage can truthfully back up."""

PROMPT = """<campaign_brief>
{brief}
</campaign_brief>
{context}
Research, then write a hook playbook in Markdown with these sections:

## What's working now
Short bullets with evidence, citing sources inline as (source: domain).

## Saturated angles to avoid
What most clippers will do with this footage.

## Hook patterns for this campaign
8-12 patterns. For each: the pattern, a ready-to-use on-screen line written for this campaign (true, rule-compliant, \
under 10 words), and what kind of moment in the footage it needs.

## Edit and pacing
Length, cut rhythm, where text goes, first-second rules.

## Caption angles
3-5 caption approaches that sound like a real fan, within the caption rules.

Keep it tight and practical; an editor will use it to pick and title clips."""


def research_hooks(campaign: Campaign, out_path: Path, client: anthropic.Anthropic | None = None,
                   refresh: bool = False, max_searches: int = 12) -> str:
    if out_path.exists() and not refresh:
        return out_path.read_text()
    client = client or anthropic.Anthropic()

    context = ""
    if campaign.context_links:
        context = "\n<context_links>\n" + "\n".join(campaign.context_links) + "\n</context_links>\n"
    messages = [{"role": "user", "content": PROMPT.format(brief=campaign.brief_text(), context=context)}]
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 6},
    ]

    for _ in range(6):  # resume server-side tool loops that pause
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=32000,
            system=SYSTEM,
            messages=messages,
            tools=tools,
            output_config={"effort": "high"},
            **fallback_kwargs(),
        ) as stream:
            response = stream.get_final_message()
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        break
    check_stop(response, "researching hooks")

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise RuntimeError("hook research returned no text")
    sources = []
    for block in response.content:
        if block.type == "web_search_tool_result" and isinstance(block.content, list):
            sources += [f"- [{r.title}]({r.url})" for r in block.content if getattr(r, "url", None)]
    playbook = f"# Hook playbook: {campaign.name}\n\n{text}\n"
    if sources:
        playbook += "\n## Sources searched\n" + "\n".join(dict.fromkeys(sources)) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(playbook)
    return playbook
