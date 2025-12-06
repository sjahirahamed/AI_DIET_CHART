# D:\api_fit_diet\app.py
"""
Streamlit app: Personalized Weekly Diet Chart + feedback + developer-only accuracy dashboard.

Place secrets in D:\api_fit_diet\secrets.toml:

GENAI_API_KEY = "YOUR_NEW_GENERATED_KEY"
DEVELOPER_TOKEN = "YourDevPasswordHere"

Don't share these values publicly.
"""

import os
import time
import json
import csv
import hashlib
import random
from datetime import datetime
from typing import Tuple

import toml
import requests
import pandas as pd
import streamlit as st

# ---------------- page config ----------------
st.set_page_config(page_title="Diet Chart Generator", layout="centered")
st.title("Personalized Weekly Diet Chart Generator 🥗")
st.write("Generate a weekly diet chart and collect feedback. Developer-only metrics are protected by a token.")

# ---------------- paths & secrets ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SECRETS_PATH = os.path.join(BASE_DIR, "secrets.toml")
FEEDBACK_CSV = os.path.join(BASE_DIR, "feedback_log.csv")

# load secrets from D:\api_fit_diet\secrets.toml
API_KEY = None
DEVELOPER_TOKEN = None
if os.path.exists(SECRETS_PATH):
    try:
        _secrets = toml.load(SECRETS_PATH)
        API_KEY = _secrets.get("GENAI_API_KEY")
        DEVELOPER_TOKEN = _secrets.get("DEVELOPER_TOKEN")
    except Exception as e:
        st.error(f"Error reading secrets.toml: {e}")
else:
    st.info(f"Create secrets.toml in the same folder as app.py: {SECRETS_PATH}")

# API availability flag (app can still show metrics without API)
API_AVAILABLE = bool(API_KEY)
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={API_KEY}" if API_AVAILABLE else None

# ---------------- feedback CSV helpers ----------------
def ensure_feedback_csv():
    header = [
        "timestamp", "user_id", "rating", "accurate_auto", "comment",
        "user_context", "api_success", "latency_s", "chart_snippet"
    ]
    if not os.path.exists(FEEDBACK_CSV):
        with open(FEEDBACK_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()

def append_feedback_row(row: dict):
    # ensures order of keys consistent with header
    header = [
        "timestamp", "user_id", "rating", "accurate_auto", "comment",
        "user_context", "api_success", "latency_s", "chart_snippet"
    ]
    with open(FEEDBACK_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writerow(row)

def load_feedback_df() -> pd.DataFrame:
    if os.path.exists(FEEDBACK_CSV):
        df = pd.read_csv(FEEDBACK_CSV, dtype={"user_id": str})
        return df
    else:
        # return empty with expected columns
        return pd.DataFrame(columns=[
            "timestamp","user_id","rating","accurate_auto","comment","user_context","api_success","latency_s","chart_snippet"
        ])

ensure_feedback_csv()

# ---------------- helper: parse api response ----------------
def extract_text_from_response(result_json):
    # Tries multiple formats that Gemini responses might use
    try:
        candidates = result_json.get("candidates")
        if isinstance(candidates, list) and candidates:
            cand0 = candidates[0]
            parts = cand0.get("content", {}).get("parts")
            if isinstance(parts, list) and parts:
                return parts[0].get("text")
    except Exception:
        pass
    try:
        output = result_json.get("output")
        if isinstance(output, list) and output:
            content = output[0].get("content")
            if isinstance(content, list) and content:
                return content[0].get("text")
    except Exception:
        pass
    # fallback keys
    return result_json.get("text") or result_json.get("response") or ""

# ---------------- calculate_accuracy (must be defined before used) ----------------
def calculate_accuracy():
    """
    Reads feedback_log.csv and returns:
      - overall_accuracy
      - total_calls
      - total_accurate
      - per_user (DataFrame)
      - two_three_avg (avg accuracy for users with 2-3 calls)
    """
    df = load_feedback_df()
    if df is None or df.empty:
        return None

    # ensure numeric columns
    df["rating"] = pd.to_numeric(df.get("rating", 0), errors="coerce").fillna(0)
    df["accurate_auto"] = pd.to_numeric(df.get("accurate_auto", 0), errors="coerce").fillna(0)

    total_calls = int(len(df))
    total_accurate = int(df["accurate_auto"].sum())
    overall_accuracy = (total_accurate / total_calls) * 100 if total_calls > 0 else 0.0

    grouped = (
        df.groupby("user_id")
          .agg(calls=("rating", "count"),
               accurate=("accurate_auto", "sum"),
               avg_rating=("rating", "mean"))
          .reset_index()
    )

    # compute accuracy pct safely
    grouped["accuracy_pct"] = grouped.apply(
        lambda row: (row["accurate"] / row["calls"]) * 100 if row["calls"] > 0 else 0.0,
        axis=1
    )

    two_three = grouped[grouped["calls"].between(2, 3)]
    avg_two_three_accuracy = two_three["accuracy_pct"].mean() if not two_three.empty else 0.0

    return {
        "overall_accuracy": overall_accuracy,
        "total_calls": total_calls,
        "total_accurate": total_accurate,
        "per_user": grouped,
        "two_three_avg": avg_two_three_accuracy
    }

# =====================================================
# INFINITE-RETRY GEMINI CALL (used only for diet chart generation)
# =====================================================
# This function will wait indefinitely when 429s are returned, honoring Retry-After header when present.
def call_gemini_infinite(prompt: str, timeout: int = 30) -> Tuple[bool, str, float]:
    """
    Infinite retry version used only for diet chart generation (Option A).
    Returns (success, text_or_error, elapsed_seconds).
    """
    if not API_AVAILABLE or not API_URL:
        return False, "API key not configured", 0.0

    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    attempt = 0
    start_overall = time.time()

    while True:  # infinite retry loop
        attempt += 1
        try:
            resp = requests.post(API_URL, headers=headers, json=payload, timeout=timeout)

            # handle 429
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = float(retry_after)
                    except Exception:
                        wait = None
                else:
                    wait = None

                if wait is None:
                    # exponential base with jitter
                    base = 1.5 * (2 ** min(attempt, 10))
                    jitter = random.uniform(0, base * 0.3)
                    wait = min(base + jitter, 3600)  # cap to 1 hour so we don't overflow
                st.warning(f"⚠ API rate-limited (429). Waiting {int(wait)}s (attempt {attempt})…")
                time.sleep(wait)
                continue

            # other HTTP errors
            resp.raise_for_status()

            # success
            try:
                result_json = resp.json()
            except Exception:
                result_json = {}

            text = extract_text_from_response(result_json) or ""
            elapsed = time.time() - start_overall
            return True, text, elapsed

        except requests.exceptions.RequestException as e:
            # network/timeout - retry with backoff
            wait = min(3600, (2 ** min(attempt, 10)) + random.uniform(0, 1))
            st.warning(f"⚠ Network error: {e}. Retrying in {int(wait)}s (attempt {attempt})…")
            time.sleep(wait)
            continue

        except Exception as e:
            # any other unexpected issues: keep retrying after backoff
            wait = min(3600, (2 ** min(attempt, 10)))
            st.warning(f"⚠ Unexpected error: {e}. Retrying in {int(wait)}s (attempt {attempt})…")
            time.sleep(wait)
            continue

# =====================================================
# UI: user inputs
# =====================================================
st.header("User Information")

height = st.number_input("Height (cm)", min_value=50, max_value=250, step=1, value=170)
weight = st.number_input("Weight (kg)", min_value=20, max_value=300, step=1, value=70)
target_weight = st.number_input("Target Weight (kg)", min_value=20, max_value=300, step=1, value=68)
age = st.number_input("Age", min_value=1, max_value=120, step=1, value=25)
gender = st.selectbox("Gender", ["Male", "Female", "Other"])
diet_pref = st.selectbox("Diet Preference", ["Vegetarian", "Non-Vegetarian"])

# diet-specific options
chicken_days = eggs_days = other_meat_days = 0
paneer_days = 0
alternative_proteins = ""
if diet_pref == "Non-Vegetarian":
    if st.radio("Do you eat chicken?", ["Yes", "No"]) == "Yes":
        chicken_days = st.slider("Chicken days/week", 0, 7, 2)
    if st.radio("Do you eat eggs?", ["Yes", "No"]) == "Yes":
        eggs_days = st.slider("Egg days/week", 0, 7, 3)
    if st.radio("Other meats?", ["Yes", "No"]) == "Yes":
        other_meat_days = st.slider("Other meat days/week", 0, 7, 1)
else:
    if st.radio("Do you eat paneer?", ["Yes", "No"]) == "Yes":
        paneer_days = st.slider("Paneer days/week", 0, 7, 2)
    else:
        alternative_proteins = "Tofu, Lentils, Soy Chunks, Chickpeas"

# session uid fallback if user doesn't provide user_id
if "session_uid" not in st.session_state:
    st.session_state.session_uid = hashlib.sha1(str(time.time()).encode()).hexdigest()[:8]

st.markdown("**Optional:** enter a user id (email/username) to group multiple ratings.")
user_id_input = st.text_input("User ID (optional)", value="")
current_user_id = user_id_input.strip() if user_id_input.strip() else st.session_state.session_uid
user_context = f"h={height},w={weight},tw={target_weight},age={age},diet={diet_pref}"

# ---------------- generate diet chart ----------------
st.markdown("---")
st.subheader("Generate Diet Chart")
if not API_AVAILABLE:
    st.warning("API key not configured. Diet chart generation is disabled. Add GENAI_API_KEY to secrets.toml to enable.")
generate_clicked = st.button("Generate Diet Chart")

if generate_clicked:
    if not API_AVAILABLE:
        st.error("Cannot generate: API key not configured.")
    else:
        user_input = {
            "height": height,
            "weight": weight,
            "target_weight": target_weight,
            "age": age,
            "gender": gender,
            "diet_pref": diet_pref,
            "chicken_days": chicken_days,
            "eggs_days": eggs_days,
            "other_meat_days": other_meat_days,
            "paneer_days": paneer_days,
            "alternative_proteins": alternative_proteins,
        }
        prompt_text = (
            "Create a detailed weekly diet chart (Monday to Sunday) for the following user: "
            f"{json.dumps(user_input)}\n"
            "Include meals with portion sizes and macronutrients (protein, carbs, fat) and micronutrients. "
            "Format for display on a website."
        )
        with st.spinner("Generating diet chart (infinite-retry if rate-limited)..."):
            success, result_text, latency = call_gemini_infinite(prompt_text)

        st.session_state.last_api_success = success
        st.session_state.last_latency = latency
        st.session_state.last_chart = result_text if success else ""
        st.session_state.generated_at = datetime.utcnow().isoformat()

        if success:
            st.subheader("Your Weekly Diet Chart")
            st.markdown(result_text or "No text returned.")
        else:
            st.error(f"API error: {result_text}")

# ---------------- rating UI (limit 3 per user) ----------------
st.markdown("---")
st.header("Rate this output (max 3 ratings per user)")

df_feedback = load_feedback_df()
user_rows = df_feedback[df_feedback["user_id"] == str(current_user_id)] if not df_feedback.empty else pd.DataFrame()
user_rating_count = len(user_rows)

st.write(f"User ID: **{current_user_id}** — Ratings submitted: **{user_rating_count}/3**")

if user_rating_count >= 3:
    st.warning("You have reached the maximum of 3 ratings and cannot submit more.")
else:
    rating = st.radio("How accurate is this chart? (1 = poor, 5 = excellent)", [1,2,3,4,5], index=4, horizontal=True)
    comment = st.text_area("Optional comment (why you rated this way)", max_chars=500)

    if st.button("Submit rating for this call"):
        last_chart = st.session_state.get("last_chart", "")
        last_api_success = st.session_state.get("last_api_success", None)
        last_latency = st.session_state.get("last_latency", None)
        if not last_chart:
            st.error("No generated chart to rate. Please generate a chart first.")
        else:
            ts = datetime.utcnow().isoformat()
            accurate_auto = 1 if int(rating) >= 4 else 0
            row = {
                "timestamp": ts,
                "user_id": str(current_user_id),
                "rating": int(rating),
                "accurate_auto": int(accurate_auto),
                "comment": comment.replace("\n"," ").strip(),
                "user_context": user_context,
                "api_success": bool(last_api_success),
                "latency_s": float(last_latency) if last_latency is not None else "",
                "chart_snippet": (last_chart[:400] + "...") if isinstance(last_chart, str) else ""
            }
            append_feedback_row(row)
            st.success("Rating saved. You may submit up to 3 ratings total.")
            # refresh local dataframe/count
            df_feedback = load_feedback_df()
            user_rows = df_feedback[df_feedback["user_id"] == str(current_user_id)]
            user_rating_count = len(user_rows)

# ---------------- developer-only metrics view ----------------
st.markdown("---")

st.header("Developer (private)")

# initialize auth state
if "developer_authenticated" not in st.session_state:
    st.session_state["developer_authenticated"] = False
    st.session_state["developer_auth_time"] = 0

if not DEVELOPER_TOKEN:
    st.info("Developer token not configured. Add DEVELOPER_TOKEN in secrets.toml to enable developer-only metrics.")
else:
    with st.expander("Developer: authenticate to view private metrics", expanded=False):
        token_input = st.text_input("Developer token (kept local)", value="", type="password", key="dev_token_input")
        cols = st.columns([1,1,2])
        with cols[0]:
            if st.button("Authenticate", key="dev_auth_button"):
                if token_input and token_input == DEVELOPER_TOKEN:
                    st.session_state["developer_authenticated"] = True
                    st.session_state["developer_auth_time"] = time.time()
                    st.success("Authenticated as developer. Private metrics unlocked.")
                else:
                    st.session_state["developer_authenticated"] = False
                    st.error("Authentication failed. Token incorrect.")
        with cols[1]:
            if st.button("Logout", key="dev_logout_button"):
                st.session_state["developer_authenticated"] = False
                st.session_state["developer_auth_time"] = 0
                st.experimental_rerun()
        with cols[2]:
            if st.session_state["developer_authenticated"]:
                st.write(f"✅ Authenticated {int(time.time()-st.session_state['developer_auth_time'])}s ago")

# Only show metrics when authenticated
if st.session_state.get("developer_authenticated", False):
    st.markdown("### 📊 Project Accuracy Summary (Developer view)")
    metrics = calculate_accuracy()

    if not metrics:
        st.info("No ratings yet. Accuracy metrics will appear once data is added.")
    else:
        st.subheader("Overall Accuracy")
        st.write(f"**Accuracy:** {metrics['overall_accuracy']:.2f}%")
        st.write(f"Total Calls: **{metrics['total_calls']}** | Accurate: **{metrics['total_accurate']}**")

        st.subheader("Per-User Accuracy Table")
        st.dataframe(metrics["per_user"], use_container_width=True)

        st.subheader("Users with 2–3 Calls (Average Accuracy)")
        st.write(f"**Average Accuracy:** {metrics['two_three_avg']:.2f}%")

        # developer-only download
        csv_bytes = metrics["per_user"].to_csv(index=False).encode("utf-8")
        st.download_button("Download per-user CSV (private)", data=csv_bytes, file_name="per_user_accuracy.csv", mime="text/csv")
else:
    st.write("Developer metrics are hidden. Authenticate to view them.")

# ---------------- optional admin raw view & reset ----------------
st.markdown("---")
if st.checkbox("Show raw feedback CSV (admin)"):
    df = load_feedback_df()
    st.dataframe(df, use_container_width=True)
    if st.button("Reset feedback CSV (delete all)"):
        try:
            os.remove(FEEDBACK_CSV)
            ensure_feedback_csv()
            st.success("Feedback CSV deleted and recreated.")
            st.experimental_rerun()
        except Exception as e:
            st.error(f"Could not reset feedback CSV: {e}")
