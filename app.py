import requests
import streamlit as st

st.title("Personalized Weekly Diet Chart Generator 🥗")
st.write("Hello! Fill in your details to get a diet chart.")


API_KEY = "AIzaSyCbpk3YFYFqcTktGBvV6tVfeQlEwT9P8w0"
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={API_KEY}"

st.header("User Information")

# General Info
height = st.number_input("Height (cm)", min_value=50, max_value=250, step=1)
weight = st.number_input("Weight (kg)", min_value=20, max_value=300, step=1)
target_weight = st.number_input("Target Weight (kg)", min_value=20, max_value=300, step=1)
age = st.number_input("Age", min_value=1, max_value=120, step=1)
gender = st.selectbox("Gender", ["Male", "Female", "Other"])
diet_pref = st.selectbox("Diet Preference", ["Vegetarian", "Non-Vegetarian"])

# Non-Vegetarian options
chicken_days = 0
eggs_days = 0
other_meat_days = 0
if diet_pref == "Non-Vegetarian":
    chicken = st.radio("Do you eat chicken?", ["Yes", "No"])
    if chicken == "Yes":
        chicken_days = st.slider("How many days per week can you afford chicken?", 0, 7)
    eggs = st.radio("Do you eat eggs?", ["Yes", "No"])
    if eggs == "Yes":
        eggs_days = st.slider("How many days per week can you afford eggs?", 0, 7)
    other_meat = st.radio("Do you eat other meats?", ["Yes", "No"])
    if other_meat == "Yes":
        other_meat_days = st.slider("How many days per week can you afford other meats?", 0, 7)

# Vegetarian options
paneer_days = 0
alternative_proteins = ""
if diet_pref == "Vegetarian":
    paneer = st.radio("Do you eat paneer?", ["Yes", "No"])
    if paneer == "Yes":
        paneer_days = st.slider("How many days per week can you afford paneer?", 0, 7)
    else:
        alternative_proteins = "Tofu, Lentils, Soy Chunks, Chickpeas"

# Submit button
if st.button("Generate Diet Chart"):
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
        f"{user_input}\n"
        "Include all meals with portion sizes and detailed macronutrients (protein, carbs, fat)"
        " and micronutrients (vitamins, minerals). Format it clearly for display on a website."
    )

    headers = {"Content-Type": "application/json"}

    data = {
        "contents": [
            {
                "parts": [{"text": prompt_text}]
            }
        ]
    }

    try:
        response = requests.post(API_URL, headers=headers, json=data)
        response.raise_for_status()
        result = response.json()

        # Safe parsing of response
        candidates = result.get("candidates")
        if isinstance(candidates, list) and len(candidates) > 0:
            content = candidates[0]
            if isinstance(content, dict):
                parts = content.get("content", {}).get("parts")
                if isinstance(parts, list) and len(parts) > 0:
                    diet_chart = parts[0].get("text", "No diet chart generated.")
                else:
                    diet_chart = "No parts found in content."
            else:
                diet_chart = "No content found in candidate."
        else:
            diet_chart = "No candidates found in response."

        st.subheader("Your Weekly Diet Chart")
        st.markdown(diet_chart)

    except Exception as e:
        st.error(f"Error generating diet chart: {e}")
