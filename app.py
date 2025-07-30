import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from youtubesearchpython import VideosSearch
from ytmusicapi import YTMusic
from functools import wraps
import yt_dlp
import subprocess

# 📁 Load environment variables
load_dotenv()

# 📂 Directories
MUSIC_DOWNLOADS_FOLDER = "music_downloads"
os.makedirs(MUSIC_DOWNLOADS_FOLDER, exist_ok=True)


app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev")
# Load environment variables
load_dotenv()

# TMDB and Jamendo API keys
TMDB_API_KEY = os.getenv("TMDB_API_KEY") or "ff3c52c367b55f45c8083c75df003bc0"
JAMENDO_API_KEY = os.getenv("JAMENDO_API_KEY") or "9e6a9815"

# ---------------------- FLASK APP CONFIG ----------------------

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or "dev-key"
DB_NAME = "users.db"

# Music download folder
MUSIC_DOWNLOADS_FOLDER = os.path.join("static", "music_downloads")
os.makedirs(MUSIC_DOWNLOADS_FOLDER, exist_ok=True)

# ---------------------- DATABASE INIT ----------------------

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        # Users table
        conn.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL,
            paid INTEGER DEFAULT 0
        )''')

        # App settings
        conn.execute('''CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')

        # Admin action log
        conn.execute('''CREATE TABLE IF NOT EXISTS admin_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            action TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')

        # Downloads table
        conn.execute('''CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT,
            item_id TEXT,
            title TEXT,
            poster TEXT,
            type TEXT, -- 'movie', 'series', 'music'
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')

        # Watch Later table
        conn.execute('''CREATE TABLE IF NOT EXISTS watch_later (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT,
            item_id TEXT,
            title TEXT,
            poster TEXT,
            type TEXT, -- 'movie', 'series', 'music'
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')

        # Default setting: disable payments initially
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('payments_required', '0')")

# Call it during startup
init_db()

# ---------------------- TMDB TRAILER HELPER ----------------------
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("role") != "admin":
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function
def download_video(url, format_code='bestaudio'):
    ydl_opts = {
        'format': format_code,
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

def get_audio_url(video_url):
    ydl_opts = {
        'quiet': True,
        'format': 'bestaudio/best',
        'noplaylist': True,
        'cookiefile': 'youtube_cookies.txt',  # 👈 Use your cookies here
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        return info['url']



# ---------------------- SMART MUSIC SEARCH ----------------------

def smart_music_search(song_title):
    query = song_title.strip().lower()

    # 1. Audicious
    audicious_results = search_audicious(query)
    exact = [t for t in audicious_results if query == t["title"].lower()]
    fuzzy = [t for t in audicious_results if query in t["title"].lower()]
    if exact: return exact
    if fuzzy: return fuzzy

    # 2. Free Music Archive (FMA)
    fma_results = search_fma(query)
    exact = [t for t in fma_results if query == t["title"].lower()]
    fuzzy = [t for t in fma_results if query in t["title"].lower()]
    if exact: return exact
    if fuzzy: return fuzzy

    # 3. Jamendo (Last fallback)
    jamendo_results = search_jamendo(query)
    exact = [t for t in jamendo_results if query == t["title"].lower()]
    fuzzy = [t for t in jamendo_results if query in t["title"].lower()]
    return exact or fuzzy or []

# ---------------------- SEARCH FUNCTIONS ----------------------

def search_audicious(song_title):
    try:
        url = f"https://audicious.co/?s={song_title.replace(' ', '+')}"
        r = requests.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        results = []

        for item in soup.select("article"):
            title = item.select_one("h2 a").text.strip()
            link = item.select_one("h2 a")["href"]
            if not link:
                continue

            track_page = requests.get(link, timeout=10)
            inner = BeautifulSoup(track_page.text, "html.parser")
            audio = inner.find("audio")
            audio_src = audio.find("source")["src"] if audio else None

            if title and audio_src:
                results.append({
                    "title": title,
                    "artist": "Audicious Artist",
                    "id": audio_src,
                    "download": audio_src,
                    "thumbnail": "",
                    "source": "audicious"
                })
        return results[:5]
    except Exception as e:
        print("Audicious error:", e)
        return []

def search_fma(song_title):
    try:
        url = f"https://freemusicarchive.org/search/?quicksearch={song_title.replace(' ', '+')}"
        r = requests.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        results = []

        for track in soup.select("li.track-item"):
            title_link = track.select_one(".track-item-title a")
            if not title_link:
                continue

            name = title_link.text.strip()
            link = "https://freemusicarchive.org" + title_link["href"]

            detail_res = requests.get(link, timeout=10)
            detail = BeautifulSoup(detail_res.text, "html.parser")
            audio_tag = detail.find("audio")
            audio_src = audio_tag.find("source")["src"] if audio_tag else None

            if name and audio_src:
                results.append({
                    "title": name,
                    "artist": "FMA Artist",
                    "id": audio_src,
                    "download": audio_src,
                    "thumbnail": "",
                    "source": "fma"
                })

        return results[:5]
    except Exception as e:
        print("FMA error:", e)
        return []

def search_jamendo(song_title):
    url = "https://api.jamendo.com/v3.0/tracks"
    params = {
        "client_id": os.getenv("JAMENDO_CLIENT_ID") or JAMENDO_API_KEY,
        "format": "json",
        "limit": 10,
        "namesearch": song_title,
        "audioformat": "mp31"
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return [{
            "title": t["name"],
            "artist": t["artist_name"],
            "id": t["audio"],
            "download": t.get("audiodownload", ""),
            "thumbnail": t.get("album_image", ""),
            "source": "jamendo"
        } for t in r.json().get('results', []) if t.get("audio")]
    except Exception as e:
        print("Jamendo error:", e)
        return []

# ---------------------- ADMIN ----------------------

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

# ---------------------- SAVE FUNCTIONS ----------------------

def save_download(user_email, item_id, title, poster, type_):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''INSERT INTO downloads (user_email, item_id, title, poster, type) VALUES (?, ?, ?, ?, ?)''', 
                     (user_email, item_id, title, poster, type_))

def save_watch_later(user_email, item_id, title, poster, type_):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''INSERT INTO watch_later (user_email, item_id, title, poster, type) VALUES (?, ?, ?, ?, ?)''',
                     (user_email, item_id, title, poster, type_))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("role") != "admin":
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated_function

@app.route("/")
def home():
    return redirect("/welcome")

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('welcome'))  # This renders welcome.html

@app.route("/welcome")
def welcome():
    return render_template("welcome.html", message=request.args.get("logged_out"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE email = ?", (email,))
        user = cur.fetchone()
        conn.close()

        if user and check_password_hash(user[2], password):
            session["user"] = email
            session["role"] = user[3]
            return redirect("/intro")
        else:
            return render_template("login.html", error="Invalid credentials.")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        if not email or not password:
            return render_template("register.html", error="Missing fields.")
        hashed = generate_password_hash(password)
        with sqlite3.connect(DB_NAME) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users")
            role = "admin" if cur.fetchone()[0] == 0 else "user"
            try:
                conn.execute("INSERT INTO users (email, password, role) VALUES (?, ?, ?)", (email, hashed, role))
                return redirect("/login")
            except sqlite3.IntegrityError:
                return render_template("register.html", error="Email already registered.")
    return render_template("register.html")

@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "POST":
        session['reset_email'] = request.form.get("email", "").strip()
        return redirect("/reset")
    return render_template("forgot.html")

@app.route("/reset", methods=["GET", "POST"])
def reset():
    if request.method == "POST":
        password = request.form.get("password", "").strip()
        hashed = generate_password_hash(password)
        email = session.get("reset_email", "")
        if email:
            with sqlite3.connect(DB_NAME) as conn:
                conn.execute("UPDATE users SET password = ? WHERE email = ?", (hashed, email))
            flash("Password reset successful.")
            return redirect("/login")
    return render_template("reset.html")

@app.route("/intro")
def intro():
    if "user" not in session:
        return redirect("/login")

    role = session.get("role", "user")
    if role == "admin":
        return redirect("/admin")
    else:
        return redirect("/music")


@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/login")

    def fetch_tmdb(endpoint):
        url = f"https://api.themoviedb.org/3/{endpoint}"
        params = {"api_key": TMDB_API_KEY, "language": "en-US"}
        try:
            r = requests.get(url, params=params, timeout=8)
            r.raise_for_status()
            return r.json().get("results", [])
        except:
            return []

    home = fetch_tmdb("movie/popular")
    trending = fetch_tmdb("trending/movie/week")
    latest = fetch_tmdb("movie/now_playing")

    if not any([home, trending, latest]):
        fallback_movie = {
            "id": 0,
            "title": "No Movies Available",
            "poster_path": "",
        }
        home = [fallback_movie]
        trending = []
        latest = []
        hero = {
            "id": 0,
            "title": "Tomorrow",
            "overview": "Enjoy free movies, shows, and music even when offline.",
            "poster_path": "",
        }
    else:
        hero = trending[0] if trending else (home[0] if home else None)

    try:
        asia_response = requests.get("https://movie.kaedenoki.net/list/asia/1", timeout=8)
        asia_data = asia_response.json().get("results", [])
        asian_dramas = [
            {
                "id": m.get("id"),
                "title": m.get("title"),
                "poster_url": m.get("poster"),
            }
            for m in asia_data
        ]
    except:
        asian_dramas = []

    return render_template(
        "index.html",
        home=home,
        trending=trending,
        latest=latest,
        hero=hero,
        asian_dramas=asian_dramas
    )

@app.route("/search_music")
def search_music():
    query = request.args.get("q")
    if not query:
        return render_template("search_music.html", results=[], query="", message="No search query.")
    
    results = smart_music_search(query)
    if not results:
        return render_template("search_music.html", results=[], query=query, message="No matches found.")
    
    return render_template("search_music.html", results=results, query=query)
@app.route("/offline")
def offline():
    return render_template("offline.html")

@app.route("/music")
def music():
    query = request.args.get("query", "").strip()
    results = []

    if query:
        ydl_opts = {
            'quiet': True,
            'skip_download': True,
            'format': 'bestaudio/best',
            'default_search': 'ytsearch10',
            'cookiefile': 'youtube_cookies.txt'
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                search_results = ydl.extract_info(query, download=False)
                for entry in search_results.get("entries", []):
                    results.append({
                        "id": entry.get("id"),
                        "title": entry.get("title"),
                        "thumbnail": entry.get("thumbnail", ""),
                    })
            except Exception as e:
                print("Search failed:", e)

    return render_template("music.html", results=results, query=query)

@app.route("/download_music")
def download_music():
    video_id = request.args.get("id")
    title = request.args.get("title", "music").replace("/", "-").replace("\\", "-")
    url = f"https://www.youtube.com/watch?v={video_id}"
    output_path = os.path.join(MUSIC_DOWNLOADS_FOLDER, f"{title}.mp3")

    if not os.path.exists(output_path):
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": output_path,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "quiet": True,
            "cookies": "youtube_cookies.txt"  # ✅ Supports login-required videos (if cookie file is valid)
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as e:
            return f"Download failed: {str(e)}"

    return send_file(output_path, as_attachment=True)

@app.route("/my_music")
def my_music():
    if "user" not in session:
        return redirect("/login")

    fav_path = os.path.join(MUSIC_DOWNLOADS_FOLDER, "favorites.txt")
    if not os.path.exists(fav_path):
        open(fav_path, "w").close()

    with open(fav_path, "r") as f:
        favs = set(f.read().splitlines())

    songs = []
    for f in os.listdir(MUSIC_DOWNLOADS_FOLDER):
        if f.endswith(".mp3"):
            songs.append({
                "title": os.path.splitext(f)[0].replace("_", " ").title(),
                "filename": f,
                "favorite": f in favs
            })
    return render_template("my_music.html", songs=songs)

@app.route("/delete_music/<filename>", methods=["POST"])
def delete_music(filename):
    if "user" not in session:
        return "Unauthorized", 403
    path = os.path.join(MUSIC_DOWNLOADS_FOLDER, filename)
    if os.path.exists(path):
        os.remove(path)
    return "", 204

@app.route("/favorite_music", methods=["POST"])
def favorite_music():
    if "user" not in session:
        return redirect("/login")

    filename = request.form.get("filename")
    fav_path = os.path.join(MUSIC_DOWNLOADS_FOLDER, "favorites.txt")

    with open(fav_path, "r+") as f:
        favs = set(f.read().splitlines())
        if filename in favs:
            favs.remove(filename)
        else:
            favs.add(filename)
        f.seek(0)
        f.truncate()
        f.write("\n".join(favs))

    return redirect("/my_music")


# --- Admin ---
@app.route("/admin")
@admin_required
def admin():
    search = request.args.get("search", "").lower()
    page = int(request.args.get("page", 1))
    per_page = 10
    offset = (page - 1) * per_page
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    query = "SELECT email, role, paid FROM users WHERE 1=1"
    params = []

    if search:
        query += " AND LOWER(email) LIKE ?"
        params.append(f"%{search}%")

    cur.execute("SELECT COUNT(*) FROM (" + query + ")", params)
    total_users = cur.fetchone()[0]
    total_pages = (total_users + per_page - 1) // per_page

    query += " LIMIT ? OFFSET ?"
    params += [per_page, offset]
    cur.execute(query, params)
    users = [{"email": row[0], "role": row[1], "paid": row[2]} for row in cur.fetchall()]

    cur.execute("SELECT value FROM settings WHERE key = 'payments_required'")
    payments_required = cur.fetchone()
    conn.close()

    return render_template("admin.html", users=users, total_pages=total_pages, current_page=page, payments_required=payments_required)

@app.route("/admintoggle_user", methods=["POST"])
@admin_required
def admintoggle_user():
    email = request.form.get("email")
    set_paid = request.form.get("set_paid")
    if email and set_paid in ("0", "1"):
        conn = sqlite3.connect(DB_NAME)
        conn.execute("UPDATE users SET paid = ? WHERE email = ?", (int(set_paid), email))
        conn.execute("INSERT INTO admin_log (email, action) VALUES (?, ?)", (session["user"], f"Set {email} paid = {set_paid}"))
        conn.commit()
        conn.close()
    return redirect("/admin")

@app.route("/admintoggle_payments", methods=["POST"])
@admin_required
def admintoggle_payments():
    enable = request.form.get("enable")
    if enable in ("0", "1"):
        conn = sqlite3.connect(DB_NAME)
        conn.execute("UPDATE settings SET value = ? WHERE key = 'payments_required'", (enable,))
        conn.commit()
        conn.close()
    return redirect("/admin")

@app.route("/admin/export")
@admin_required
def export_users():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT email, role, paid FROM users")
    rows = cur.fetchall()
    conn.close()
    output = "email,role,paid\n" + "\n".join([f"{r[0]},{r[1]},{r[2]}" for r in rows])
    response = make_response(output)
    response.headers["Content-Disposition"] = "attachment; filename=users.csv"
    response.headers["Content-Type"] = "text/csv"
    return response



# --- Run App ---
if __name__ == "__main__":
    init_db()
    app.run(debug=False)