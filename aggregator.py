"""
Evidence Aggregator + Final LLM synthesis.
"""

import os
import re
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

client = Groq(
    api_key=os.getenv("API_KEY")
)

FINAL_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


def aggregate_evidence(tool_outputs: list[dict]) -> list[dict]:
    evidence = []
    counter = 1

    for output in tool_outputs:
        source = output["source"]
        sub_question = output["sub_question"]

        for result in output.get("results", []):
            evidence.append({
                "id": counter,
                "source": source,
                "sub_question": sub_question,
                "text": result if isinstance(result, str) else str(result),
            })
            counter += 1

    return evidence


def synthesize_answer(user_question: str, evidence: list[dict]) -> str:
    if not evidence:
        return "I couldn't find anything relevant across your connected sources."

    evidence_block = "\n".join(
        f"[{e['id']}] (source: {e['source']}) {e['text']}"
        for e in evidence
    )

    system_prompt = """
You are a helpful colleague briefing someone about their project.

Answer ONLY using the evidence provided.

Rules:
- Never invent facts.
- If evidence is insufficient, clearly state that.
- Write natural plain-text prose, not a report generated from a template.
- Use short paragraphs and an occasional simple list only when it improves clarity.
- Do not use Markdown tables, headings with #, bold markers, code fences, or raw pipe-delimited tables.
- Do not use web-style citations such as 【1†L1-L9】, [1†L1-L9], footnotes, or URLs as citations.
- Do not expose evidence IDs such as [1] unless the user explicitly asks for citations.
- Translate implementation notation into normal language when possible (for example,
  say "the Windows local application-data folder" instead of `%LOCALAPPDATA%`).
- Mention useful filenames or technologies naturally in the sentence when they support a claim.
"""

    prompt = f"""
Question:
{user_question}

Evidence:
{evidence_block}

Answer the question using only the evidence above.
"""

    response = client.chat.completions.create(
        model=FINAL_MODEL,
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.2,
    )

    return _clean_answer(response.choices[0].message.content)


def _clean_answer(answer: str) -> str:
    """Turn occasional model formatting into readable UI-safe plain text."""
    text = answer.strip()

    # Models sometimes copy browser/search citation syntax even though our
    # evidence is local. Remove it rather than showing confusing line ranges.
    text = re.sub(r"【[^】]*】", " ", text)
    text = re.sub(r"\[\d+†[^\]]+\]", " ", text)

    # Remove presentation-only Markdown while preserving the words themselves.
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = text.replace("%LOCALAPPDATA%", "the Windows local application-data folder")

    # Convert simple Markdown tables into readable label/value lines. This
    # avoids displaying pipes and separator rows in the plain-text answer card.
    lines = text.splitlines()
    cleaned_lines: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append("")
            continue
        if "|" in stripped:
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            # A header immediately followed by a separator is presentation
            # metadata, not useful answer content.
            if index + 1 < len(lines) and "|" in lines[index + 1]:
                next_cells = [cell.strip() for cell in lines[index + 1].strip().strip("|").split("|")]
                if next_cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in next_cells):
                    continue
            if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            if len(cells) == 2:
                cleaned_lines.append(f"{cells[0]}: {cells[1]}")
                continue
            if len(cells) >= 3:
                cleaned_lines.append(f"{cells[0]}: " + " — ".join(cells[1:]))
                continue
        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
