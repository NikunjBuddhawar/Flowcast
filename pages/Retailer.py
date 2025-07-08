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
from dotenv import load_dotenv
import os

warnings.simplefilter('ignore', ConvergenceWarning)
shap.initjs()

load_dotenv()
INDIA_HOLIDAY_API_KEY = os.getenv("INDIA_HOLIDAY_API_KEY")


# --- Access Control ---
st.set_page_config(page_title="Flowcast", layout="centered")
st.title("📊 Flowcast")

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
        st.switch_page("auth.py")

# --- Open-Meteo 12-day Forecast ---
def get_openmeteo_forecast(city_name):
    city_coords = {
        "Mumbai": (19.0760, 72.8777),
        "Delhi": (28.6139, 77.2090),
        "Bangalore": (12.9716, 77.5946),
        "New York": (40.7128, -74.0060),
        "Toronto": (43.651070, -79.347015)
    }

    if city_name not in city_coords:
        st.warning("⚠️ Unsupported city for Open-Meteo forecast.")
        return pd.DataFrame()

    lat, lon = city_coords[city_name]
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

        # Add weekends as holidays
        full_holidays = [1 if d in holiday_dates or d.weekday() in [5, 6] else 0 for d in forecast_days]

        st.info(f"📅 Holidays for {state}, {year}:\n" +
                f"{[str(d) for d in forecast_days if d in holiday_dates or d.weekday() in [5,6]]}")

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
city = st.sidebar.selectbox("City", ["Mumbai", "Delhi", "Bangalore", "New York", "Toronto"])
state_name = st.sidebar.selectbox("Indian State (for holidays)", ["Maharashtra", "Delhi", "Karnataka"])
stock = st.sidebar.slider("Stock", 10, 200, 100)
discount = st.sidebar.slider("Discount", 0.0, 1.0, 0.2)

# Expiry Input
if category == "Dairy":
    days_to_expiry = st.sidebar.slider("Days to Expiry", 0.0, 10.0, 3.0)
else:
    days_to_expiry = st.sidebar.slider("Days to Expiry", 10.0, 40.0, 10.0)

mrp = st.sidebar.number_input("Base Price (MRP ₹)", 10.0, 1000.0, 100.0)
submit = st.sidebar.button("💡 Predict & Save")

# --- Main Logic ---
if submit:
    try:
        start_day = datetime.date.today()

        # Weather
        weather_df = get_openmeteo_forecast(city)

        # Holidays
        if city in ["Mumbai", "Delhi", "Bangalore"]:
            holiday_series = get_combined_holidays(
                api_key=INDIA_HOLIDAY_API_KEY,
                state=state_name,
                year=str(start_day.year),
                start_date=start_day,
                days=12
            )
        else:
            holiday_series = pd.Series([0] * 12, index=[start_day + datetime.timedelta(days=i) for i in range(12)])

        if len(weather_df) < 12 or len(holiday_series) < 12:
            st.error("❌ Insufficient forecast data.")
            st.stop()

        weather_df["forecast_day"] = pd.date_range(datetime.date.today(), periods=12)

        # Input DF
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

        # Features
        input_df["stock_expiry_ratio"] = input_df["stock_level"] / (input_df["days_to_expiry"] + 1)
        input_df["rain_temp_interaction"] = input_df["rain"] * input_df["temperature"]

        features = ["stock_level", "holiday", "rain", "temperature", "days_to_expiry",
                    "stock_expiry_ratio", "rain_temp_interaction"]

        predicted_multipliers = model.predict(input_df[features])

        min_multiplier = 0.60
        min_price = min_multiplier * input_df["mrp"]

        if category == "Dairy":
            forecasted = predicted_multipliers * input_df["mrp"]
            forecasted = np.where(input_df["days_to_expiry"] > 0, forecasted, 0.0)
        else:
            forecasted = np.minimum(predicted_multipliers * input_df["mrp"], input_df["mrp"])

        input_df["forecasted_price"] = np.maximum(forecasted, min_price)
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

    except Exception as e:
        st.error(f"❌ Error: {e}")
