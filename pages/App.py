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


warnings.simplefilter('ignore', ConvergenceWarning)
shap.initjs()

# --- Access Control ---
st.set_page_config(page_title="Dynamic Pricing Predictor", layout="centered")
if "logged_in" not in st.session_state or not st.session_state.logged_in:
    st.markdown("## 🚫 Access Denied. Please log in.")
    st.stop()
elif st.session_state.role != "Retailer":
    st.markdown("## 🚫 Access Denied. This page is for Retailers only.")
    st.stop()

# --- Sidebar Logout ---
with st.sidebar:
    st.markdown(f"**👤 {st.session_state.name} ({st.session_state.role})**")
    if st.button("🔓 Logout"):
        for key in ["logged_in", "username", "role", "name", "auth_mode"]:
            st.session_state.pop(key, None)
        st.switch_page("auth.py")

# --- Weather API ---
def get_weatherapi_forecast(city, api_key):
    url = f"http://api.weatherapi.com/v1/forecast.json?key={api_key}&q={city}&days=3&aqi=no&alerts=no"
    res = requests.get(url)
    if res.status_code != 200:
        raise Exception("WeatherAPI error or invalid city.")
    data = res.json()
    return pd.DataFrame({
        "forecast_day": [datetime.datetime.strptime(d["date"], "%Y-%m-%d").date() for d in data["forecast"]["forecastday"]],
        "temp": [d["day"]["avgtemp_c"] for d in data["forecast"]["forecastday"]],
        "rain": [d["day"]["totalprecip_mm"] for d in data["forecast"]["forecastday"]]
    })

# --- Holiday API ---
def get_calendarific_holidays(country_code, start_date, days, api_key):
    try:
        url = "https://calendarific.com/api/v2/holidays"
        res = requests.get(url, params={
            "api_key": api_key,
            "country": country_code,
            "year": start_date.year,
            "type": "national"
        })
        res.raise_for_status()
        holidays = [datetime.datetime.strptime(h["date"]["iso"], "%Y-%m-%d").date()
                    for h in res.json()["response"]["holidays"]]
        forecast_days = [start_date + datetime.timedelta(days=i) for i in range(days)]
        return pd.Series([1 if day in holidays else 0 for day in forecast_days], index=forecast_days)
    except Exception as e:
        st.warning(f"⚠️ Holiday API error: {e}")
        return pd.Series([0] * days, index=[start_date + datetime.timedelta(days=i) for i in range(days)])

# --- UI ---
st.title("📊 Dynamic Price Predictor (Retailer Panel)")
st.sidebar.header("🛠️ Input Conditions")

category = st.sidebar.selectbox("Category", ["Vegetables", "Fruits", "Dairy"])
product = st.sidebar.text_input("Product Name", "Tomato")
city = st.sidebar.text_input("City", "Mumbai")
stock = st.sidebar.slider("Stock", 10, 200, 100)
discount = st.sidebar.slider("Discount", 0.0, 1.0, 0.2)

# Dynamically adjust expiry range for Dairy
if category == "Dairy":
    days_to_expiry = st.sidebar.slider("Days to Expiry", 0.0, 10.0, 3.0)
else:
    days_to_expiry = st.sidebar.slider("Days to Expiry", 10.0, 40.0, 10.0)

mrp = st.sidebar.number_input("Base Price (MRP ₹)", 10.0, 1000.0, 100.0)
submit = st.sidebar.button("💡 Predict & Save")

# --- Main Logic ---
if submit:
    try:
        weather_api_key = st.secrets["api_keys"]["weather"]
        holiday_api_key = st.secrets["api_keys"]["holiday"]
        start_day = datetime.date.today()

        # Fetch weather
        weather_df = get_weatherapi_forecast(city, api_key=weather_api_key)
        weather_df = pd.concat([weather_df] * 4, ignore_index=True).head(10)
        weather_df["forecast_day"] = pd.date_range(datetime.date.today(), periods=10)

        # Fetch holidays
        city_country_map = {"Mumbai": "IN", "Delhi": "IN", "Bangalore": "IN", "New York": "US", "Toronto": "CA"}
        country = city_country_map.get(city, "IN")
        holiday_series = get_calendarific_holidays(country, start_day, 10, holiday_api_key)

        if len(weather_df) < 10 or len(holiday_series) < 10:
            st.error("❌ Insufficient forecast data from APIs.")
            st.stop()

        # Build Input DF
        input_df = pd.DataFrame({
            "stock_level": [stock] * 10,
            "discount": [discount] * 10,
            "days_to_expiry": [max(days_to_expiry - i, 0) for i in range(10)],
            "temperature": weather_df["temp"],
            "rain": weather_df["rain"],
            "holiday": holiday_series.values,
            "forecast_day": weather_df["forecast_day"].values,
            "mrp": [mrp] * 10
        })

        # Load model and predict
        with open("model.pkl", "rb") as f:
            model = pickle.load(f)

        # Add required features
        input_df["stock_expiry_ratio"] = input_df["stock_level"] / (input_df["days_to_expiry"] + 1)
        input_df["rain_temp_interaction"] = input_df["rain"] * input_df["temperature"]

        features = ["stock_level", "holiday", "rain", "temperature", "days_to_expiry",
                    "stock_expiry_ratio", "rain_temp_interaction"]

        predicted_multipliers = model.predict(input_df[features])


        # 🧠 Set minimum allowed price as 70% of MRP
        min_multiplier = 0.60
        min_price = min_multiplier * input_df["mrp"]

        # Dairy expiry condition
        if category == "Dairy":
            forecasted = predicted_multipliers * input_df["mrp"]
            forecasted = np.where(input_df["days_to_expiry"] > 0, forecasted, 0.0)
        else:
            forecasted = np.minimum(predicted_multipliers * input_df["mrp"], input_df["mrp"])

        # 🛡️ Apply minimum price floor
        input_df["forecasted_price"] = np.maximum(forecasted, min_price)

        input_df["category"] = category
        input_df["product"] = product

        # SHAP Explainability
        explainer = shap.Explainer(model)
        shap_values = explainer(input_df[features])

        st.subheader("🧬 Feature Importance (SHAP)")
        fig, ax = plt.subplots()
        shap.summary_plot(shap_values, input_df[features], plot_type="bar", show=False)
        plt.xlabel("Mean |SHAP value|")
        plt.tight_layout()
        st.pyplot(plt.gcf())  

        st.subheader("🧠 Feature Impact (SHAP) for Day 1 Prediction")
        fig = shap.plots._waterfall.waterfall_legacy(
            explainer.expected_value, shap_values.values[0], feature_names=features, show=False
        )
        st.pyplot(fig)

        # Save to DB
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
