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


@app.route("/trello")
@app.route("/tasks")
def trello_board():
    """Renders the team task board (Kanban)."""
    return render_template("trello.html")


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


# ==========================================================================
# REST API ENDPOINTS FOR CLIENT-SIDE CHATBOT WIRING
# ==========================================================================

def init_logs_table():
    """Initialize the interaction_logs table if it does not exist."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS interaction_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_message TEXT,
                intent TEXT,
                deflected INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error initializing interaction_logs: {e}")

# Run log table init
init_logs_table()


@app.route("/api/tickets", methods=["GET"])
def get_tickets_summary():
    """Returns a count of deflected vs escalated interactions."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Count deflected interactions
        cursor.execute("SELECT COUNT(*) FROM interaction_logs WHERE deflected = 1;")
        deflected = cursor.fetchone()[0]
        
        # Count escalated tickets
        cursor.execute("SELECT COUNT(*) FROM tickets;")
        escalated = cursor.fetchone()[0]
        
        conn.close()
        return jsonify({
            "summary": {
                "deflected": deflected,
                "escalated": escalated
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/tickets", methods=["POST"])
def log_ticket_interaction():
    """Logs whether a user interaction deflected or escalated a support request."""
    try:
        data = request.get_json() if request.is_json else request.form
        if not data:
            return jsonify({"status": "error", "message": "Missing data"}), 400
            
        user_message = data.get("user_message", "")
        intent = data.get("intent", "")
        deflected = data.get("deflected", False)
        
        # Convert boolean to 1 or 0
        deflected_val = 1 if deflected else 0
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO interaction_logs (user_message, intent, deflected)
            VALUES (?, ?, ?);
        """, (user_message, intent, deflected_val))
        conn.commit()
        conn.close()
        
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/escalations", methods=["POST"])
def create_escalation():
    """Alternative ticket submission route specifically for frontend form."""
    try:
        data = request.get_json() if request.is_json else request.form
        if not data:
            return jsonify({"status": "error", "message": "Missing data"}), 400
            
        customer_name = data.get("name", "").strip()
        customer_email = data.get("email", "").strip()
        issue_description = data.get("message", "").strip()
        
        if not customer_name or not customer_email or not issue_description:
            return jsonify({"status": "error", "response": "All fields are required"}), 400
            
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tickets (customer_name, customer_email, issue_description)
            VALUES (?, ?, ?);
        """, (customer_name, customer_email, issue_description))
        ticket_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return jsonify({"status": "success", "ticket_id": ticket_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/orders/<order_id>", methods=["GET"])
def get_order_details(order_id):
    """Retrieves standard details about a specific order."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT product, status, ship_date, eta, tracking_update FROM orders WHERE id = ?;", (order_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return jsonify({"status": "error", "message": "Order not found"}), 404
            
        product, status, ship_date, eta, tracking_update = row
        
        # Map database status terms to standard UI states
        js_status = "Processing"
        if status == "delivered":
            js_status = "Delivered"
        elif status in ("shipped", "delayed", "delivery_exception"):
            js_status = "Shipped"
            
        return jsonify({
            "id": order_id,
            "status": js_status,
            "delivered_date": eta if status == "delivered" else None,
            "shipped_date": ship_date,
            "carrier": "Northstar Logistics",
            "tracking_number": f"NS-{order_id}-TRK",
            "eta": eta,
            "items": [product]
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/orders/<order_id>/return-eligibility", methods=["GET"])
def check_return_eligibility(order_id):
    """Verifies if an order is eligible for customer return."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM orders WHERE id = ?;", (order_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return jsonify({"status": "error", "message": "Order not found"}), 404
            
        status = row[0]
        if status == "delivered":
            return jsonify({"eligible": True, "reason": "Eligible for return."})
        else:
            return jsonify({"eligible": False, "reason": "Only delivered orders are eligible for return."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/orders/<order_id>/return", methods=["POST"])
def initiate_return(order_id):
    """Triggers return request flow and issues refund estimation."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE orders SET status = 'returned' WHERE id = ?;", (order_id,))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "refund_days": 5})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/stock/<product_key>", methods=["GET"])
def get_stock(product_key):
    """Checks catalog inventory stock count and size lists."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT product_name, sizes, quantity, restock_date FROM inventory WHERE product_name = ? OR product_name LIKE ?;", 
                       (product_key.lower().strip(), f"%{product_key.lower().strip()}%"))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return jsonify({"status": "error", "message": "Product not found"}), 404
            
        product_name, sizes, quantity, restock_date = row
        size_list = [s.strip() for s in sizes.split(",")]
        
        requested_size = request.args.get("size")
        if requested_size:
            size_clean = requested_size.strip().lower()
            size_list_lower = [s.lower() for s in size_list]
            in_stock = (quantity > 0) and (size_clean in size_list_lower)
            return jsonify({
                "in_stock": in_stock,
                "restock_eta": restock_date if restock_date else "date to be confirmed"
            })
            
        return jsonify({
            "sizes_in_stock": size_list
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    print("=" * 60)
    print("Starting Northstar Assistant on http://127.0.0.1:5000")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=True)
