import os
import openai
import uuid
import json
import datetime
import sqlite3
from flask import Flask, request, jsonify, send_file, redirect
from flask_cors import CORS
from gtts import gTTS
from dotenv import load_dotenv
import dash
from dash import dcc
from dash import html
import plotly.express as px
import pandas as pd
from dash import dcc, html, Input, Output  # Fix missing import
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
# Access environment variables
openai_api_key = os.getenv("OPENAI_API_KEY")
database_url = os.getenv("DATABASE_URL")

print(f"OpenAI API Key: {openai_api_key}")  # For debugging (Remove in production)
print(f"Database URL: {database_url}")  # For debugging
# Initialize OpenAI Client
client = openai.OpenAI()
client.api_key = openai_api_key

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Create folder for storing audio files
AUDIO_FOLDER = "static/audio"
os.makedirs(AUDIO_FOLDER, exist_ok=True)

# Initialize SQLite Database
db_path = "logs.db"

def init_db():
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                user_query TEXT,
                ai_response TEXT,
                request_type TEXT,
                voice_output BOOLEAN
            )
        ''')
        conn.commit()
init_db()

# Function to save logs
def save_log(entry):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO logs (timestamp, user_query, ai_response, request_type, voice_output)
            VALUES (?, ?, ?, ?, ?)
        ''', (entry["timestamp"], entry["user_query"], entry["ai_response"], entry["request_type"], entry["voice_output"]))
        conn.commit()

# 1. User Interaction - Chat with AI
@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_text = data.get("message")
    want_audio = data.get("voice", False)

    if not user_text:
        return jsonify({"error": "No message provided"}), 400

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": user_text}]
        )
        answer_text = response.choices[0].message.content
    except Exception as e:
        return jsonify({"error": f"OpenAI API error: {str(e)}"}), 500

    # If user requested audio, generate TTS (Google Text-to-Speech)
    audio_filename = None
    if want_audio:
        audio_filename = f"{uuid.uuid4().hex}.mp3"
        audio_path = os.path.join(AUDIO_FOLDER, audio_filename)
        tts = gTTS(text=answer_text, lang="en")  # Convert text to speech
        tts.save(audio_path)  # Save as an MP3 file

    # Save the log
    log_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "user_query": user_text,
        "ai_response": answer_text,
        "request_type": "chat",
        "voice_output": want_audio
    }
    save_log(log_entry)

    # Return response (text + optional audio file URL)
    result = {"answer": answer_text}
    if audio_filename:
        result["audio_url"] = f"/download/{audio_filename}"

    return jsonify(result)

# 2. Speech-to-Text (STT)
ALLOWED_EXTENSIONS = {"flac", "m4a", "mp3", "mp4", "mpeg", "mpga", "oga", "ogg", "wav", "webm"}

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/transcribe", methods=["POST"])
def transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio_file = request.files["audio"]
    if not allowed_file(audio_file.filename):
        return jsonify({"error": "Unsupported file format"}), 400

    file_path = os.path.join(AUDIO_FOLDER, audio_file.filename)
    audio_file.save(file_path)  # Save file temporarily

    try:
        with open(file_path, "rb") as file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=file
            )
        text = transcript.text
    except Exception as e:
        return jsonify({"error": f"Whisper API error: {str(e)}"}), 500
    finally:
        os.remove(file_path)  # Delete temp file

    return jsonify({"transcription": text})

# 3. Download Audio Files
@app.route("/download/<filename>", methods=["GET"])
def download_audio(filename):
    file_path = os.path.join(AUDIO_FOLDER, filename)
    if not os.path.exists(file_path):
        return jsonify({"error": "File not found"}), 404
    return send_file(file_path, as_attachment=True)

# 4. Dashboard Visualization with Dash
dash_app = dash.Dash(
    __name__,
    server=app,
    routes_pathname_prefix="/dashboard/",
    requests_pathname_prefix="/dashboard/"  # Fix routing issue
)

dash_app.layout = html.Div([
    html.H1("Chatbot Analytics Dashboard"),
    dcc.Graph(id="query-frequency"),
    dcc.Interval(id="interval-component", interval=5000, n_intervals=0)  # Auto-refresh every 5 sec
])

@dash_app.callback(
    dash.dependencies.Output("query-frequency", "figure"),
    [dash.dependencies.Input("interval-component", "n_intervals")]
)
def update_graph(n_intervals):
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql("SELECT user_query, COUNT(*) as count FROM logs GROUP BY user_query ORDER BY count DESC LIMIT 10", conn)
    fig = px.bar(df, x="user_query", y="count", title="Top Asked Questions")
    return fig

# 5. API to Get Analytics Data
@app.route("/analytics", methods=["GET"])
def analytics():
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_query, COUNT(*) FROM logs GROUP BY user_query ORDER BY COUNT(*) DESC LIMIT 5")
        common_queries = cursor.fetchall()
    return jsonify({"most_common_queries": common_queries})

@app.route("/")
def home():
    return redirect("/dashboard/")

# Run Flask App
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
