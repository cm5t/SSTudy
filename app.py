import streamlit as st
from supabase import create_client, Client
import mimetypes
import math
import hashlib
import time

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

st.set_page_config(page_title="SST Study Sphere", page_icon="🏫", layout="wide")

# --- Supabase Setup ---
@st.cache_resource
def get_supabase():
    try:
        return create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])
    except Exception as e:
        st.error(f"Failed to connect to Supabase: {e}")
        st.stop()

supabase = get_supabase()

@st.cache_resource
def get_or_create_bucket():
    bucket_name = "file"
    try:
        buckets = supabase.storage.list_buckets()
        bucket_names = [b.name for b in buckets]
        if bucket_name not in bucket_names:
            supabase.storage.create_bucket(bucket_name, options={"public": True})
            return bucket_name
        return bucket_name
    except:
        return bucket_name

BUCKET_NAME = get_or_create_bucket()

@st.cache_data(ttl=1) # Reduced TTL to ensure freshness or we can clear it manually
def fetch_projects(query, subject, level, sort_by):
    db_query = supabase.table("projects").select("*")
    
    # Sorting
    if sort_by == "Most Likes":
        db_query = db_query.order("likes", desc=True)
    else: # Recent
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
    # Fetch directly from users table now
    response = supabase.table("users").select("username, xp").order("xp", desc=True).limit(50).execute()
    return response.data

class DataManager:
    def __init__(self):
        self.bucket_name = BUCKET_NAME
        
        # Initialize session state for user-specific likes if not present
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
            # Check if user exists (username OR email)
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
            # Login with EMAIL now
            response = supabase.table("users").select("*").eq("email", email).eq("password", hashed_pw).execute()
            if response.data:
                return True, response.data[0]
            return False, "Invalid email or password."
        except Exception as e:
            return False, str(e)
            
    def refresh_user(self):
        """Re-fetches the current user's data (XP, etc) from the DB."""
        if 'user' in st.session_state and st.session_state.user:
            try:
                username = st.session_state.user['username']
                # Fetch fresh data
                response = supabase.table("users").select("*").eq("username", username).execute()
                if response.data:
                    st.session_state.user = response.data[0]
                    # Also refresh likes just in case
                    # st.session_state.user_likes = self.get_user_likes() 
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
            # Metadata
            file_name = uploaded_file.name
            file_size = uploaded_file.size
            
            # Clean filename for storage path
            safe_filename = file_name.replace(" ", "_").replace("(", "").replace(")", "")
            file_path = f"{current_username}/{safe_filename}"
            
            # 1. Upload file to Storage
            try:
                file_bytes = uploaded_file.getvalue()
                content_type = uploaded_file.type or mimetypes.guess_type(file_name)[0]
                
                # Check if exists (optional, simply overwriting here for demo simplicity)
                supabase.storage.from_(self.bucket_name).upload(
                    path=file_path, 
                    file=file_bytes,
                    file_options={"content-type": content_type, "upsert": "true"}
                )
                
                # 2. Get Public URL
                file_url = supabase.storage.from_(self.bucket_name).get_public_url(file_path)
            except Exception as e:
                st.error(f"File upload failed: {e}")
                return False

        # 3. Database Insert
        new_note = {
            "title": title,
            "subject": subject,
            "level": [level], # Store as array
            "author": current_username,
            "description": description,
            "file": file_url,
            "file_name": file_name,
            "file_size": file_size,
            "likes": 0
        }
        
        try:
            supabase.table("projects").insert(new_note).execute()
            
            # 4. Award XP (+15)
            # Read -> Write pattern
            author_data = supabase.table("users").select("xp").eq("username", current_username).execute()
            if author_data.data:
                current_xp = author_data.data[0]['xp']
                new_xp = current_xp + 15
                supabase.table("users").update({"xp": new_xp}).eq("username", current_username).execute()
            
            # 5. Refresh everything
            fetch_projects.clear()
            fetch_leaderboard.clear()
            self.refresh_user() # Updates local session XP immediately
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
            # 1. Record the like
            supabase.table("project_likes").insert({
                "project_id": note_id,
                "username": current_username
            }).execute()
            
            # 2. Increment the counter
            supabase.table("projects").update({"likes": current_likes + 1}).eq("id", note_id).execute()

            # 3. Give XP to the Author (+10)
            author_data = supabase.table("users").select("xp").eq("username", note_author).execute()
            if author_data.data:
                current_xp = author_data.data[0]['xp']
                new_xp = current_xp + 10
                supabase.table("users").update({"xp": new_xp}).eq("username", note_author).execute()
            
            # 4. Update local state immediately
            st.session_state.user_likes.append(note_id)
            # Invalidate caches to show new data
            fetch_projects.clear()
            fetch_leaderboard.clear()
            
            # Refresh user if they liked their own note (if that was possible, but logic usually prevents self-XP or self-like if desired, but here we just refresh to be safe)
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

# --- CSS & Layout ---
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
        max-height: 4.5em; /* Exactly 3 lines */
        display: -webkit-box;
        -webkit-line-clamp: 3;
        -webkit-box-orient: vertical;
        overflow: hidden;
        text-overflow: ellipsis;
        margin-bottom: 8px;
        word-break: break-all; /* FORCES wrapping for strings without spaces */
        overflow-wrap: break-word;
    }

    /* Light Gray Background for Buttons as requested */
    div[data-testid="stButton"] button, div[data-testid="stLinkButton"] a {
        background-color: #e5e7eb !important;
        color: #000000 !important; /* Black text on light gray */
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

    /* Double font size for Tabs, Expanders, and Labels */
    button[data-testid="stTab"] p { font-size: 1.5rem !important; }
    div[data-testid="stExpander"] p { font-size: 1.5rem !important; }
    label[data-testid="stWidgetLabel"] p { font-size: 1.5rem !important; }
    
    /* Input fields themselves */
    div[data-testid="stForm"] input, div[data-testid="stForm"] textarea, div[data-testid="stForm"] div[role="combobox"] {
        font-size: 1.2rem !important;
    }

    /* Leaderboard font scaling */
    .leaderboard-row {
        font-size: 1.5rem !important;
    }
    .leaderboard-rank {
        font-size: 1.8rem !important;
    }
    </style>
""", unsafe_allow_html=True)


# --- Authentication Logic ---
if 'user' not in st.session_state:
    st.session_state.user = None

if st.session_state.user is None:
    st.markdown('<div class="main-header" style="text-align: center;">🏫 SST Study Sphere</div>', unsafe_allow_html=True)
    st.markdown("<h3 style='text-align: center;'>Please Sign In to Continue</h3>", unsafe_allow_html=True)
    
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
    # --- Main App (Logged In) ---
    
    # Always refresh user data on load to ensure XP is up to date
    data.refresh_user()
    
    with st.container():
        c1, c2, c3 = st.columns([3, 1, 0.5])
        c1.markdown('<div class="main-header">🏫 SST Study Sphere</div>', unsafe_allow_html=True)
        
        # Calculate Level
        xp = st.session_state.user['xp']
        level = calculate_level(xp)
        next_level_xp = calculate_next_level_xp(level)
        
        c2.markdown(f"<div style='font-size: 120%;'>Welcome, {st.session_state.user['username']} | <b>Level {level}</b> ({xp}/{next_level_xp} XP)</div>", unsafe_allow_html=True)
        if c3.button("Logout"):
            st.session_state.user = None
            st.session_state.user_likes = []
            st.rerun()

    tab1, tab2, tab3 = st.tabs(["📚 Notes Forum", "🏆 Leaderboard", "🤖 AI Tutor"])

    # --- Notes Tab ---
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
                            time.sleep(1) # Let them read it
                            st.rerun()
                    else:
                        st.warning("Please enter a title and description.")

        # Search & Filter
        col_search, col_sub, col_lvl, col_sort = st.columns([3, 1, 1, 1])
        search_query = col_search.text_input("Search", placeholder="Search notes...")
        subject_filter = col_sub.selectbox("Subject Filter", ['All', 'English', 'Chinese', 'Malay', 'Tamil', 'Math', 'Physics', 'Chemistry', 'Biology', 'Computing', 'Biotechnology', 'Design Studies', 'Electronics', 'Geography', 'History', 'Social Studies', 'CCE', 'Changemakers'])
        level_filter = col_lvl.selectbox("Level Filter", ['All', 'Sec 1', 'Sec 2', 'Sec 3', 'Sec 4'])
        sort_option = col_sort.selectbox("Sort By", ["Recent", "Most Likes"])

        # Grid - Using unified containers for stability
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
                            # Use a standard container and our custom .note-card class
                            st.markdown(f"""
                                <div class="note-card">
                                    <h4>{note['title']}</h4>
                                    <div class="note-author">By {note['author']}</div>
                                    <div class="note-tag">{note['subject']} • {", ".join(note["level"]) if note["level"] else ""}</div>
                                    <div class="note-description">{note['description'] or "No description provided."}</div>
                                    <div style="margin-top: 0px;">
                            """, unsafe_allow_html=True)
                            
                            # Buttons inside the auto-pushed bottom area
                            if note['file'] and note['file'] != "#":
                                f_name = note.get('file_name') or "File"
                                # Append ?download= to force browser download instead of opening in tab
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

    # --- Leaderboard Tab ---
    with tab2:
        st.header("Leaderboard 🏆")
        leaderboard = data.get_leaderboard()
        
        for idx, user_row in enumerate(leaderboard):
            rank = idx + 1
            icon = "🥇" if rank==1 else "🥈" if rank==2 else "🥉" if rank==3 else f"#{rank}"
            
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([1, 4, 1.5, 1.5])
                c1.markdown(f'<div class="leaderboard-rank">{icon}</div>', unsafe_allow_html=True)
                c2.markdown(f'<div class="leaderboard-row"><b>{user_row["username"]}</b></div>', unsafe_allow_html=True)
                
                u_xp = user_row["xp"]
                u_lvl = calculate_level(u_xp)
                
                c3.markdown(f'<div class="leaderboard-row">Lvl {u_lvl}</div>', unsafe_allow_html=True)
                c4.markdown(f'<div class="leaderboard-row">{u_xp} XP</div>', unsafe_allow_html=True)

    # --- AI Tutor Tab ---
    with tab3:
        st.header("AI Study Buddy 🤖")
        st.write("Coming soon...")
