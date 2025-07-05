# --- Imports and Setup ---
import streamlit as st
import pandas as pd
import numpy as np
import requests
import datetime
from datetime import timedelta
import os
import sqlite3

st.set_page_config(page_title="10-Day Smart Forecast", layout="wide")

# --- Access Control ---
if "logged_in" not in st.session_state or not st.session_state.logged_in:
    st.error("Access Denied. Please log in from the Auth page.")
    st.stop()
elif st.session_state.role != "User":
    st.error("Access Denied. This page is for Users only.")
    st.stop()

# --- Setup DB for User Cart ---
def setup_user_db():
    conn = sqlite3.connect("retail_forecasts.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_cart (
            username TEXT,
            category TEXT,
            product TEXT,
            quantity INTEGER,
            locked_date TEXT,
            locked_price REAL
        )
    """)
    conn.commit()
    conn.close()

setup_user_db()


def save_user_cart(username):
    conn = sqlite3.connect("retail_forecasts.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_cart WHERE username = ?", (username,))
    for (cat, prod, qty) in st.session_state.cart:
        locked = st.session_state.locked_prices.get((cat, prod))
        if locked:
            locked_date, locked_price = locked
            cursor.execute("""
                INSERT INTO user_cart (username, category, product, quantity, locked_date, locked_price)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (username, cat, prod, qty, locked_date.strftime("%Y-%m-%d"), locked_price))
        else:
            cursor.execute("""
                INSERT INTO user_cart (username, category, product, quantity, locked_date, locked_price)
                VALUES (?, ?, ?, ?, NULL, NULL)
            """, (username, cat, prod, qty))
    conn.commit()
    conn.close()

# --- Sidebar Logout ---
with st.sidebar:
    st.markdown(f"**👤 {st.session_state.name} ({st.session_state.role})**")
    if st.button("🔓 Logout"):
        save_user_cart(st.session_state["username"])  # Save before logout
        for key in ["logged_in", "username", "role", "name", "auth_mode", "cart", "locked_prices", "lock_status"]:
            st.session_state.pop(key, None)
        st.switch_page("auth.py")

# --- Title and Description ---
st.title("🧠 10-Day Dynamic Price Forecast")
st.markdown("""
This tool shows forecasted **optimal prices** for products set by retailers:
- 📈 10-day forecast from retailer's model
- 🧠 Reason-based explanation for low prices
- 🛒 Add multiple products to cart with quantity
- 🔔 WhatsApp alerts coming soon
""")

# --- Load Forecasts from SQLite ---
def load_available_products():
    conn = sqlite3.connect("retail_forecasts.db")
    df = pd.read_sql_query("SELECT DISTINCT category, product FROM forecasts", conn)
    conn.close()
    return df

def fetch_prediction(category, product):
    conn = sqlite3.connect("retail_forecasts.db")
    df = pd.read_sql_query(
        "SELECT * FROM forecasts WHERE category = ? AND product = ? ORDER BY forecast_day ASC",
        conn,
        params=(category, product)
    )
    conn.close()
    df["forecast_day"] = pd.to_datetime(df["forecast_day"], errors='coerce')
    return df

# --- Load User Session Cart and Lock State ---
def load_user_session_cart(username):
    conn = sqlite3.connect("retail_forecasts.db")
    cursor = conn.cursor()
    cursor.execute("SELECT category, product, quantity, locked_date, locked_price FROM user_cart WHERE username = ?", (username,))
    rows = cursor.fetchall()
    conn.close()

    cart = []
    locked_prices = {}
    lock_status = {}

    for row in rows:
        cat, prod, qty, ldate, lprice = row
        cart.append((cat, prod, qty))
        if ldate and lprice is not None:
            locked_prices[(cat, prod)] = (pd.to_datetime(ldate), lprice)
            lock_status[(cat, prod)] = False
        else:
            lock_status[(cat, prod)] = True
    return cart, locked_prices, lock_status



# --- Initialize Session State ---
if "cart" not in st.session_state:
    username = st.session_state.get("username", "unknown")
    cart, locked, lockstatus = load_user_session_cart(username)
    st.session_state.cart = cart
    st.session_state.locked_prices = locked
    st.session_state.lock_status = lockstatus

if "latest_forecast" not in st.session_state:
    st.session_state.latest_forecast = None
    st.session_state.latest_product = None

if "add_disabled" not in st.session_state:
    st.session_state.add_disabled = True

if "qty_count" not in st.session_state:
    st.session_state.qty_count = 1

# --- Product Selection ---
products_df = load_available_products()
if products_df.empty:
    st.warning("No forecasts available yet. Please check back later.")
    st.stop()

categories = sorted(products_df["category"].unique())
category = st.sidebar.selectbox("📦 Select Category", categories)
filtered_products = products_df[products_df["category"] == category]["product"].unique()
product = st.sidebar.selectbox("🍅 Select Product", sorted(filtered_products))
price_threshold = st.sidebar.number_input("Notify me if price drops below (₹)", 1.0, 500.0, 30.0)

# --- Generate Forecast and Add to Cart Buttons ---
col_gen, col_add = st.sidebar.columns([1, 1])
if col_gen.button("Generate Forecast"):
    df = fetch_prediction(category, product)
    if df.empty:
        st.warning("No forecast data found for the selected product.")
    else:
        df.set_index("forecast_day", inplace=True)
        st.session_state.latest_forecast = df.copy()
        st.session_state.latest_product = product
        st.session_state.add_disabled = False
        st.session_state.qty_count = 1

item_in_cart = any(c == category and p == product for c, p, _ in st.session_state.cart)
if item_in_cart:
    col_add.button("✅ Added to Cart", disabled=True)
else:
    if col_add.button("🛒 Add to Cart"):
        st.session_state.cart.append((category, product, st.session_state.qty_count))
        st.session_state.lock_status[(category, product)] = True
        save_user_cart(st.session_state["username"])
        st.session_state.add_disabled = True
        st.rerun()

# --- Forecast Display ---
if st.session_state.latest_forecast is not None:
    df = st.session_state.latest_forecast.copy()
    product = st.session_state.latest_product
    selected_category = category

    st.markdown(f"### 📦 Forecast for: **{product}**")

    is_dairy = selected_category.lower() == "dairy"
    if is_dairy:
        df = df[df["forecasted_price"] > 0]

    today = pd.Timestamp.today().normalize()
    rolling_df = df[df.index >= today].copy().head(3)

    if rolling_df.empty:
        st.warning("⚠️ No valid forecasted prices available.")
    else:
        min_price = rolling_df["forecasted_price"].min()
        max_price = rolling_df["forecasted_price"].max()
        avg_price = rolling_df["forecasted_price"].mean()
        std_dev = rolling_df["forecasted_price"].std()
        ci_low = avg_price - 1.28 * std_dev
        ci_high = avg_price + 1.28 * std_dev

        volatility_ratio = std_dev / avg_price if avg_price else 0
        if volatility_ratio < 0.05:
            confidence = "~95%"
        elif volatility_ratio < 0.1:
            confidence = "~90%"
        elif volatility_ratio < 0.2:
            confidence = "~80%"
        elif volatility_ratio < 0.3:
            confidence = "~70%"
        elif volatility_ratio < 0.4:
            confidence = "~60%"
        elif volatility_ratio < 0.5:
            confidence = "~50%"
        else:
            confidence = "~40%"

        st.markdown("### 📊 Forecast Confidence Interval")
        st.info(
            f"🔎 Based on price trends, there's a {confidence} chance prices will stay between "
            f"**₹{ci_low:.2f} – ₹{ci_high:.2f}** over the next {len(rolling_df)} days."
        )

        if std_dev / avg_price > 0.3:
            st.warning("⚠️ High volatility detected in forecasted prices. Consider checking more frequently.")

        best_day = rolling_df["forecasted_price"].idxmin()
        best_price = rolling_df.loc[best_day, "forecasted_price"]
        st.success(f"📆 Best buy window: **{best_day.strftime('%b %d')}**")

        try:
            day_data = rolling_df.loc[best_day]
            insights = []
            if "discount" in rolling_df.columns and day_data["discount"] > 0.2:
                insights.append("🟢 Retailer is offering a high discount.")
            if "stock" in rolling_df.columns and day_data["stock"] > 150:
                insights.append("🔴 Stock is in excess, likely causing markdowns.")
            if "days_to_expiry" in rolling_df.columns and day_data["days_to_expiry"] < 5:
                insights.append("🟡 Product is nearing expiry.")
            if "rain" in rolling_df.columns and day_data["rain"] > 8:
                insights.append("🟡 Rain may reduce foot traffic or demand.")
            if "temperature" in rolling_df.columns and (day_data["temperature"] < 10 or day_data["temperature"] > 35):
                insights.append("🟡 Extreme temperatures predicted on that day.")
            if "holiday" in rolling_df.columns and day_data["holiday"] == 1:
                insights.append("🟢 Holiday pricing behavior has been applied.")
            if not insights:
                insights.append("ℹ️ Standard price adjustment by the retailer.")

            col1, col2 = st.columns([2, 1])
            with col1:
                st.markdown("### 🤖 Why are prices low in this window?")
                for insight in insights:
                    st.markdown(f"- {insight}")

            lock_key = (selected_category, product)
            is_locked = lock_key in st.session_state.locked_prices
            is_clickable = st.session_state.lock_status.get(lock_key, True)

            with col2:
                if not any(c == selected_category and p == product for c, p, _ in st.session_state.cart):
                    st.caption("🛒 Add the product to your cart before locking the price.")
                    st.button("🔐 Lock this price", disabled=True, key=f"lockbtn_{product}")
                elif is_locked:
                    locked_date, locked_price = st.session_state.locked_prices[lock_key]
                    st.success(f"🔒 Price locked for {product} on {locked_date.strftime('%b %d')} at ₹{locked_price:.2f}")
                    st.button("🔒 Locked", disabled=True, key=f"lockbtn_{product}")
                elif not is_clickable:
                    st.caption("🔐 You’ve already locked this price. Modify cart to unlock again.")
                    st.button("🔒 Locked", disabled=True, key=f"lockbtn_{product}")
                else:
                    st.caption(f"✅ Lock this price on {best_day.strftime('%b %d')} so that even if it changes later, you're unaffected.")
                    if st.button("🔐 Lock this price", key=f"lockbtn_{product}"):
                        st.session_state.locked_prices[lock_key] = (best_day, best_price)
                        st.session_state.lock_status[lock_key] = False
                        save_user_cart(st.session_state["username"])
                        st.rerun()

        except Exception as e:
            st.warning(f"Could not generate insight: {e}")

# --- Sidebar Cart Viewer ---
with st.sidebar.expander("🛒 View Cart & Best Buy Window", expanded=True):
    if st.session_state.cart:
        st.markdown("### Your Cart")
        total_df = pd.DataFrame()
        product_days = {}

        for idx, (cat, prod, qty) in enumerate(st.session_state.cart):
            cart_df = fetch_prediction(cat, prod)
            if not cart_df.empty:
                cart_df["product"] = prod
                cart_df["category"] = cat

                if "dairy" in cat.lower():
                    cart_df = cart_df[cart_df["forecasted_price"] > 0]

                valid_days = set(cart_df["forecast_day"])
                product_days[f"{cat}_{prod}"] = valid_days
                cart_df["forecasted_price"] *= qty
                total_df = pd.concat([total_df, cart_df], axis=0)

                col1, col2, col3 = st.columns([3, 1, 1])
                col1.markdown(f"- {prod} (x{qty})")

                if col2.button("➕", key=f"inc_{cat}_{prod}"):
                    st.session_state.cart[idx] = (cat, prod, qty + 1)
                    st.session_state.lock_status[(cat, prod)] = True
                    save_user_cart(st.session_state["username"])
                    st.rerun()

                if col3.button("➖", key=f"dec_{cat}_{prod}"):
                    if qty > 1:
                        st.session_state.cart[idx] = (cat, prod, qty - 1)
                        st.session_state.lock_status[(cat, prod)] = True
                    else:
                        st.session_state.cart.pop(idx)
                        st.session_state.locked_prices.pop((cat, prod), None)
                        st.session_state.lock_status.pop((cat, prod), None)
                    save_user_cart(st.session_state["username"])
                    st.rerun()

        if not total_df.empty:
            total_df["forecast_day"] = pd.to_datetime(total_df["forecast_day"], errors='coerce')
            if product_days:
                common_days = set.intersection(*product_days.values())
                filtered_df = total_df[total_df["forecast_day"].isin(common_days)]
                if not filtered_df.empty:
                    grouped = filtered_df.groupby("forecast_day")["forecasted_price"].sum()
                    best_day = grouped.idxmin()
                    st.markdown("### 📅 Best Buy Window")
                    st.success(f"🗓️ {best_day.strftime('%b %d')}")
                    st.markdown("""
                    <div style='
                        background-color: #f9d976;
                        padding: 14px 18px;
                        border-radius: 10px;
                        margin-top: 12px;
                        margin-bottom: 8px;
                        font-size: 0.92rem;
                        border: 1px solid #e0c265;
                    '>
                    🔁 <b>Prices update every 12 hours.</b><br>
                    Best-buy recommendations may shift based on real-time inventory, expiry, or local conditions. 
                    We recommend revisiting frequently for the latest window.
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.warning("⚠️ No common valid forecasted days found.")
            else:
                st.warning("⚠️ No valid forecasted days for any product.")
    else:
        st.info("Your cart is empty.")
