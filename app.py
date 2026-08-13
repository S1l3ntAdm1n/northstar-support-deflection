import os
import re
import json
import sqlite3
from flask import Flask, request, jsonify, render_template, session

from chatbot import get_response, detect_intent

app = Flask(__name__, template_folder="templates")
# Session secret — in production this should come from an environment variable
app.secret_key = os.environ.get("FLASK_SECRET", "northstar_support_deflection_secret_2026")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")


@app.route("/")
def index():
    """Renders the main chatbot UI."""
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
@app.route("/api/chat", methods=["POST"])
def chat():
    """
    POST Route: Receives the user message, passes it through the chatbot engine
    along with the server-side session, and returns a JSON response.
    """
    try:
        user_message = ""

        if request.is_json:
            data = request.get_json()
            if data and "message" in data:
                user_message = data["message"]
        else:
            user_message = request.form.get("message", "")

        # Build a plain dict from Flask's session proxy to pass to chatbot
        chat_session = {
            "pending_context": session.get("pending_context"),
            "order_lookup_attempts": session.get("order_lookup_attempts", 0),
            "stock_lookup_attempts": session.get("stock_lookup_attempts", 0),
            "escalated": session.get("escalated", False),
        }
        # Remove None values so chatbot's `.get()` defaults work cleanly
        chat_session = {k: v for k, v in chat_session.items() if v is not None}

        # Run chatbot logic — session dict is mutated in-place
        result = get_response(user_message, chat_session)

        # Write mutations back into Flask session
        session["pending_context"] = chat_session.get("pending_context")
        session["order_lookup_attempts"] = chat_session.get("order_lookup_attempts", 0)
        session["stock_lookup_attempts"] = chat_session.get("stock_lookup_attempts", 0)
        session["escalated"] = chat_session.get("escalated", False)

        return jsonify({
            "status": "success",
            "response": result["response"],
            "show_ticket_form": result.get("show_ticket_form", False),
            "suggest_ticket": result.get("suggest_ticket", False),
            "escalated": result.get("escalated", False),
            "prefilled_order_id": result.get("prefilled_order_id", ""),
        })

    except Exception as e:
        return jsonify({
            "status": "error",
            "response": "I encountered an error — please try again in a moment.",
            "show_ticket_form": False,
            "suggest_ticket": False,
            "escalated": False,
            "prefilled_order_id": "",
        }), 500


@app.route("/api/ticket", methods=["POST"])
def create_ticket():
    """
    POST Route: Creates a support ticket in the SQLite database.
    Expects: customer_name, customer_email, issue_description, optional order_id.
    """
    try:
        if request.is_json:
            data = request.get_json()
        else:
            data = request.form

        if not data:
            return jsonify({"status": "error", "response": "Missing form data."}), 400

        customer_name = data.get("customer_name", "").strip()
        customer_email = data.get("customer_email", "").strip()
        issue_description = data.get("issue_description", "").strip()
        order_id = data.get("order_id", "").strip()

        if not customer_name or not customer_email or not issue_description:
            return jsonify({"status": "error", "response": "Please fill out all required fields."}), 400

        if "@" not in customer_email or "." not in customer_email:
            return jsonify({"status": "error", "response": "Please enter a valid email address."}), 400

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO tickets (order_id, customer_name, customer_email, issue_description)
            VALUES (?, ?, ?, ?);
            """,
            (order_id if order_id else None, customer_name, customer_email, issue_description),
        )
        ticket_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # Mark session as escalated once ticket is submitted
        session["escalated"] = True

        return jsonify({
            "status": "success",
            "ticket_id": ticket_id,
            "message": f"Support ticket #{ticket_id} created successfully.",
        })

    except Exception:
        return jsonify({
            "status": "error",
            "response": "Could not create support ticket — please try again.",
        }), 500


@app.route("/api/session/reset", methods=["POST"])
def reset_session():
    """Clears the chatbot session state so the user can start fresh."""
    session.clear()
    return jsonify({"status": "success", "message": "Session reset."})


if __name__ == "__main__":
    print("=" * 60)
    print("Starting Northstar Assistant on http://127.0.0.1:5000")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=True)
