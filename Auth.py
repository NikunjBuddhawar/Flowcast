import streamlit as st
import sqlite3

# ---------- DATABASE SETUP ----------
conn = sqlite3.connect('users.db')
c = conn.cursor()
c.execute('''
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password TEXT NOT NULL,
        name TEXT NOT NULL,
        role TEXT NOT NULL
    )
''')
conn.commit()
conn.close()

# ---------- FUNCTIONS ----------
def create_user(username, password, name, role):
    try:
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute('INSERT INTO users (username, password, name, role) VALUES (?, ?, ?, ?)',
                  (username, password, name, role))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False

def login_user(username, password, role):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE username = ? AND password = ? AND role = ?',
              (username, password, role))
    user = c.fetchone()
    conn.close()
    return user

# ---------- SESSION STATE SETUP ----------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.role = ""
    st.session_state.name = ""

if "auth_mode" not in st.session_state:
    st.session_state.auth_mode = "Login"

# ---------- PAGE SETUP ----------
st.set_page_config(page_title="Login Portal", layout="centered")
st.title("🔐 Welcome to the Portal")

# ---------- IF LOGGED IN: Prevent Access to Auth ----------
if st.session_state.get("logged_in"):
    st.success(f"✅ Logged in as {st.session_state.name} ({st.session_state.role})")
    
    # Disable Auth button (simulated here as caption instead of real button)
    st.button("🔐 Already Logged In", disabled=True)

    # Redirect immediately
    if st.session_state.role == "User":
        st.switch_page("pages/Forecast.py")
    else:
        st.switch_page("pages/App.py")

    # Prevent rest of page from rendering
    st.stop()

# ---------- IF NOT LOGGED IN: Show Auth UI ----------
auth_mode = st.radio("Select Mode", ["Login", "Sign Up"],
                     index=0 if st.session_state.auth_mode == "Login" else 1)
st.session_state.auth_mode = auth_mode

is_retailer = st.toggle("🧑 Slide Right for Retailer Role", value=False)
role = "Retailer" if is_retailer else "User"
st.markdown("---")

if auth_mode == "Login":
    st.subheader("🔓 Login")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        user = login_user(username, password, role)
        if user:
            st.success(f"Welcome {user[2]}! Logged in as {user[3]}")
            st.session_state.logged_in = True
            st.session_state.username = username
            st.session_state.role = role
            st.session_state.name = user[2]
            st.rerun()
        else:
            st.error("Invalid credentials or role.")

else:
    st.subheader("📝 Create an Account")
    name = st.text_input("Full Name")
    username = st.text_input("Choose Username")
    password = st.text_input("Password", type="password")
    confirm_password = st.text_input("Confirm Password", type="password")

    if st.button("Create Account"):
        if not all([username, password, name, confirm_password]):
            st.warning("All fields are required.")
        elif password != confirm_password:
            st.error("Passwords do not match.")
        elif len(password) < 4:
            st.warning("Password too short.")
        else:
            success = create_user(username, password, name, role)
            if success:
                st.success("✅ Account created! Please log in.")
                st.session_state.auth_mode = "Login"
                st.rerun()
            else:
                st.error("❌ Username already exists.")
