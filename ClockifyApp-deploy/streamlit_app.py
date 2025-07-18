import streamlit as st
from datetime import date
import pandas as pd
import requests
from io import BytesIO

from main import to_iso_format, get_entries_by_date
from main import generate_report_pdf_bytes, get_months_range_string
from main import build_pdf_filename
from main import LOGO_PATH, COMPANY_NAME


# === Session State Init ===
for key in [
    "zeitraum_confirmed",
    "data_loaded",
    "df_date",
    "client_selected",
    "selected_projects",
    "final_confirmed",
    "pdf_bytes"
]:
    if key not in st.session_state:
        if key == "df_date":
            st.session_state[key] = pd.DataFrame()
        elif key == "selected_projects":
            st.session_state[key] = []
        else:
            st.session_state[key] = False


# === Title ===
st.title("Clockify PDF Report Generator")

# === Zeitraum auswählen ===
st.subheader("1️⃣ Zeitraum auswählen")
today = date.today()
first_day_of_month = today.replace(day=1)

start_date = st.date_input("Startdatum", value=first_day_of_month, format="DD.MM.YYYY")
end_date = st.date_input("Enddatum", value=today, format="DD.MM.YYYY")

if start_date > end_date:
    st.error("❌ Fehler: Enddatum darf nicht vor dem Startdatum liegen!")
    st.stop()

# === Daten laden ===
if not st.session_state.data_loaded:
    if st.button("Daten laden"):
        # Reset all selections
        for key in ["client_selected", "selected_projects", "final_confirmed", "pdf_bytes"]:
            st.session_state[key] = False if key != "selected_projects" else []

        with st.spinner("⏳ Clockify-Daten werden geladen..."):
            try:
                start_iso = to_iso_format(start_date.strftime("%d-%m-%Y"), is_end=False)
                end_iso = to_iso_format(end_date.strftime("%d-%m-%Y"), is_end=True)
                df_date = get_entries_by_date(start_iso, end_iso)
            except requests.exceptions.RequestException as e:
                st.error(f"❌ Netzwerkfehler: {e}")
                st.stop()

            if df_date.empty or 'client_name' not in df_date.columns:
                st.warning("⚠️ Keine Daten im gewählten Zeitraum!")
                st.stop()

            st.session_state.df_date = df_date
            st.session_state.data_loaded = True
            st.success(f"✅ {len(df_date)} Einträge geladen.")

# === Client auswählen ===
if st.session_state.data_loaded and not st.session_state.final_confirmed:
    st.subheader("2️⃣ Client auswählen")
    df_date = st.session_state.df_date    
    clients = sorted(df_date['client_name'].dropna().unique())

    if not clients:
        st.warning("⚠️ Keine Clients im gewählten Zeitraum!")
        st.stop()

    # Index für Vorauswahl bestimmen
    if st.session_state.client_selected in clients:
        default_index = clients.index(st.session_state.client_selected)
    else:
        default_index = 0

    client_selected = st.selectbox(
        "👤 Client auswählen",
        options=clients,
        index=default_index
    )
    st.session_state.client_selected = client_selected


    # === Projekte auswählen ===
    if client_selected:
        st.subheader("3️⃣ Projekte auswählen")
        df_client = df_date[df_date['client_name'] == client_selected]
        projects = sorted(df_client['project_name'].dropna().unique())

        if not projects:
            st.warning("⚠️ Keine Projekte für diesen Client!")
            st.stop()
       
        if len(projects) == 1:
            selected_projects = projects
        else:
        
            # 🟢 Filter old selection
            valid_selected_projects = [
                p for p in st.session_state.selected_projects if p in projects
            ]
            st.session_state.selected_projects = valid_selected_projects

            if not isinstance(st.session_state.selected_projects, list):
                st.session_state.selected_projects = []

            selected_projects = st.multiselect(
                "📌 Verfügbare Projekte (Mehrfach möglich):",
                options=projects,
                default=st.session_state.selected_projects
            )

            if st.button("✨ Alle auswählen"):
                selected_projects = projects

        st.session_state.selected_projects = selected_projects

        # === Überblick und Button Bestätigen / Ändern ===
        if st.session_state.selected_projects and not st.session_state.final_confirmed:
            st.subheader("✅ Überblick")
            st.success(
                f"""
                **Zeitraum:** {start_date.strftime('%d.%m.%Y')} bis {end_date.strftime('%d.%m.%Y')}  
                **Client:** {st.session_state.client_selected}  
                **Projekte ({len(st.session_state.selected_projects)}):** {', '.join(st.session_state.selected_projects)}
                """
            )

            col1, col2 = st.columns(2)
            with col1:
                if st.button("✅ Auswahl abschließen"):
                    st.session_state.final_confirmed = True
                    # === PDF generieren und Download ===
                if st.session_state.final_confirmed:
                    st.subheader("4️⃣ PDF-Download bereit")

                    df_client = st.session_state.df_date[
                        st.session_state.df_date['client_name'] == st.session_state.client_selected
                    ]
                    df_selected = df_client[
                        df_client['project_name'].isin(st.session_state.selected_projects)
                    ]
                    df_selected = df_selected.sort_values(
                        by='start',
                        key=lambda x: pd.to_datetime(x, dayfirst=True),
                        ascending=True  # чтобы от старых к новым
                    )


                    if df_selected.empty:
                        st.warning("⚠️ Keine Einträge für die gewählten Projekte!")
                        st.stop()

                    # Generate PDF 
                    if not st.session_state.pdf_bytes:
                        months_range = get_months_range_string(df_selected)
                        total_hours = df_selected['duration_hours'].sum()

                        data_rows = [
                            [
                                row['description'],
                                row['task_name'],
                                row['start'],
                                f"{row['duration_hours']:.2f}".replace('.', ',')
                            ]
                            for _, row in df_selected.iterrows()
                        ]

                        st.session_state.pdf_bytes = generate_report_pdf_bytes(
                            logo_path=str(LOGO_PATH),
                            company_name=COMPANY_NAME,
                            months_range=months_range,
                            rows=data_rows,
                            total_hours=total_hours
                        )

                    start_dates = pd.to_datetime(df_selected["start"], dayfirst=True, errors="coerce").sort_values()
                    first_date = start_dates.iloc[0]
                    last_date = start_dates.iloc[-1]
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


