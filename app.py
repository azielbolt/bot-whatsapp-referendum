import json
import os
import random
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Any, Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Query
from fastapi.responses import PlainTextResponse, JSONResponse

load_dotenv()

APP_DIR = Path(__file__).resolve().parent
QUESTIONS_PATH = APP_DIR / "questions.json"

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "referendum_rdc_2026")
META_API_VERSION = os.getenv("META_API_VERSION", "v23.0")
BOT_NAME = os.getenv("BOT_NAME", "Assistant Référendum RDC")

app = FastAPI(title="Bot WhatsApp Référendum RDC")

with QUESTIONS_PATH.open("r", encoding="utf-8") as f:
    QA: List[Dict[str, Any]] = json.load(f)

MODULES = []
_seen = set()
for item in QA:
    key = item["module"]
    if key not in _seen:
        _seen.add(key)
        MODULES.append({"module": key, "title": item["moduleTitle"]})

# Mémoire simple par utilisateur : suffit pour un petit bot. Pour production, utiliser Redis/PostgreSQL.
SESSIONS: Dict[str, Dict[str, Any]] = {}


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def search_answers(user_text: str, limit: int = 3) -> List[Dict[str, Any]]:
    query = normalize(user_text)
    if not query:
        return []

    words = [w for w in query.split() if len(w) > 2]
    results = []

    for item in QA:
        haystack = normalize(" ".join([
            item.get("q", ""),
            item.get("a", ""),
            item.get("keys", ""),
            item.get("moduleTitle", ""),
        ]))

        score = 0
        if query in haystack:
            score += 20
        for word in words:
            if word in haystack:
                score += 3
            if word in normalize(item.get("q", "")):
                score += 5

        if score > 0:
            results.append((score, item))

    results.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in results[:limit]]


def format_search_response(user_text: str) -> str:
    results = search_answers(user_text)
    if not results:
        return (
            "Je n’ai pas trouvé une réponse exacte.\n\n"
            "Essaie avec un mot-clé comme : référendum, CENI, fraude, recours, vote, Constitution, territoire.\n\n"
            "Tape MENU pour voir les options."
        )

    lines = ["Voici ce que j’ai trouvé :"]
    for i, item in enumerate(results, 1):
        lines.append(
            f"\n{i}. {item['q']}\n"
            f"Réponse : {item['a']}\n"
            f"Module : {item['moduleTitle']}"
        )
    lines.append("\nTape QUIZ pour t’entraîner ou MENU pour les options.")
    return "\n".join(lines)


def main_menu() -> str:
    return (
        f"🇨🇩 {BOT_NAME}\n\n"
        "Choisis une option :\n"
        "1. Poser une question\n"
        "2. Lancer le quiz\n"
        "3. Voir les modules\n"
        "4. Aide\n\n"
        "Tu peux aussi écrire directement un mot-clé : CENI, fraude, vote, recours, Constitution..."
    )


def modules_text() -> str:
    lines = ["📚 Modules disponibles :"]
    for i, module in enumerate(MODULES, 1):
        lines.append(f"{i}. {module['title']}")
    lines.append("\nPour chercher dans tout le contenu, écris directement ta question.")
    return "\n".join(lines)


def start_quiz(user_id: str) -> str:
    question = random.choice(QA)
    SESSIONS[user_id] = {"mode": "quiz", "question": question, "score": 0, "total": 0}
    return render_quiz_question(question)


def render_quiz_question(question: Dict[str, Any]) -> str:
    options = question.get("options", [])
    letters = ["A", "B", "C", "D"]
    lines = [f"🧠 Quiz Référendum RDC\n\n{question['q']}"]
    for letter, option in zip(letters, options):
        lines.append(f"{letter}. {option}")
    lines.append("\nRéponds par A, B, C ou D.")
    return "\n".join(lines)


def handle_quiz_answer(user_id: str, text: str) -> str:
    session = SESSIONS.get(user_id)
    if not session or session.get("mode") != "quiz":
        return start_quiz(user_id)

    answer = normalize(text).upper()
    mapping = {"A": 0, "B": 1, "C": 2, "D": 3}
    if answer not in mapping:
        return "Réponds seulement par A, B, C ou D. Tape STOP pour arrêter le quiz."

    question = session["question"]
    correct = int(question.get("correct", 0))
    selected = mapping[answer]
    session["total"] += 1

    if selected == correct:
        session["score"] += 1
        feedback = "✅ Bonne réponse !"
    else:
        correct_letter = ["A", "B", "C", "D"][correct]
        feedback = f"❌ Mauvaise réponse. La bonne réponse était {correct_letter}."

    new_question = random.choice(QA)
    session["question"] = new_question

    return (
        f"{feedback}\n\n"
        f"Explication : {question['a']}\n\n"
        f"Score : {session['score']}/{session['total']}\n\n"
        f"Question suivante :\n\n{render_quiz_question(new_question)}"
    )


def build_reply(user_id: str, text: str) -> str:
    cleaned = normalize(text)

    if cleaned in {"menu", "bonjour", "salut", "start", "debut", "0"}:
        return main_menu()

    if cleaned in {"1", "question", "poser une question"}:
        return "Écris ta question ou un mot-clé sur le référendum. Exemple : Qui organise le référendum ?"

    if cleaned in {"2", "quiz", "lancer le quiz"}:
        return start_quiz(user_id)

    if cleaned in {"3", "modules", "module"}:
        return modules_text()

    if cleaned in {"4", "aide", "help"}:
        return (
            "Aide :\n"
            "- Écris une question pour recevoir une réponse.\n"
            "- Tape QUIZ pour t’entraîner.\n"
            "- Tape MODULES pour voir les modules.\n"
            "- Tape STOP pour quitter le quiz."
        )

    if cleaned in {"stop", "quitter", "fin"}:
        SESSIONS.pop(user_id, None)
        return "Quiz arrêté. Tape MENU pour revenir aux options."

    if SESSIONS.get(user_id, {}).get("mode") == "quiz":
        return handle_quiz_answer(user_id, text)

    return format_search_response(text)


def send_whatsapp_text(to: str, body: str) -> Dict[str, Any]:
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        print("Mode test : WHATSAPP_TOKEN ou PHONE_NUMBER_ID manquant.")
        print(f"Message à {to}: {body}")
        return {"ok": False, "test_mode": True}

    url = f"https://graph.facebook.com/{META_API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body[:4000]},
    }
    response = requests.post(url, headers=headers, json=payload, timeout=20)
    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text}
    if response.status_code >= 400:
        print("Erreur WhatsApp:", response.status_code, data)
    return data


@app.get("/")
def home():
    return {
        "status": "ok",
        "bot": BOT_NAME,
        "questions": len(QA),
        "modules": len(MODULES),
        "endpoints": ["GET /webhook", "POST /webhook", "POST /test"],
    }


@app.get("/webhook")
def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge or "")
    return PlainTextResponse("Forbidden", status_code=403)


@app.post("/webhook")
async def whatsapp_webhook(request: Request):
    payload = await request.json()

    try:
        entries = payload.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                for message in messages:
                    sender = message.get("from")
                    msg_type = message.get("type")
                    if not sender:
                        continue

                    if msg_type == "text":
                        text = message.get("text", {}).get("body", "")
                    else:
                        text = "menu"

                    reply = build_reply(sender, text)
                    send_whatsapp_text(sender, reply)
    except Exception as e:
        print("Erreur traitement webhook:", e)

    return JSONResponse({"status": "received"})


@app.post("/test")
async def test_bot(request: Request):
    data = await request.json()
    user_id = data.get("user", "test_user")
    text = data.get("message", "menu")
    return {"reply": build_reply(user_id, text)}
