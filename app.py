import streamlit as st
from supabase import create_client, Client
import mimetypes
import math

def format_size(bytes):
    if bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(bytes, 1024)))
    p = math.pow(1024, i)
    s = round(bytes / p, 2)
    return f"{s} {size_name[i]}"

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

@st.cache_data(ttl=10)
def fetch_projects(query, subject, level):
    db_query = supabase.table("projects").select("*").order("created_at", desc=True)
    if subject != 'All':
        db_query = db_query.eq("subject", subject)
    if level != 'All':
        db_query = db_query.cs("level", [level]) 
    if query:
        db_query = db_query.ilike("title", f"%{query}%")
    return db_query.execute().data

@st.cache_data(ttl=60)
def fetch_leaderboard():
    response = supabase.table("projects").select("author, likes").execute()
    projects_data = response.data
    scores = {}
    for note in projects_data:
        author = note['author']
        likes = note['likes']
        if author not in scores:
            scores[author] = 0
        scores[author] += likes * 10
    leaderboard = [{"username": k, "points": v, "house": "Unknown"} for k, v in scores.items()]
    return sorted(leaderboard, key=lambda x: x['points'], reverse=True)

class DataManager:
    def __init__(self):
        # In a real app, you'd handle auth. For now, mocking a logged-in user.
        self.current_user = {"username": "Alice", "house": "Blue House"}
        self.bucket_name = BUCKET_NAME
        
        # Initialize session state for user-specific likes if not present
        if 'user_likes' not in st.session_state:
            st.session_state.user_likes = self.get_user_likes()
        self.user_likes = st.session_state.user_likes

    def get_user_likes(self):
        try:
            response = supabase.table("project_likes").select("project_id").eq("username", self.current_user['username']).execute()
            return [r['project_id'] for r in response.data]
        except Exception as e:
            print(f"Error fetching user likes: {e}")
            return []


    def add_note(self, title, subject, level, description, uploaded_file):
        file_url = "#"
        file_name = None
        file_size = 0
        
        if uploaded_file:
            # Metadata
            file_name = uploaded_file.name
            file_size = uploaded_file.size
            
            # Clean filename for storage path
            safe_filename = file_name.replace(" ", "_").replace("(", "").replace(")", "")
            file_path = f"{self.current_user['username']}/{safe_filename}"
            
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
                # Debugging: List buckets
                try:
                    buckets = supabase.storage.list_buckets()
                    valid_names = [b.name for b in buckets]
                    st.error(f"Available buckets: {valid_names}")
                except Exception as b_e:
                    st.error(f"Could not list buckets: {b_e}")
                return False

        # 3. Database Insert
        new_note = {
            "title": title,
            "subject": subject,
            "level": [level], # Store as array
            "author": self.current_user['username'],
            "description": description,
            "file": file_url,
            "file_name": file_name,
            "file_size": file_size,
            "likes": 0
        }
        
        try:
            supabase.table("projects").insert(new_note).execute()
            # CLEAR CACHE so the new note shows up immediately
            fetch_projects.clear()
            fetch_leaderboard.clear()
            return True
        except Exception as e:
            st.error(f"Database insert failed: {e}")
            return False

    def like_note(self, note_id, current_likes):
        if note_id in st.session_state.user_likes:
            return False
            
        try:
            # 1. Record the like
            supabase.table("project_likes").insert({
                "project_id": note_id,
                "username": self.current_user['username']
            }).execute()
            
            # 2. Increment the counter
            supabase.table("projects").update({"likes": current_likes + 1}).eq("id", note_id).execute()
            
            # 3. Update local state immediately
            st.session_state.user_likes.append(note_id)
            # Invalidate caches to show new data
            fetch_projects.clear()
            fetch_leaderboard.clear()
            return True
        except Exception as e:
            st.error(f"Like failed: {e}")
            return False
            
    def get_projects(self, query="", subject='All', level='All'):
        try:
            return fetch_projects(query, subject, level)
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
    .main-header { font-size: 2.3rem; color: #4C51BF; font-weight: bold}
    
    .note-card {
        background-color: #898989 !important;
        border: 1px solid #e2e8f0 !important;
        border-radius: 12px !important;
        padding: 24px !important;
        color: #000000 !important;
        height: 282px;
        display: flex;
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
        margin-bottom: 20px;
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
        padding: 10px !important;
        font-weight: 600 !important;
    }
    
    div[data-testid="stButton"] button:hover {
        background-color: #d1d5db !important;
    }
    </style>
""", unsafe_allow_html=True)

with st.container():
    c1, c2 = st.columns([3, 1])
    c1.markdown('<div class="main-header">🏫 SST Study Sphere</div>', unsafe_allow_html=True)
    c2.write(f"**Welcome, {data.current_user['username']}**")

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
            
            if st.form_submit_button("Post Note"):
                if u_title and u_desc:
                    with st.spinner("Publishing..."):
                        success = data.add_note(u_title, u_subject, u_level, u_desc, u_file)
                    if success:
                        st.success("Note Published!")
                        st.rerun()
                else:
                    st.warning("Please enter a title and description.")

    # Search & Filter
    col_search, col_sub, col_lvl = st.columns([3, 1, 1])
    search_query = col_search.text_input("Search", placeholder="Search notes...")
    subject_filter = col_sub.selectbox("Subject Filter", ['All', 'English', 'Chinese', 'Malay', 'Tamil', 'Math', 'Physics', 'Chemistry', 'Biology', 'Computing', 'Biotechnology', 'Design Studies', 'Electronics', 'Geography', 'History', 'Social Studies', 'CCE', 'Changemakers'])
    level_filter = col_lvl.selectbox("Level Filter", ['All', 'Sec 1', 'Sec 2', 'Sec 3', 'Sec 4'])

    # Grid - Using unified containers for stability
    notes = data.get_projects(search_query, subject_filter, level_filter)
    
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
                                <div style="margin-top: auto;">
                        """, unsafe_allow_html=True)
                        
                        # Buttons inside the auto-pushed bottom area
                        if note['file'] and note['file'] != "#":
                            f_name = note.get('file_name') or "File"
                            label = f"⬇️ Download {f_name}"
                            st.link_button(label, note['file'], use_container_width=True)
                        
                        has_liked = note['id'] in st.session_state.user_likes
                        btn_text = f"❤️ {note['likes']} Like" if not has_liked else f"💖 {note['likes']} Liked"
                        
                        if st.button(btn_text, key=f"like_btn_{note['id']}", disabled=has_liked, use_container_width=True):
                            if data.like_note(note['id'], note['likes']):
                                st.rerun()
                        
                        st.markdown('</div></div>', unsafe_allow_html=True)
                    else:
                        st.empty()

# --- Leaderboard Tab ---
with tab2:
    st.header("Leaderboard 🏆")
    leaderboard = data.get_leaderboard()
    
    for idx, user in enumerate(leaderboard):
        rank = idx + 1
        icon = "🥇" if rank==1 else "🥈" if rank==2 else "🥉" if rank==3 else f"#{rank}"
        
        with st.container(border=True):
             c1, c2, c3 = st.columns([1, 4, 1])
             c1.subheader(icon)
             c2.markdown(f"**{user['username']}**")
             c3.markdown(f"**{user['points']} pts**")

# --- AI Tutor Tab ---
with tab3:
    st.header("AI Study Buddy 🤖")
    st.write("Coming soon...")
