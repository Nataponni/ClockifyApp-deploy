import streamlit as st
from datetime import date
import calendar
import base64
import pandas as pd
import requests
import hashlib
from io import BytesIO

from main import to_iso_format, get_entries_by_date
from main import generate_report_pdf_bytes, get_months_range_string
from main import build_pdf_filename
from main import LOGO_PATH, COMPANY_NAME



USERS = {
    "admin": "12345"  # пароль: "password"
}

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔐 Login")

    username = st.text_input("Benutzername")
    password = st.text_input("Passwort", type="password")
    if st.button("Login"):
        if username in USERS and USERS[username] == hash_password(password):
            st.session_state.authenticated = True
            st.success("Erfolgreich eingeloggt.")
            st.rerun()
        else:
            st.error("Falscher Benutzername oder Passwort.")

    st.stop()

# === Session State Init ===
for key in [
    "zeitraum_confirmed", "data_loaded", "df_date", "client_selected",
    "selected_projects", "final_confirmed", "pdf_bytes"
]:
    if key not in st.session_state:
        st.session_state[key] = pd.DataFrame() if key == "df_date" else ([] if key == "selected_projects" else False)

# === Page Config ===
st.set_page_config(page_title="Clockify Report Generator", layout="centered", initial_sidebar_state="auto")

# === Custom Style (Business Light Theme) ===
st.markdown(
    """
    <style>
    .block-container {
        padding-top: 2rem;
    }
    h1, h2, h3, h4 {
        color: #2c3e50;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# === Encode logo ===
def get_image_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

logo_base64 = get_image_base64(LOGO_PATH)

# === Header ===
st.markdown(
    f"""
    <div style="display: flex; justify-content: space-between; align-items: center;">
        <h2 style="margin: 0;">{COMPANY_NAME}</h2>
        <img src="data:image/png;base64,{logo_base64}" width="100" />  
    </div>
    <h3 style="margin: 0;">PDF Report</h3>
    <hr style="margin-top:1rem;margin-bottom:1.5rem;">
    """,
    unsafe_allow_html=True
)

# === Zeitraum auswählen ===
st.subheader("Zeitraum auswählen")
today = date.today()
first_day_of_month = today.replace(day=1)
end_day_of_month = today.replace(day=calendar.monthrange(today.year, today.month)[1])

start_date, end_date = st.date_input(
    "Wähle den Zeitraum:",
    value=(first_day_of_month, end_day_of_month),
    format="DD.MM.YYYY"
)

if start_date > end_date:
    st.error("Enddatum darf nicht vor dem Startdatum liegen.")
    st.stop()

# === Daten laden ===
if not st.session_state.data_loaded:
    if st.button("Daten laden"):
        for key in ["client_selected", "selected_projects", "final_confirmed", "pdf_bytes"]:
            st.session_state[key] = [] if key == "selected_projects" else False

        with st.spinner("Lade Daten von Clockify..."):
            try:
                start_iso = to_iso_format(start_date.strftime("%d-%m-%Y"), is_end=False)
                end_iso = to_iso_format(end_date.strftime("%d-%m-%Y"), is_end=True)
                df_date = get_entries_by_date(start_iso, end_iso)
            except requests.exceptions.RequestException as e:
                st.error(f"Netzwerkfehler: {e}")
                st.stop()

            if df_date.empty or 'client_name' not in df_date.columns:
                st.warning("Keine Daten im gewählten Zeitraum.")
                st.stop()

            st.session_state.df_date = df_date
            st.session_state.data_loaded = True
            st.success(f"{len(df_date)} Einträge geladen.")

# === Client auswählen ===
if st.session_state.data_loaded and not st.session_state.final_confirmed:
    st.subheader("Client auswählen")
    df_date = st.session_state.df_date
    clients = sorted(df_date['client_name'].dropna().unique())

    if not clients:
        st.warning("Keine Clients vorhanden.")
        st.stop()

    default_index = clients.index(st.session_state.client_selected) if st.session_state.client_selected in clients else 0
    client_selected = st.selectbox("Client: ", options=clients, index=default_index)
    st.session_state.client_selected = client_selected

    # === Projekte auswählen ===
    df_client = df_date[df_date['client_name'] == client_selected]
    projects = sorted(df_client['project_name'].dropna().unique())

    if not projects:
        st.warning("Keine Projekte vorhanden.")
        st.stop()

    valid_selected_projects = [p for p in st.session_state.selected_projects if p in projects]
    st.session_state.selected_projects = valid_selected_projects

    if len(projects) == 1:
        selected_projects = projects
        st.info(f"Nur ein Projekt verfügbar: **{projects[0]}** wird automatisch ausgewählt.")
    else:
        selected_projects = st.multiselect(
            "Verfügbare Projekte:",
            options=projects,
            default=valid_selected_projects
        )
        if st.button("Alle Projekte auswählen"):
            selected_projects = projects

    st.session_state.selected_projects = selected_projects


    if selected_projects and not st.session_state.final_confirmed:
        st.subheader("Überblick")
        st.success(
            f"Zeitraum: {start_date.strftime('%d.%m.%Y')} bis {end_date.strftime('%d.%m.%Y')}\n\n"
            f"Client: {client_selected}\n\nProjekte: {', '.join(selected_projects)}"
        )
        if st.button("Auswahl bestätigen"):
            st.session_state.final_confirmed = True

# === PDF-Download ===
if st.session_state.final_confirmed:
    st.subheader("PDF-Download")

    df_selected = st.session_state.df_date[
        (st.session_state.df_date['client_name'] == st.session_state.client_selected) &
        (st.session_state.df_date['project_name'].isin(st.session_state.selected_projects))
    ].sort_values(by='start', key=lambda x: pd.to_datetime(x, dayfirst=True))

    if df_selected.empty:
        st.warning("Keine Einträge gefunden.")
        st.stop()

    if not st.session_state.pdf_bytes:
        months_range = get_months_range_string(df_selected)
        total_hours = df_selected['duration_hours'].sum()
        data_rows = [
            [row['description'], row['task_name'], row['start'], f"{row['duration_hours']:.2f}".replace('.', ',')]
            for _, row in df_selected.iterrows()
        ]
        st.session_state.pdf_bytes = generate_report_pdf_bytes(
            logo_path=str(LOGO_PATH),
            company_name=COMPANY_NAME,
            months_range=months_range,
            rows=data_rows,
            total_hours=total_hours
        )

    first_date = pd.to_datetime(df_selected["start"], dayfirst=True).min()
    last_date = pd.to_datetime(df_selected["start"], dayfirst=True).max()
    pdf_filename = build_pdf_filename(
        st.session_state.client_selected,
        st.session_state.selected_projects,
        first_date,
        last_date
    )

    st.download_button(
        label="📥 PDF herunterladen",
        data=st.session_state.pdf_bytes,
        file_name=pdf_filename,
        mime="application/pdf"
    )

# === Navigation ===
if st.session_state.get("pdf_bytes"):
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Neuer Zeitraum"):
            for key in ["data_loaded", "df_date", "client_selected", "selected_projects", "final_confirmed", "pdf_bytes"]:
                st.session_state[key] = [] if key == "selected_projects" else (pd.DataFrame() if key == "df_date" else False)
            st.rerun()
    with col2:
        if st.button("Anderer Client"):
            for key in ["client_selected", "selected_projects", "final_confirmed", "pdf_bytes"]:
                st.session_state[key] = [] if key == "selected_projects" else False
            st.rerun()
    with col3:
        if st.button("Beenden"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.session_state["authenticated"] = False
            st.rerun()
