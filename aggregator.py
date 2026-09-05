"""
Evidence Aggregator + Final LLM synthesis.
"""

import os
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
You are a helpful assistant.

Answer ONLY using the evidence provided.

Rules:
- Never invent facts.
- Cite evidence using [n].
- If evidence is insufficient, clearly state that.
- Write naturally, like you're briefing a colleague.
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

    return response.choices[0].message.content
