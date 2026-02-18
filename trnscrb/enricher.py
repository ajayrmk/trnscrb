"""Post-call LLM enrichment via Claude API.

Sends the raw transcript to Claude and gets back:
  - A summary
  - Action items
  - Inferred speaker names (replaces SPEAKER_00 etc.)
"""
from typing import Optional


_PROMPT_TEMPLATE = """You are analyzing a meeting transcript.{context}

Transcript:
{transcript}

Provide:
1. A brief summary (2-3 sentences)
2. Action items with owner names if identifiable
3. Inferred speaker names — if speakers appear as SPEAKER_00, SPEAKER_01 etc., \
infer their names or roles from the conversation

Respond in exactly this format:

SUMMARY:
<summary here>

ACTION ITEMS:
- <item> (Owner: <name or Unknown>)

SPEAKER MAPPING:
- SPEAKER_00 → <inferred name or "Participant 1">
- SPEAKER_01 → <inferred name or "Participant 2">
"""


def enrich_transcript(
    transcript_text: str,
    calendar_event: Optional[dict] = None,
    model: str = "claude-sonnet-4-6",
) -> dict:
    """
    Returns {enrichment: str, speaker_map: dict, enriched_transcript: str}.
    Raises if ANTHROPIC_API_KEY is not set.
    """
    from anthropic import Anthropic

    context = ""
    if calendar_event:
        context = f"\nMeeting: {calendar_event.get('title', '')}"
        if calendar_event.get("attendees"):
            context += f"\nKnown attendees: {', '.join(calendar_event['attendees'])}"

    prompt = _PROMPT_TEMPLATE.format(context=context, transcript=transcript_text)

    client = Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    enrichment = response.content[0].text

    speaker_map = _parse_speaker_map(enrichment)
    enriched = _apply_speaker_map(transcript_text, speaker_map)

    return {
        "enrichment": enrichment,
        "speaker_map": speaker_map,
        "enriched_transcript": enriched,
    }


def _parse_speaker_map(enrichment: str) -> dict:
    speaker_map = {}
    in_section = False
    for line in enrichment.splitlines():
        if line.strip().startswith("SPEAKER MAPPING:"):
            in_section = True
            continue
        if in_section:
            if "→" in line:
                raw, _, name = line.partition("→")
                raw = raw.strip().lstrip("- ").strip()
                name = name.strip().strip('"')
                if raw:
                    speaker_map[raw] = name
            elif line.strip() and not line.startswith("-"):
                break  # end of section
    return speaker_map


def _apply_speaker_map(text: str, speaker_map: dict) -> str:
    for raw, name in speaker_map.items():
        text = text.replace(f"[{raw}]", f"[{name}]")
    return text
