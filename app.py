import streamlit as st
from supabase import create_client, Client, ClientOptions
import mimetypes
import math
import hashlib
import time


# ----------------------------------------------------------------------
# Helper functions (unchanged)
# ----------------------------------------------------------------------
def format_size(bytes):
    if bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(bytes, 1024)))
    p = math.pow(1024, i)
    s = round(bytes / p, 2)
    return f"{s} {size_name[i]}"

def calculate_level(xp):
    return math.floor(math.sqrt(xp / 50))

def calculate_next_level_xp(level):
    return 50 * ((level + 1) ** 2)

# ----------------------------------------------------------------------
# Page config
# ----------------------------------------------------------------------
st.set_page_config(page_title="SST Study Sphere", page_icon="🏫", layout="wide")

# ----------------------------------------------------------------------
# Custom Storage for Streamlit (persists PKCE verifier across redirects)
# ----------------------------------------------------------------------
class StreamlitSessionStorage:
    def get_item(self, key: str) -> str:
        # Check both with and without prefix for compatibility
        val = st.session_state.get(f"sb_{key}") or st.session_state.get(key)
        return val
    def set_item(self, key: str, value: str) -> None:
        # Store in both places to ensure it's found regardless of prefixing
        st.session_state[f"sb_{key}"] = value
        st.session_state[key] = value
    def remove_item(self, key: str) -> None:
        st.session_state.pop(f"sb_{key}", None)
        st.session_state.pop(key, None)

# ----------------------------------------------------------------------
# Supabase client – stored in session_state to survive OAuth redirect
# ----------------------------------------------------------------------
def get_supabase():
    """Return Supabase client, stored in session_state to preserve PKCE verifier."""
    if 'supabase_client' not in st.session_state:
        try:
            st.session_state.supabase_client = create_client(
                st.secrets["supabase"]["url"],
                st.secrets["supabase"]["key"],
                options=ClientOptions(storage=StreamlitSessionStorage())
            )
        except Exception as e:
            st.error(f"Failed to connect to Supabase: {e}")
            st.stop()
    return st.session_state.supabase_client

supabase = get_supabase()

# ----------------------------------------------------------------------
# Storage bucket setup
# ----------------------------------------------------------------------
@st.cache_resource
def get_or_create_bucket():
    bucket_name = "file"
    try:
        buckets = supabase.storage.list_buckets()
        bucket_names = [b.name for b in buckets]
        if bucket_name not in bucket_names:
            supabase.storage.create_bucket(bucket_name, options={"public": True})
        return bucket_name
    except:
        return bucket_name

BUCKET_NAME = get_or_create_bucket()

# ----------------------------------------------------------------------
# Cached data fetchers
# ----------------------------------------------------------------------
@st.cache_data(ttl=1)
def fetch_projects(query, subject, level, sort_by):
    db_query = supabase.table("projects").select("*")
    if sort_by == "Most Likes":
        db_query = db_query.order("likes", desc=True)
    else:  # Recent
        db_query = db_query.order("created_at", desc=True)

    if subject != 'All':
        db_query = db_query.eq("subject", subject)
    if level != 'All':
        db_query = db_query.cs("level", [level])
    if query:
        db_query = db_query.ilike("title", f"%{query}%")
    return db_query.execute().data

@st.cache_data(ttl=10)
def fetch_leaderboard():
    response = supabase.table("users").select("username, xp").order("xp", desc=True).limit(50).execute()
    return response.data

# ----------------------------------------------------------------------
# DataManager class (with improvements – no OAuth changes needed inside)
# ----------------------------------------------------------------------
class DataManager:
    def __init__(self):
        self.bucket_name = BUCKET_NAME
        if 'user_likes' not in st.session_state:
            if 'user' in st.session_state and st.session_state.user:
                st.session_state.user_likes = self.get_user_likes()
            else:
                st.session_state.user_likes = []
        self.user_likes = st.session_state.user_likes

    def hash_password(self, password):
        return hashlib.sha256(password.encode()).hexdigest()

    def signup(self, email, username, password):
        try:
            if not (3 <= len(username) <= 36):
                return False, "Username must be between 3 and 36 characters."

            existing_user = supabase.table("users").select("*").eq("username", username).execute()
            if existing_user.data:
                return False, "Username already taken."

            existing_email = supabase.table("users").select("*").eq("email", email).execute()
            if existing_email.data:
                return False, "Email already registered."

            hashed_pw = self.hash_password(password)
            user_data = {
                "email": email,
                "username": username,
                "password": hashed_pw,
                "xp": 0
            }
            response = supabase.table("users").insert(user_data).execute()
            if response.data:
                return True, response.data[0]
            return False, "Signup failed."
        except Exception as e:
            return False, str(e)

    def login(self, email, password):
        try:
            hashed_pw = self.hash_password(password)
            response = supabase.table("users").select("*").eq("email", email).eq("password", hashed_pw).execute()
            if response.data:
                return True, response.data[0]
            return False, "Invalid email or password."
        except Exception as e:
            return False, str(e)

    def sync_google_user(self, email):
        """Ensures a Google-authenticated user exists in public.users table."""
        try:
            res = supabase.table("users").select("*").eq("email", email).execute()
            if res.data:
                return res.data[0]

            username = email.split("@")[0]
            check = supabase.table("users").select("username").eq("username", username).execute()
            if check.data:
                import random
                username = f"{username}{random.randint(100, 999)}"

            new_user = {
                "email": email,
                "username": username,
                "password": "GOOGLE_AUTH_USER",
                "xp": 0
            }
            res = supabase.table("users").insert(new_user).execute()
            if res.data:
                return res.data[0]
            return None
        except Exception as e:
            print(f"Sync Google User Error: {e}")
            return None

    def refresh_user(self):
        if 'user' in st.session_state and st.session_state.user:
            try:
                username = st.session_state.user['username']
                response = supabase.table("users").select("*").eq("username", username).execute()
                if response.data:
                    st.session_state.user = response.data[0]
            except Exception as e:
                print(f"Error refreshing user: {e}")

    def get_user_likes(self):
        if 'user' not in st.session_state or not st.session_state.user:
            return []
        try:
            username = st.session_state.user['username']
            response = supabase.table("project_likes").select("project_id").eq("username", username).execute()
            return [r['project_id'] for r in response.data]
        except Exception as e:
            print(f"Error fetching user likes: {e}")
            return []

    def add_note(self, title, subject, level, description, uploaded_file):
        if 'user' not in st.session_state or not st.session_state.user:
            return False

        current_username = st.session_state.user['username']
        file_url = "#"
        file_name = None
        file_size = 0

        if uploaded_file:
            file_name = uploaded_file.name
            file_size = uploaded_file.size
            safe_filename = file_name.replace(" ", "_").replace("(", "").replace(")", "")
            file_path = f"{current_username}/{safe_filename}"

            try:
                file_bytes = uploaded_file.getvalue()
                content_type = uploaded_file.type or mimetypes.guess_type(file_name)[0]
                supabase.storage.from_(self.bucket_name).upload(
                    path=file_path,
                    file=file_bytes,
                    file_options={"content-type": content_type, "upsert": "true"}
                )
                file_url = supabase.storage.from_(self.bucket_name).get_public_url(file_path)
            except Exception as e:
                st.error(f"File upload failed: {e}")
                return False

        new_note = {
            "title": title,
            "subject": subject,
            "level": [level],
            "author": current_username,
            "description": description,
            "file": file_url,
            "file_name": file_name,
            "file_size": file_size,
            "likes": 0
        }

        try:
            supabase.table("projects").insert(new_note).execute()
            author_data = supabase.table("users").select("xp").eq("username", current_username).execute()
            if author_data.data:
                current_xp = author_data.data[0]['xp']
                new_xp = current_xp + 15
                supabase.table("users").update({"xp": new_xp}).eq("username", current_username).execute()

            fetch_projects.clear()
            fetch_leaderboard.clear()
            self.refresh_user()
            return True
        except Exception as e:
            st.error(f"Database insert failed: {e}")
            return False

    def like_note(self, note_id, current_likes, note_author):
        if 'user' not in st.session_state or not st.session_state.user:
            st.warning("You must be logged in to like.")
            return False

        if note_id in st.session_state.user_likes:
            return False

        try:
            current_username = st.session_state.user['username']
            supabase.table("project_likes").insert({
                "project_id": note_id,
                "username": current_username
            }).execute()
            supabase.table("projects").update({"likes": current_likes + 1}).eq("id", note_id).execute()

            author_data = supabase.table("users").select("xp").eq("username", note_author).execute()
            if author_data.data:
                current_xp = author_data.data[0]['xp']
                new_xp = current_xp + 10
                supabase.table("users").update({"xp": new_xp}).eq("username", note_author).execute()

            st.session_state.user_likes.append(note_id)
            fetch_projects.clear()
            fetch_leaderboard.clear()
            if current_username == note_author:
                self.refresh_user()
            return True
        except Exception as e:
            st.error(f"Like failed: {e}")
            return False

    def get_projects(self, query="", subject='All', level='All', sort_by="Recent"):
        try:
            return fetch_projects(query, subject, level, sort_by)
        except Exception as e:
            st.error(f"Error fetching projects: {e}")
            return []

    def get_leaderboard(self):
        try:
            return fetch_leaderboard()
        except Exception as e:
            st.error(f"Error fetching leaderboard: {e}")
            return []

data = DataManager()

# ----------------------------------------------------------------------
# CSS Styling (unchanged)
# ----------------------------------------------------------------------
st.markdown("""
    <style>
    .main-header { font-size: 3.15rem; color: #4C51BF; font-weight: bold}
    
    .note-card {
        background-color: #898989 !important;
        border: 1px solid #e2e8f0 !important;
        border-radius: 8px !important;
        padding: 24px !important;
        color: #000000 !important;
        display: flex;
        height: 250px;
        flex-direction: column;
        justify-content: flex-start;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    }

    .note-card h4 {
        color: #000000 !important;
        font-size: 1.5rem !important;
    }

    .note-author { 
        color: #1f2937 !important; 
        font-weight: 700; 
        font-size: 0.9rem;
        margin-bottom: 12px;
    }

    .note-tag {
        background-color: #d1d5db !important;
        color: #000000 !important;
        padding: 6px 12px;
        border-radius: 8px;
        font-size: 0.85rem;
        font-weight: 600;
        display: block;
        width: 100%;
        margin-bottom: 18px;
    }

    .note-description {
        color: #000000 !important;
        font-size: 1rem;
        line-height: 1.5;
        max-height: 4.5em;
        display: -webkit-box;
        -webkit-line-clamp: 3;
        -webkit-box-orient: vertical;
        overflow: hidden;
        text-overflow: ellipsis;
        margin-bottom: 8px;
        word-break: break-all;
        overflow-wrap: break-word;
    }

    div[data-testid="stButton"] button, div[data-testid="stLinkButton"] a {
        background-color: #e5e7eb !important;
        color: #000000 !important;
        border: 1px solid #d1d5db !important;
        border-radius: 8px !important;
        width: 100% !important;
        padding: 6px !important;
        font-weight: 600 !important;
        margin-bottom: 0px !important;
    }
    
    div[data-testid="stButton"] button:hover {
        background-color: #d1d5db !important;
    }

    button[data-testid="stTab"] p { font-size: 1.5rem !important; }
    div[data-testid="stExpander"] p { font-size: 1.5rem !important; }
    label[data-testid="stWidgetLabel"] p { font-size: 1.5rem !important; }
    
    div[data-testid="stForm"] input, div[data-testid="stForm"] textarea, div[data-testid="stForm"] div[role="combobox"] {
        font-size: 1.2rem !important;
    }

    .leaderboard-row {
        font-size: 1.5rem !important;
    }
    .leaderboard-rank {
        font-size: 1.8rem !important;
    }
    </style>
""", unsafe_allow_html=True)

# ----------------------------------------------------------------------
# DYNAMIC BASE URL DETECTION (fixes Google OAuth redirect)
# ----------------------------------------------------------------------
def get_base_url():
    """
    Returns the base URL of the current Streamlit app.
    Works both locally and on Streamlit Cloud.
    """
    try:
        headers = st.context.headers
        host = headers.get("host", "localhost:8501")
        # Protocol: use X-Forwarded-Proto if behind a proxy, else default to https
        proto = headers.get("x-forwarded-proto", "https").split(",")[0].strip()
        return f"{proto}://{host}"
    except:
        # Fallback for older Streamlit versions or local testing
        return "http://localhost:8501"

# ----------------------------------------------------------------------
# Authentication state
# ----------------------------------------------------------------------
if 'user' not in st.session_state:
    st.session_state.user = None

# ----------------------------------------------------------------------
# GOOGLE OAUTH CALLBACK HANDLER (with improved error handling)
# ----------------------------------------------------------------------
params = st.query_params
if "code" in params:
    try:
        # 1. Get code from URL (ensure it's a string)
        auth_code = params.get("code")
        if isinstance(auth_code, list): auth_code = auth_code[0]
        
        # 2. Find verifier in session state
        code_verifier = None
        # Look for any key that looks like a verifier
        for key in st.session_state.keys():
            if "code-verifier" in key or "code_verifier" in key:
                code_verifier = st.session_state[key]
                break
        
        # 3. DEBUG (Visible if error occurs)
        debug_info = {
            "has_code": bool(auth_code),
            "has_verifier": bool(code_verifier),
            "all_session_keys": list(st.session_state.keys())
        }

        # 4. Exchange the OAuth code for a session
        # We try to pass both auth_code and code_verifier explicitly
        # Some library versions expect a dict, some positional. We'll use a dict.
        exchange_params = {
            "auth_code": auth_code,
            "code_verifier": code_verifier
        }
        
        # If no verifier found, try passing just the code string as fallback
        if not code_verifier:
            try:
                session = supabase.auth.exchange_code_for_session(auth_code)
            except:
                session = supabase.auth.exchange_code_for_session(exchange_params)
        else:
            session = supabase.auth.exchange_code_for_session(exchange_params)
        if session and session.user and session.user.email:
            app_user = data.sync_google_user(session.user.email)
            if app_user:
                st.session_state.user = app_user
                st.session_state.user_likes = data.get_user_likes()
                st.success(f"Signed in as {app_user['username']} via Google!")
                st.query_params.clear()
                st.rerun()
            else:
                st.error("Google login succeeded but user profile could not be created.")
        else:
            st.error("Google login succeeded but no user session was returned.")
    except Exception as e:
        st.error(f"Google callback error: {e}")
        if 'debug_info' in locals():
            st.write("Debug Info:", debug_info)
        st.exception(e)  # Show full traceback for debugging
    finally:
        # Always clear the code to prevent reprocessing on refresh
        if "code" in st.query_params:
            st.query_params.clear()

# ----------------------------------------------------------------------
# LOGIN PAGE (if not authenticated)
# ----------------------------------------------------------------------
if st.session_state.user is None:
    st.markdown('<div class="main-header" style="text-align: center;">🏫 SST Study Sphere</div>', unsafe_allow_html=True)
    st.markdown("<h3 style='text-align: center;'>Please Sign In to Continue</h3>", unsafe_allow_html=True)

    # ---------- Google Sign-In Button (Custom HTML to force same tab) ----------
    try:
        redirect_url = get_base_url()
        auth_response = supabase.auth.sign_in_with_oauth({
            "provider": "google",
            "options": {
                "redirect_to": redirect_url
            }
        })
        if auth_response.url:
            # We use custom HTML because st.link_button defaults to target="_blank"
            st.markdown(f"""
                <a href="{auth_response.url}" target="_self" style="
                    display: block;
                    width: 100%;
                    background-color: #4285F4;
                    color: white;
                    text-align: center;
                    padding: 10px;
                    text-decoration: none;
                    border-radius: 8px;
                    font-weight: 600;
                    margin-bottom: 20px;
                ">🔵 Sign in with Google</a>
            """, unsafe_allow_html=True)
        else:
            st.error("Failed to start Google sign‑in – no authorization URL returned.")
    except Exception as e:
        st.error(f"Could not load Google Sign‑In: {e}")

    # ---------- OAuth Troubleshooting Diagnostics ----------
    with st.expander("🛠️ OAuth Troubleshooter"):
        st.write("Cross-check these values with your dashboards:")
        st.code(f"App Redirect URL: {get_base_url()}")
        st.write("1. **Google Cloud**: The 'Authorized redirect URI' must be the **Supabase** callback URL (found in Supabase > Authentication > Providers > Google).")
        st.write("2. **Supabase**: The 'Redirect URL' in Supabase URL Configuration must be the **App Redirect URL** shown above.")
        st.write("3. **Google Cloud**: Ensure 'User Type' is set to **External** on the OAuth consent screen.")

    # ---------- Email/Password Tabs ----------
    tab_login, tab_signup = st.tabs(["Sign In", "Sign Up"])

    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign In")
            if submitted:
                if not email or not password:
                    st.error("Please fill in all fields.")
                else:
                    success, res = data.login(email, password)
                    if success:
                        st.session_state.user = res
                        st.session_state.user_likes = data.get_user_likes()
                        st.success(f"Welcome back, {res['username']}!")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(res)

    with tab_signup:
        with st.form("signup_form"):
            new_email = st.text_input("Email")
            new_user = st.text_input("Username")
            new_pass = st.text_input("Password", type="password")
            confirm_pass = st.text_input("Confirm Password", type="password")
            submitted = st.form_submit_button("Sign Up")
            if submitted:
                if not new_user or not new_pass or not new_email:
                    st.error("Please fill in all fields.")
                elif new_pass != confirm_pass:
                    st.error("Passwords do not match.")
                else:
                    success, res = data.signup(new_email, new_user, new_pass)
                    if success:
                        st.session_state.user = res
                        st.session_state.user_likes = []
                        st.success("Account created successfully! Logging in...")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(res)

else:
    # ------------------------------------------------------------------
    # MAIN APP (Logged In)
    # ------------------------------------------------------------------
    data.refresh_user()

    with st.container():
        c1, c2, c3 = st.columns([3, 1, 0.5])
        c1.markdown('<div class="main-header">🏫 SST Study Sphere</div>', unsafe_allow_html=True)

        xp = st.session_state.user['xp']
        level = calculate_level(xp)
        next_level_xp = calculate_next_level_xp(level)

        c2.markdown(f"<div style='font-size: 120%;'>Welcome, {st.session_state.user['username']} | <b>Level {level}</b> ({xp}/{next_level_xp} XP)</div>", unsafe_allow_html=True)
        if c3.button("Logout"):
            st.session_state.user = None
            st.session_state.user_likes = []
            st.rerun()

    tab1, tab2, tab3 = st.tabs(["📚 Notes Forum", "🏆 Leaderboard", "🤖 AI Tutor"])

    # ---------- Notes Forum ----------
    with tab1:
        with st.expander("⬆️ Upload New Note"):
            with st.form("upload_form", clear_on_submit=True):
                u_title = st.text_input("Title")
                u_file = st.file_uploader("Upload PDF/Video", type=['pdf', 'mp4', 'png', 'jpg'])

                c_a, c_b = st.columns(2)
                u_subject = c_a.selectbox("Subject", ['English', 'Chinese', 'Malay', 'Tamil', 'Math', 'Physics', 'Chemistry', 'Biology', 'Computing', 'Biotechnology', 'Design Studies', 'Electronics', 'Geography', 'History', 'Social Studies', 'CCE', 'Changemakers'])
                u_level = c_b.selectbox("Level", ['Sec 1', 'Sec 2', 'Sec 3', 'Sec 4'])

                u_desc = st.text_area("Description")

                if st.form_submit_button("Post Note (+15 XP)"):
                    if u_title and u_desc:
                        with st.spinner("Publishing..."):
                            success = data.add_note(u_title, u_subject, u_level, u_desc, u_file)
                        if success:
                            st.success("Note Published! You gained 15 XP.")
                            time.sleep(1)
                            st.rerun()
                    else:
                        st.warning("Please enter a title and description.")

        col_search, col_sub, col_lvl, col_sort = st.columns([3, 1, 1, 1])
        search_query = col_search.text_input("Search", placeholder="Search notes...")
        subject_filter = col_sub.selectbox("Subject Filter", ['All', 'English', 'Chinese', 'Malay', 'Tamil', 'Math', 'Physics', 'Chemistry', 'Biology', 'Computing', 'Biotechnology', 'Design Studies', 'Electronics', 'Geography', 'History', 'Social Studies', 'CCE', 'Changemakers'])
        level_filter = col_lvl.selectbox("Level Filter", ['All', 'Sec 1', 'Sec 2', 'Sec 3', 'Sec 4'])
        sort_option = col_sort.selectbox("Sort By", ["Recent", "Most Likes"])

        notes = data.get_projects(search_query, subject_filter, level_filter, sort_option)

        if not notes:
            st.info("No notes found.")
        else:
            for i in range(0, len(notes), 3):
                row_notes = notes[i:i+3]
                cols = st.columns(3)
                for j in range(3):
                    with cols[j]:
                        if j < len(row_notes):
                            note = row_notes[j]
                            st.markdown(f"""
                                <div class="note-card">
                                    <h4>{note['title']}</h4>
                                    <div class="note-author">By {note['author']}</div>
                                    <div class="note-tag">{note['subject']} • {", ".join(note["level"]) if note["level"] else ""}</div>
                                    <div class="note-description">{note['description'] or "No description provided."}</div>
                                    <div style="margin-top: 0px;">
                            """, unsafe_allow_html=True)

                            if note['file'] and note['file'] != "#":
                                f_name = note.get('file_name') or "File"
                                download_url = f"{note['file']}?download="
                                st.link_button(f"⬇️ Download {f_name}", download_url, use_container_width=True)

                            has_liked = note['id'] in st.session_state.user_likes
                            btn_text = f"❤️ {note['likes']} Like" if not has_liked else f"💖 {note['likes']} Liked"

                            if st.button(btn_text, key=f"like_btn_{note['id']}", disabled=has_liked, use_container_width=True):
                                if data.like_note(note['id'], note['likes'], note['author']):
                                    st.rerun()

                            st.markdown('</div></div>', unsafe_allow_html=True)
                        else:
                            st.empty()

    # ---------- Leaderboard ----------
    with tab2:
        st.header("Leaderboard 🏆")
        leaderboard = data.get_leaderboard()

        for idx, user_row in enumerate(leaderboard):
            rank = idx + 1
            icon = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"#{rank}"

            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([1, 4, 1.5, 1.5])
                c1.markdown(f'<div class="leaderboard-rank">{icon}</div>', unsafe_allow_html=True)
                c2.markdown(f'<div class="leaderboard-row"><b>{user_row["username"]}</b></div>', unsafe_allow_html=True)

                u_xp = user_row["xp"]
                u_lvl = calculate_level(u_xp)

                c3.markdown(f'<div class="leaderboard-row">Lvl {u_lvl}</div>', unsafe_allow_html=True)
                c4.markdown(f'<div class="leaderboard-row">{u_xp} XP</div>', unsafe_allow_html=True)

    # ---------- AI Tutor (placeholder) ----------
    with tab3:
        st.header("AI Study Buddy 🤖")
        st.write("Coming soon...")