import time
from datetime import date as date_type, datetime
import streamlit as st
import httpx
from supabase import create_client, Client
from calculations import compute_estimate, CONFIDENCE_LABELS

st.set_page_config(
    page_title="Release Estimation",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Hide Streamlit chrome ──────────────────────────────────────────────────────
st.markdown("""
<style>
#MainMenu  {visibility: hidden;}
footer     {visibility: hidden;}
header     {visibility: hidden;}
section[data-testid="stSidebar"] {background-color: #1e2a3a;}
section[data-testid="stSidebar"] * {color: #ffffff !important;}
section[data-testid="stSidebar"] .stButton > button {
    background-color: #2c3e50; color: #ffffff; border: none;
    width: 100%; text-align: left;
}
section[data-testid="stSidebar"] .stButton > button:hover {background-color: #34495e;}
section[data-testid="stSidebar"] .stSelectbox div[data-baseweb="select"] * {color: #000000 !important;}
</style>
""", unsafe_allow_html=True)


# ── Supabase client ────────────────────────────────────────────────────────────
def get_supabase() -> Client:
    if "supabase_client" not in st.session_state:
        st.session_state["supabase_client"] = create_client(
            st.secrets["supabase_url"],
            st.secrets["supabase_anon_key"],
        )
    return st.session_state["supabase_client"]


def get_supabase_public() -> Client:
    """Unauthenticated client for public/shared pages."""
    return create_client(
        st.secrets["supabase_url"],
        st.secrets["supabase_anon_key"],
    )


# ── Token refresh helpers ──────────────────────────────────────────────────────
def _raw_token_refresh(refresh_token: str) -> dict | None:
    """Exchange a refresh token via Supabase REST API directly."""
    try:
        url  = f"{st.secrets['supabase_url']}/auth/v1/token?grant_type=refresh_token"
        hdrs = {"apikey": st.secrets["supabase_anon_key"], "Content-Type": "application/json"}
        resp = httpx.post(url, json={"refresh_token": refresh_token}, headers=hdrs, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        st.session_state["debug_refresh_detail"] = f"HTTP {resp.status_code}: {resp.text[:300]}"
    except Exception as e:
        st.session_state["debug_refresh_detail"] = f"Exception: {str(e)[:300]}"
    return None


def _parse_expires_at(data: dict) -> float:
    """Compute expires_at from expires_in — avoids trusting the library's value."""
    expires_in = data.get("expires_in", 3600)
    return time.time() + float(expires_in or 3600)


def restore_session() -> bool:
    if not st.session_state.get("access_token"):
        return False

    expires_at = st.session_state.get("expires_at", 0)

    # Proactively refresh if token is expired or expiry is unknown.
    # expires_at defaults to 0 after a server-session restore (it is not
    # persisted to user_sessions), so a refresh always runs after browser reload.
    if time.time() >= expires_at - 60:
        data = _raw_token_refresh(st.session_state.get("refresh_token", ""))
        if not (data and data.get("access_token")):
            clear_session()
            return False
        st.session_state["access_token"]  = data["access_token"]
        st.session_state["refresh_token"] = data.get("refresh_token", st.session_state["refresh_token"])
        st.session_state["expires_at"]    = _parse_expires_at(data)
        st.session_state.pop("supabase_client", None)

    try:
        get_supabase().auth.set_session(
            st.session_state["access_token"],
            st.session_state["refresh_token"],
        )
    except Exception:
        # set_session threw — last-resort refresh attempt
        data = _raw_token_refresh(st.session_state.get("refresh_token", ""))
        if data and data.get("access_token"):
            st.session_state["access_token"]  = data["access_token"]
            st.session_state["refresh_token"] = data.get("refresh_token", st.session_state["refresh_token"])
            st.session_state["expires_at"]    = _parse_expires_at(data)
            st.session_state.pop("supabase_client", None)
            try:
                get_supabase().auth.set_session(data["access_token"], data.get("refresh_token", ""))
            except Exception:
                pass
        else:
            clear_session()
            return False
    return True


def clear_session():
    try:
        if "sid" in st.query_params:
            del st.query_params["sid"]
    except Exception:
        pass
    for key in ["access_token", "refresh_token", "expires_at", "user_id", "user_email",
                "current_team_id", "current_team_name", "page", "supabase_client", "session_id"]:
        st.session_state.pop(key, None)


# ── Server-side session store ──────────────────────────────────────────────────
def create_server_session() -> str | None:
    try:
        result = db().table("user_sessions").insert({
            "user_id":       st.session_state["user_id"],
            "access_token":  st.session_state["access_token"],
            "refresh_token": st.session_state["refresh_token"],
        }).execute()
        return result.data[0]["id"]
    except Exception:
        return None


def load_server_session(sid: str) -> bool:
    try:
        result = get_supabase().table("user_sessions").select("*").eq("id", sid).execute()
        if result.data:
            row = result.data[0]
            st.session_state["access_token"]  = row["access_token"]
            st.session_state["refresh_token"] = row["refresh_token"]
            st.session_state["user_id"]       = row["user_id"]
            st.session_state["session_id"]    = sid
            if row.get("current_page"):
                st.session_state["page"] = row["current_page"]
            if row.get("current_team_id"):
                st.session_state["current_team_id"] = row["current_team_id"]
            if row.get("current_team_name"):
                st.session_state["current_team_name"] = row["current_team_name"]
            return True
        return False
    except Exception:
        return False


def update_server_session():
    sid = st.session_state.get("session_id")
    if not sid:
        return
    try:
        db().table("user_sessions").update({
            "access_token":      st.session_state["access_token"],
            "refresh_token":     st.session_state["refresh_token"],
            "current_page":      st.session_state.get("page", "teams"),
            "current_team_id":   st.session_state.get("current_team_id"),
            "current_team_name": st.session_state.get("current_team_name"),
        }).eq("id", sid).execute()
    except Exception:
        pass


def delete_server_session():
    sid = st.session_state.get("session_id")
    if not sid:
        return
    try:
        db().table("user_sessions").delete().eq("id", sid).execute()
    except Exception:
        pass


# ── Auth helpers ───────────────────────────────────────────────────────────────
def is_authenticated() -> bool:
    return bool(st.session_state.get("access_token"))


def is_auth_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(k in msg for k in ["jwt expired", "invalid jwt", "token expired",
                                   "not authenticated", "session expired", "refresh token"])


def do_login(email: str, password: str):
    try:
        r = get_supabase().auth.sign_in_with_password({"email": email, "password": password})
        st.session_state["access_token"]  = r.session.access_token
        st.session_state["refresh_token"] = r.session.refresh_token
        st.session_state["expires_at"]    = time.time() + 3600
        st.session_state["user_id"]       = r.user.id
        st.session_state["user_email"]    = r.user.email
        st.session_state["page"]          = "teams"
        sid = create_server_session()
        if sid:
            st.session_state["session_id"] = sid
            st.query_params["sid"] = sid
        return None
    except Exception as e:
        return str(e)


def do_signup(email: str, password: str):
    try:
        get_supabase().auth.sign_up({"email": email, "password": password})
        return None, "Account created. Check your email to confirm before logging in."
    except Exception as e:
        return str(e), None


def do_logout():
    try:
        get_supabase().auth.sign_out()
    except Exception:
        pass
    delete_server_session()
    clear_session()


def handle_password_recovery(token_hash: str = None, code: str = None,
                             access_token: str = None, refresh_token: str = None):
    st.title("Reset Your Password")

    if "recovery_session_set" not in st.session_state:
        try:
            if token_hash:
                r = get_supabase().auth.verify_otp({"token_hash": token_hash, "type": "recovery"})
            elif code:
                r = get_supabase().auth.exchange_code_for_session({"auth_code": code})
            elif access_token:
                r = get_supabase().auth.set_session(access_token, refresh_token or "")
            else:
                st.error("Invalid recovery link.")
                return
            st.session_state["recovery_access_token"]  = r.session.access_token
            st.session_state["recovery_refresh_token"] = r.session.refresh_token
            st.session_state["recovery_session_set"]   = True
        except Exception as e:
            st.error(f"Recovery link is invalid or expired. Please request a new one. ({e})")
            return

    with st.form("reset_password_form"):
        new_password = st.text_input("New Password", type="password")
        confirm      = st.text_input("Confirm Password", type="password")
        if st.form_submit_button("Set New Password"):
            if not new_password:
                st.warning("Please enter a password.")
            elif new_password != confirm:
                st.error("Passwords do not match.")
            else:
                try:
                    get_supabase().auth.set_session(
                        st.session_state["recovery_access_token"],
                        st.session_state["recovery_refresh_token"],
                    )
                    get_supabase().auth.update_user({"password": new_password})
                    for k in ["recovery_access_token", "recovery_refresh_token", "recovery_session_set"]:
                        st.session_state.pop(k, None)
                    st.success("Password updated. You can now log in.")
                except Exception as e:
                    st.error(f"Failed to update password: {e}")


# ── Database helpers ───────────────────────────────────────────────────────────
def db():
    return get_supabase()


def get_teams() -> list:
    try:
        r = db().table("teams").select("id, name").eq("user_id", st.session_state["user_id"]).order("created_at").execute()
        return r.data or []
    except Exception:
        return []


def create_team(name: str):
    db().table("teams").insert({
        "user_id": st.session_state["user_id"],
        "name":    name,
    }).execute()


def update_team(team_id: str, name: str):
    db().table("teams").update({"name": name}).eq("id", team_id).execute()


def delete_team(team_id: str):
    db().table("teams").delete().eq("id", team_id).execute()


# ── Release helpers ────────────────────────────────────────────────────────────
def get_releases(team_id: str) -> list:
    try:
        r = db().table("releases").select("id, name").eq("team_id", team_id).order("created_at").execute()
        return r.data or []
    except Exception:
        return []


def create_release(team_id: str, name: str) -> str:
    """Create a release and its default base scenario. Returns the release id."""
    r   = db().table("releases").insert({"team_id": team_id, "name": name}).execute()
    rid = r.data[0]["id"]
    db().table("scenarios").insert({"release_id": rid, "name": "Base", "sort_order": 0}).execute()
    return rid


def update_release(release_id: str, name: str):
    db().table("releases").update({"name": name}).eq("id", release_id).execute()


def delete_release(release_id: str):
    db().table("releases").delete().eq("id", release_id).execute()


def get_scenario(release_id: str) -> dict:
    """Get the base scenario for a release."""
    try:
        r = db().table("scenarios").select("*").eq("release_id", release_id).order("sort_order").limit(1).execute()
        return r.data[0] if r.data else {}
    except Exception:
        return {}


def save_scenario(release_id: str, data: dict):
    """Update the base scenario for a release."""
    try:
        r = db().table("scenarios").select("id").eq("release_id", release_id).order("sort_order").limit(1).execute()
        if r.data:
            db().table("scenarios").update(data).eq("id", r.data[0]["id"]).execute()
    except Exception:
        pass


# ── Pages ──────────────────────────────────────────────────────────────────────
def page_login():
    st.title("Release Estimation")
    st.write("Enhancing release estimates with confidence levels.")
    st.divider()

    tab_login, tab_signup = st.tabs(["Log In", "Sign Up"])

    with tab_login:
        with st.form("login_form"):
            email    = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log In")
        if submitted:
            if not email or not password:
                st.warning("Please enter your email and password.")
            else:
                err = do_login(email, password)
                if err:
                    st.error(f"Login failed: {err}")
                else:
                    st.rerun()
        st.markdown("---")
        if st.button("Forgot your password?"):
            st.session_state["show_forgot"] = True
            st.rerun()
        if st.session_state.get("show_forgot"):
            with st.form("forgot_form"):
                reset_email = st.text_input("Enter your email")
                send = st.form_submit_button("Send Reset Email")
            if send:
                if reset_email:
                    try:
                        get_supabase().auth.reset_password_email(
                            reset_email,
                            {"redirect_to": f"{st.secrets['app_url']}?type=recovery"},
                        )
                        st.success("Reset email sent. Check your inbox.")
                    except Exception as e:
                        st.error(f"Failed to send reset email: {e}")
                else:
                    st.warning("Please enter your email.")

    with tab_signup:
        with st.form("signup_form"):
            new_email    = st.text_input("Email", key="su_email")
            new_password = st.text_input("Password", type="password", key="su_pass")
            confirm      = st.text_input("Confirm Password", type="password", key="su_confirm")
            submitted_su = st.form_submit_button("Sign Up")
        if submitted_su:
            if not new_email or not new_password:
                st.warning("Please fill in all fields.")
            elif new_password != confirm:
                st.error("Passwords do not match.")
            else:
                err, msg = do_signup(new_email, new_password)
                if err:
                    st.error(f"Sign up failed: {err}")
                else:
                    st.success(msg)


def page_teams():
    st.title("Your Teams")

    if st.session_state.pop("team_created_success", None):
        st.success(st.session_state.pop("team_created_name", "Team created."))
    if st.session_state.pop("team_deleted_success", None):
        st.success(st.session_state.pop("team_deleted_name", "Team deleted."))

    teams = get_teams()

    with st.expander("How to use this page"):
        st.markdown("""
- Each team has its own releases and estimation scenarios.
- Click **Open** to view and manage a team's estimations.
- Use **Add New Team** to create a team for each group you want to track separately.
- Use **Rename** or **Delete** to manage existing teams.
        """)

    with st.expander("Add New Team", expanded=(len(teams) == 0)):
        with st.form("add_team"):
            name = st.text_input("Team Name")
            if st.form_submit_button("Add Team"):
                if name.strip():
                    create_team(name.strip())
                    st.session_state["team_created_success"] = True
                    st.session_state["team_created_name"]    = f"Team '{name.strip()}' created."
                    st.rerun()
                else:
                    st.warning("Please enter a team name.")

    if not teams:
        st.info("No teams yet. Add one above to get started.")
        return

    st.divider()

    for team in teams:
        col_name, col_open, col_rename, col_delete = st.columns([5, 2, 2, 2])
        col_name.write(f"**{team['name']}**")

        if col_open.button("Open", key=f"open_{team['id']}"):
            st.session_state["current_team_id"]   = team["id"]
            st.session_state["current_team_name"] = team["name"]
            st.session_state["page"]              = "estimation"
            st.rerun()

        if col_rename.button("Rename", key=f"rename_{team['id']}"):
            st.session_state[f"renaming_{team['id']}"] = True
            st.rerun()

        if col_delete.button("Delete", key=f"delete_{team['id']}"):
            st.session_state[f"confirm_delete_{team['id']}"] = True
            st.rerun()

        if st.session_state.get(f"renaming_{team['id']}"):
            with st.form(f"rename_form_{team['id']}"):
                new_name = st.text_input("New name", value=team["name"])
                c1, c2 = st.columns(2)
                save   = c1.form_submit_button("Save")
                cancel = c2.form_submit_button("Cancel")
            if save:
                if new_name.strip():
                    update_team(team["id"], new_name.strip())
                    if st.session_state.get("current_team_id") == team["id"]:
                        st.session_state["current_team_name"] = new_name.strip()
                    st.session_state.pop(f"renaming_{team['id']}", None)
                    st.session_state["team_renamed_success"] = True
                    st.rerun()
                else:
                    st.warning("Name cannot be empty.")
            if cancel:
                st.session_state.pop(f"renaming_{team['id']}", None)
                st.rerun()

        if st.session_state.get(f"confirm_delete_{team['id']}"):
            st.warning(f"Delete **{team['name']}**? This cannot be undone.")
            c1, c2 = st.columns(2)
            if c1.button("Yes, delete", key=f"yes_del_{team['id']}"):
                delete_team(team["id"])
                st.session_state.pop(f"confirm_delete_{team['id']}", None)
                if st.session_state.get("current_team_id") == team["id"]:
                    st.session_state.pop("current_team_id", None)
                    st.session_state.pop("current_team_name", None)
                    st.session_state["page"] = "teams"
                st.session_state["team_deleted_success"] = True
                st.session_state["team_deleted_name"]    = f"Team '{team['name']}' deleted."
                st.rerun()
            if c2.button("Cancel", key=f"no_del_{team['id']}"):
                st.session_state.pop(f"confirm_delete_{team['id']}", None)
                st.rerun()


def page_estimation():
    team_id   = st.session_state["current_team_id"]
    team_name = st.session_state.get("current_team_name", "Team")
    st.title(f"Estimation — {team_name}")

    # Confirmation messages
    if st.session_state.pop("release_saved", False):
        st.success("Changes saved.")
    if st.session_state.pop("release_created", False):
        st.success(st.session_state.pop("release_created_name", "Release created."))
    if st.session_state.pop("release_deleted", False):
        st.success(st.session_state.pop("release_deleted_name", "Release deleted."))
    if st.session_state.pop("release_renamed", False):
        st.success(st.session_state.pop("release_renamed_name", "Release renamed."))

    with st.expander("How to use this page"):
        st.markdown("""
- Create a release for each upcoming delivery you want to estimate.
- Enter your team's velocity estimates (worst case, most likely, best case) and total backlog size.
- The projected date updates in real time as you adjust inputs — no need to click Calculate.
- Use **Desired Confidence** to see how the date changes at different confidence levels.
- Click **Save Changes** to store your inputs.
        """)

    # ── Release selector ──────────────────────────────────────────────────────
    releases = get_releases(team_id)

    col_release, col_new = st.columns([5, 1])
    with col_new:
        if st.button("+ New Release", use_container_width=True):
            st.session_state[f"creating_release_{team_id}"] = True

    with col_release:
        if releases:
            release_names = [r["name"] for r in releases]
            current_rid   = st.session_state.get(f"current_release_{team_id}")
            current_idx   = next((i for i, r in enumerate(releases) if r["id"] == current_rid), 0)
            sel_idx = st.selectbox(
                "Release",
                range(len(release_names)),
                format_func=lambda i: release_names[i],
                index=current_idx,
            )
            selected_release = releases[sel_idx]
            if selected_release["id"] != current_rid:
                st.session_state[f"current_release_{team_id}"] = selected_release["id"]
                st.rerun()
        else:
            st.info("No releases yet. Click **+ New Release** to get started.")

    # Create release form
    if st.session_state.get(f"creating_release_{team_id}"):
        with st.form(f"new_release_{team_id}"):
            rname = st.text_input("Release Name", placeholder="e.g. v1.0, Q3 Release")
            c1, c2 = st.columns(2)
            submitted = c1.form_submit_button("Create")
            cancelled = c2.form_submit_button("Cancel")
        if submitted:
            if rname.strip():
                rid = create_release(team_id, rname.strip())
                st.session_state[f"current_release_{team_id}"] = rid
                st.session_state.pop(f"creating_release_{team_id}", None)
                st.session_state["release_created"]      = True
                st.session_state["release_created_name"] = f"Release '{rname.strip()}' created."
                st.rerun()
            else:
                st.warning("Please enter a release name.")
        if cancelled:
            st.session_state.pop(f"creating_release_{team_id}", None)
            st.rerun()

    if not releases:
        return

    release    = selected_release
    release_id = release["id"]
    scenario   = get_scenario(release_id)

    # Rename / delete release
    with st.expander("Rename or Delete this Release"):
        c1, c2 = st.columns(2)
        with c1:
            new_rname = st.text_input("New Name", value=release["name"], key=f"rname_{release_id}")
            if st.button("Rename", key=f"do_rename_{release_id}"):
                if new_rname.strip():
                    update_release(release_id, new_rname.strip())
                    st.session_state["release_renamed"]      = True
                    st.session_state["release_renamed_name"] = f"Renamed to '{new_rname.strip()}'."
                    st.rerun()
                else:
                    st.warning("Name cannot be empty.")
        with c2:
            st.markdown(" ")
            if st.button("Delete this Release", key=f"del_r_{release_id}"):
                st.session_state[f"confirm_del_r_{release_id}"] = True
                st.rerun()
        if st.session_state.get(f"confirm_del_r_{release_id}"):
            st.warning(f"Delete **{release['name']}**? This cannot be undone.")
            ca, cb = st.columns(2)
            if ca.button("Yes, delete", key=f"yes_del_r_{release_id}"):
                delete_release(release_id)
                st.session_state.pop(f"current_release_{team_id}", None)
                st.session_state["release_deleted"]      = True
                st.session_state["release_deleted_name"] = f"Release '{release['name']}' deleted."
                st.rerun()
            if cb.button("Cancel", key=f"no_del_r_{release_id}"):
                st.session_state.pop(f"confirm_del_r_{release_id}", None)
                st.rerun()

    st.divider()

    # ── Inputs ────────────────────────────────────────────────────────────────
    st.subheader("Estimation Inputs")

    col1, col2, col3 = st.columns(3)
    with col1:
        sprint_weeks = st.number_input(
            "Sprint Length (weeks)", min_value=1, max_value=8,
            value=int(scenario.get("sprint_weeks") or 2),
            step=1, key=f"sw_{release_id}",
        )
    with col2:
        backlog = st.number_input(
            "Total Backlog (points)", min_value=1.0,
            value=float(scenario.get("backlog") or 1.0),
            step=1.0, key=f"bl_{release_id}",
        )
    with col3:
        raw_date   = scenario.get("start_date")
        start_date = st.date_input(
            "Release Start Date",
            value=datetime.strptime(raw_date, "%Y-%m-%d").date() if isinstance(raw_date, str) else date_type.today(),
            key=f"sd_{release_id}",
        )

    st.markdown("**Velocity Estimate (points per sprint)**")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        most_likely = st.number_input(
            "Most Likely", min_value=0.1,
            value=float(scenario.get("most_likely") or 18.0),
            step=0.5, key=f"ml_{release_id}",
        )
    with col2:
        worst_case = st.number_input(
            "Worst Case", min_value=0.1,
            value=float(scenario.get("worst_case") or 10.0),
            step=0.5, key=f"wc_{release_id}",
        )
    with col3:
        best_case = st.number_input(
            "Best Case", min_value=0.1,
            value=float(scenario.get("best_case") or 28.0),
            step=0.5, key=f"bc_{release_id}",
        )
    with col4:
        conf_idx = CONFIDENCE_LABELS.index(scenario.get("confidence_label") or "Medium confidence")
        confidence_label = st.selectbox(
            "Confidence in Most Likely",
            CONFIDENCE_LABELS,
            index=conf_idx,
            key=f"cl_{release_id}",
        )

    desired_pct = st.slider(
        "Desired Confidence",
        min_value=1, max_value=99,
        value=int(float(scenario.get("desired_confidence") or 0.80) * 100),
        format="%d%%",
        key=f"dc_{release_id}",
    )
    desired_confidence = desired_pct / 100

    with st.expander("Advanced Options"):
        rounding = st.number_input(
            "Rounding Decimal (0.1–0.9) — controls when fractional sprints round up",
            min_value=0.1, max_value=0.9,
            value=float(scenario.get("rounding_decimal") or 0.3),
            step=0.1, format="%.1f",
            key=f"rd_{release_id}",
        )
        sdo_val = st.number_input(
            "Standard Deviation Override (leave at 0 to use the calculated value)",
            min_value=0.0,
            value=float(scenario.get("std_dev_override") or 0.0),
            step=0.1, format="%.1f",
            key=f"sdo_{release_id}",
        )
        std_dev_override = sdo_val if sdo_val > 0 else None
        extra_days = st.number_input(
            "Extra Calendar Days (e.g. holidays, planned team events)",
            min_value=0,
            value=int(scenario.get("extra_days") or 0),
            step=1, key=f"ed_{release_id}",
        )

    if st.button("Save Changes", key=f"save_{release_id}"):
        save_scenario(release_id, {
            "sprint_weeks":       sprint_weeks,
            "backlog":            backlog,
            "start_date":         str(start_date),
            "most_likely":        most_likely,
            "worst_case":         worst_case,
            "best_case":          best_case,
            "confidence_label":   confidence_label,
            "desired_confidence": desired_confidence,
            "rounding_decimal":   rounding,
            "std_dev_override":   std_dev_override,
            "extra_days":         extra_days,
        })
        st.session_state["release_saved"] = True
        st.rerun()

    # ── Validation ────────────────────────────────────────────────────────────
    st.divider()

    if worst_case >= most_likely:
        st.warning("Worst case must be less than most likely.")
        return
    if best_case <= most_likely:
        st.warning("Best case must be greater than most likely.")
        return

    # ── Results ───────────────────────────────────────────────────────────────
    result = compute_estimate(
        most_likely=most_likely,
        worst_case=worst_case,
        best_case=best_case,
        confidence_label=confidence_label,
        desired_confidence=desired_confidence,
        backlog=backlog,
        sprint_weeks=sprint_weeks,
        start_date=start_date,
        rounding_decimal=rounding,
        std_dev_override=std_dev_override,
        extra_days=extra_days,
    )

    # Contextual warnings — only shown when triggered
    if not result["bell_ok"]:
        st.warning(
            "Your velocity estimates are highly asymmetric — the gap between your most likely "
            "value and worst/best case is uneven. Consider rebalancing for more reliable results."
        )
    if desired_confidence < 0.50:
        st.warning(
            f"You have selected {desired_confidence:.0%} confidence. At levels below 50%, "
            "unknown factors in software delivery frequently cause estimates to run over."
        )

    st.subheader("Results")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Projected Date",  result["projected_date"].strftime("%b %d, %Y"))
    c2.metric("Confidence",      f"{desired_confidence:.0%}")
    c3.metric("Sprints Needed",  result["sprints_rounded"])
    c4.metric("Business Weeks",  result["business_weeks"])

    st.markdown(
        f"At **{desired_confidence:.0%} confidence**, your team will complete "
        f"**{release['name']}** by **{result['projected_date'].strftime('%B %d, %Y')}**. "
        f"This assumes completing at least **{result['guaranteed_min']:.1f} points per sprint** "
        f"across **{result['sprints_rounded']} sprints** ({result['business_weeks']} business weeks)."
    )

    with st.expander("Calculation Details"):
        col1, col2 = st.columns(2)
        col1.write(f"PERT weighted mean velocity: {result['pert_mean']:.1f} pts/sprint")
        col1.write(f"Statistical std deviation: {result['std_dev']:.2f}")
        col1.write(f"Guaranteed minimum velocity: {result['guaranteed_min']:.2f} pts/sprint")
        col2.write(f"Raw sprints needed: {result['sprints_raw']:.2f}")
        col2.write(f"Rounded sprints: {result['sprints_rounded']}")
        col2.write(f"Total calendar days: {result['total_days']}")


def page_configuration():
    team_name = st.session_state.get("current_team_name", "Team")
    st.title(f"Configuration — {team_name}")
    st.info("Configuration coming in a future build layer.")


# ── Sidebar ────────────────────────────────────────────────────────────────────
def show_sidebar():
    with st.sidebar:
        st.markdown("### Release Estimation")
        st.markdown("---")

        if st.button("Manage Teams", use_container_width=True):
            st.session_state["page"] = "teams"
            st.rerun()

        # Team selector dropdown
        teams = get_teams()
        if teams:
            team_names = [t["name"] for t in teams]
            current_id = st.session_state.get("current_team_id")
            current_idx = next((i for i, t in enumerate(teams) if t["id"] == current_id), 0)

            selected_idx = st.selectbox(
                "Team",
                range(len(team_names)),
                format_func=lambda i: team_names[i],
                index=current_idx,
            )
            selected_team = teams[selected_idx]

            if selected_team["id"] != current_id:
                st.session_state["current_team_id"]   = selected_team["id"]
                st.session_state["current_team_name"] = selected_team["name"]
                st.session_state["page"]              = "estimation"
                st.rerun()

            if st.session_state.get("current_team_id"):
                if st.button("Estimation", use_container_width=True):
                    st.session_state["page"] = "estimation"
                    st.rerun()
                if st.button("Configuration", use_container_width=True):
                    st.session_state["page"] = "configuration"
                    st.rerun()

        st.markdown("---")
        if st.button("Log Out", use_container_width=True):
            do_logout()
            st.rerun()


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    params = st.query_params

    # Handle password recovery
    if params.get("type") == "recovery":
        if "token_hash" in params:
            handle_password_recovery(token_hash=params["token_hash"])
            return
        if "code" in params:
            handle_password_recovery(code=params["code"])
            return
        if "access_token" in params:
            handle_password_recovery(
                access_token=params["access_token"],
                refresh_token=params.get("refresh_token", ""),
            )
            return

    if not is_authenticated():
        sid = params.get("sid")
        if sid:
            if not load_server_session(sid):
                try:
                    del st.query_params["sid"]
                except Exception:
                    pass
                page_login()
                return
        elif not restore_session():
            page_login()
            return

    try:
        if not restore_session():
            clear_session()
            page_login()
            return
        update_server_session()
        show_sidebar()

        page    = st.session_state.get("page", "teams")
        team_id = st.session_state.get("current_team_id")

        if page == "teams":
            page_teams()
        elif page in ("estimation", "configuration") and not team_id:
            st.warning("Please select a team first.")
            page_teams()
        elif page == "estimation":
            page_estimation()
        elif page == "configuration":
            page_configuration()
        else:
            page_teams()

    except Exception as e:
        if is_auth_error(e):
            clear_session()
            st.error("Your session has expired. Please log in again.")
            page_login()
        else:
            raise


main()
