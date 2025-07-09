import streamlit as st
import pandas as pd
import numpy as np
import datetime
import matplotlib.pyplot as plt
import pickle
import sqlite3
import requests
import shap
from statsmodels.tools.sm_exceptions import ConvergenceWarning
import warnings
import os

warnings.simplefilter('ignore', ConvergenceWarning)
shap.initjs()

INDIA_HOLIDAY_API_KEY = st.secrets["INDIA_HOLIDAY_API_KEY"]

# --- Access Control ---
st.set_page_config(page_title="Flowcast", layout="centered")
st.title("Flowcast")

if "logged_in" not in st.session_state or not st.session_state.logged_in:
    st.error("Access Denied. Please log in from the Auth page.")
    st.stop()
elif st.session_state.role != "Retailer":
    st.error("Access Denied. This page is for Users only.")
    st.stop()

# --- Sidebar Logout ---
with st.sidebar:
    st.markdown(f"**👤 {st.session_state.name} ({st.session_state.role})**")
    if st.button("🔓 Logout"):
        for key in ["logged_in", "username", "role", "name", "auth_mode"]:
            st.session_state.pop(key, None)
        st.switch_page("Auth.py")

# --- Dynamic Geocoding ---
def get_city_coordinates(city_name):
    try:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city_name}&count=1&language=en&format=json"
        res = requests.get(geo_url)
        res.raise_for_status()
        results = res.json().get("results")
        if results:
            return results[0]["latitude"], results[0]["longitude"]
        else:
            return None
    except Exception as e:
        st.warning(f"⚠️ Geocoding error: {e}")
        return None

# --- Validate City–State Match ---
def validate_state_matches_city(city_name, selected_state):
    try:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city_name}&count=1&language=en&format=json"
        res = requests.get(geo_url)
        res.raise_for_status()
        results = res.json().get("results")
        if not results:
            return False, "City not found."
        city_info = results[0]
        detected_state = city_info.get("admin1", "").strip().lower()
        return (detected_state == selected_state.strip().lower()), detected_state
    except:
        return False, "Error in city validation."

# --- Open-Meteo 12-day Forecast ---
def get_openmeteo_forecast(city_name):
    coords = get_city_coordinates(city_name)
    if not coords:
        st.warning("⚠️ Could not fetch coordinates for the city.")
        return pd.DataFrame()
    lat, lon = coords
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}&daily=temperature_2m_max,precipitation_sum"
        f"&timezone=auto&forecast_days=12"
    )
    try:
        res = requests.get(url)
        res.raise_for_status()
        data = res.json()
        df = pd.DataFrame({
            "forecast_day": pd.to_datetime(data["daily"]["time"]).date,
            "temp": data["daily"]["temperature_2m_max"],
            "rain": data["daily"]["precipitation_sum"]
        })
        return df
    except Exception as e:
        st.warning(f"⚠️ Open-Meteo API error: {e}")
        return pd.DataFrame()

# --- Indian Govt. + Weekend Holidays ---
def get_combined_holidays(api_key, state="MAHARASHTRA", year="2025", start_date=None, days=12):
    try:
        url = "https://api.data.gov.in/resource/9b6c3d6a-3ab5-4a4a-872b-197b19886a18"
        params = {"api-key": api_key, "format": "json"}
        res = requests.get(url, params=params)
        res.raise_for_status()
        records = res.json().get("records", [])

        holiday_dates = set()
        for record in records:
            rec_state = record.get("state", "").strip().upper()
            rec_year = str(record.get("year", "")).strip()
            if rec_state != state.strip().upper() or rec_year != year:
                continue
            date_str = record.get("date", "")
            try:
                date_obj = datetime.datetime.strptime(date_str, "%d-%m-%Y").date()
                holiday_dates.add(date_obj)
            except:
                continue

        forecast_days = [start_date + datetime.timedelta(days=i) for i in range(days)]
        full_holidays = [1 if d in holiday_dates or d.weekday() in [5, 6] else 0 for d in forecast_days]

        return pd.Series(full_holidays, index=forecast_days)
    except Exception as e:
        st.warning(f"⚠️ Holiday API error: {e}")
        return pd.Series([1 if (start_date + datetime.timedelta(days=i)).weekday() in [5,6] else 0 for i in range(days)],
                         index=[start_date + datetime.timedelta(days=i) for i in range(days)])

# --- UI ---
st.title("📊 Dynamic Price Predictor (Retailer Panel)")
st.sidebar.header("🛠️ Input Conditions")

category = st.sidebar.selectbox("Category", ["Vegetables", "Fruits", "Dairy"])
product = st.sidebar.text_input("Product Name", "Tomato")
city = st.sidebar.text_input("City", "Mumbai")

indian_states = sorted([
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
    "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman and Nicobar Islands", "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry"
])
state_name = st.sidebar.selectbox("Indian State (for holidays)", indian_states)

stock = st.sidebar.slider("Stock", 10, 200, 100)
discount = round(st.sidebar.number_input("Discount (0.0 - 1.0)", 0.0, 1.0, 0.2, step=0.1), 1)
days_to_expiry = round(st.sidebar.number_input("Days to Expiry", 0.0, 30.0, 10.0, step=0.1), 1)
mrp = st.sidebar.number_input("Base Price (MRP ₹)", 10.0, 1000.0, 100.0)
submit = st.sidebar.button("💡 Predict & Save")

# --- Main Logic ---
if submit:
    is_valid, detected_state = validate_state_matches_city(city, state_name)
    if not is_valid:
        st.error(f"❌ City–State mismatch: '{city}' is in '{detected_state.title()}', not '{state_name}'. Please correct it.")
        st.stop()

    try:
        start_day = datetime.date.today()
        weather_df = get_openmeteo_forecast(city)
        holiday_series = get_combined_holidays(
            api_key=INDIA_HOLIDAY_API_KEY,
            state=state_name,
            year=str(start_day.year),
            start_date=start_day,
            days=12
        )

        if len(weather_df) < 12 or len(holiday_series) < 12:
            st.error("❌ Insufficient forecast data.")
            st.stop()

        weather_df["forecast_day"] = pd.date_range(datetime.date.today(), periods=12)

        input_df = pd.DataFrame({
            "stock_level": [stock] * 12,
            "discount": [discount] * 12,
            "days_to_expiry": [max(days_to_expiry - i, 0) for i in range(12)],
            "temperature": weather_df["temp"].values,
            "rain": weather_df["rain"].values,
            "holiday": holiday_series.values,
            "forecast_day": weather_df["forecast_day"].values,
            "mrp": [mrp] * 12
        })

        with open("model.pkl", "rb") as f:
            model = pickle.load(f)

        input_df["stock_expiry_ratio"] = input_df["stock_level"] / (input_df["days_to_expiry"] + 1)
        input_df["rain_temp_interaction"] = input_df["rain"] * input_df["temperature"]

        features = ["stock_level", "holiday", "rain", "temperature", "days_to_expiry",
                    "stock_expiry_ratio", "rain_temp_interaction"]

        predicted_multipliers = model.predict(input_df[features])
        predicted_price = np.minimum(predicted_multipliers * input_df["mrp"], input_df["mrp"])
        min_price = 0.60 * input_df["mrp"]

        expiry_day = int(days_to_expiry)
        valid_prices = np.maximum(predicted_price[:expiry_day], min_price[:expiry_day])
        zero_prices = np.zeros(12 - expiry_day)
        input_df["forecasted_price"] = np.concatenate([valid_prices, zero_prices])

        input_df["category"] = category
        input_df["product"] = product

        # SHAP
        explainer = shap.Explainer(model)
        shap_values = explainer(input_df[features])

        st.subheader("🧬 Feature Importance (SHAP)")
        fig, ax = plt.subplots()
        shap.summary_plot(shap_values, input_df[features], plot_type="bar", show=False)
        st.pyplot(plt.gcf())

        st.subheader("🧠 SHAP Breakdown for Day 1")
        fig = shap.plots._waterfall.waterfall_legacy(
            explainer.expected_value, shap_values.values[0], feature_names=features, show=False
        )
        st.pyplot(fig)

        # DB Save
        conn = sqlite3.connect("retail_forecasts.db")
        cursor = conn.cursor()
        cursor.execute("""CREATE TABLE IF NOT EXISTS forecasts (
            category TEXT, product TEXT, forecast_day DATE, forecasted_price REAL,
            stock INTEGER, discount REAL, holiday INTEGER, rain REAL,
            temp REAL, days_to_expiry REAL)""")
        cursor.execute("DELETE FROM forecasts WHERE category = ? AND product = ?", (category, product))
        for _, row in input_df.iterrows():
            cursor.execute("""INSERT INTO forecasts (
                category, product, forecast_day, forecasted_price,
                stock, discount, holiday, rain, temp, days_to_expiry
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                row["category"], row["product"], row["forecast_day"].date(), row["forecasted_price"],
                row["stock_level"], row["discount"], row["holiday"],
                row["rain"], row["temperature"], row["days_to_expiry"]
            ))
        conn.commit()
        conn.close()

        st.success(f"✅ Forecast saved for {product} in {city} ({category})")
        st.line_chart(input_df.set_index("forecast_day")["forecasted_price"])
        # Show next-day forecasted price
        tomorrow = pd.to_datetime(datetime.date.today() + datetime.timedelta(days=1))
        tomorrow_price = input_df.loc[input_df["forecast_day"] == tomorrow, "forecasted_price"].values


        if len(tomorrow_price) > 0:
            st.success(f"📌 Tomorrow's forecasted price for {product} is ₹{tomorrow_price[0]:.2f}")

    except Exception as e:
        st.error(f"❌ Error: {e}")
