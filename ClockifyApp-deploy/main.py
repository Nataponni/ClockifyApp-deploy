from collections import defaultdict
from datetime import datetime
from pathlib import Path
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm
from babel.dates import format_date
import pandas as pd
import requests
import locale
import sys
import re
import os


# auto-loads the .env file


API_KEY      = 'NmYxYzcxZDItYTk2OS00MjljLTlhMzktYWE2ZWRmZTg0Njc5'
WORKSPACE_ID = '66052c545402842181578e74'
BASE_URL     = "https://api.clockify.me/api/v1"
HEADERS      = {'X-Api-Key': API_KEY, 'Content-Type': 'application/json'}

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "app_Flask" / "templates"
STATIC_DIR = BASE_DIR / "static"

COMPANY_NAME = "Inpro Analytics GmbH"
LOGO_PATH = STATIC_DIR / "Logo mit Slogan.png"
TEMPLATE_PATH = TEMPLATE_DIR / "report_template.html"
CSS_PATH = STATIC_DIR / "styles.css"

PAGE_SIZE = 1000

def to_iso_format(date_str: str, is_end=False) -> str:
    """
    Parse a human-friendly date string in formats:
       - DD-MM, DD.MM, DD/MM
       - DD-MM-YYYY, DD.MM.YYYY, DD/MM/YYYY
       - YYYY-MM-DD
    and return an ISO string:
       'YYYY-MM-DDT00:00:00Z'  (if is_end=False)
       'YYYY-MM-DDT23:59:59Z'  (if is_end=True).
    Raises ValueError on unsupported format.
    """
    date_str = date_str.strip()
    today = datetime.now()

    # Pattern for DD-MM(-YYYY), DD.MM(.YYYY) or DD/MM(/YYYY)
    m = re.match(r'^(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{4}))?$', date_str)
    if m:
        d, mo, y = m.groups()
        day, mon = int(d), int(mo)
        year     = int(y) if y else today.year
    else:
        # Fallback for strict YYYY-MM-DD
        try:
            dt0 = datetime.strptime(date_str, "%Y-%m-%d")
            day, mon, year = dt0.day, dt0.month, dt0.year
        except Exception:
            raise ValueError(f"Unsupported date format: '{date_str}'")

    # Build datetime at day boundary
    if is_end:
        dt = datetime(year, mon, day, 23, 59, 59)
    else:
        dt = datetime(year, mon, day, 0,  0,  0)

    # Return ISO8601 string with 'Z'
    return dt.isoformat(timespec="seconds") + "Z"


def fetch_all(endpoint: str, params: dict = None) -> list:
    """
    Fetch all pages from Clockify API. Returns a flat list of JSON objects.
    Raises RequestException on network or HTTP errors.
    """
    items = []
    page = 1
    session = requests.Session()
    default_params = {"page-size": PAGE_SIZE, "hydrated": True}
    
    while True:
        query = {**default_params, "page": page}
        if params:
            query.update(params)
        resp = session.get(f"{BASE_URL}{endpoint}",
                           headers=HEADERS,
                           params=query,
                           timeout=10)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        items.extend(batch)
        page += 1

    return items


def get_entries_by_date(start_iso: str, end_iso: str) -> pd.DataFrame:
    """
    Return a DataFrame of all time entries between start_iso and end_iso,
    including client_id and project_id for downstream logic.
    """
    users = fetch_all(f"/workspaces/{WORKSPACE_ID}/users")
    if not users:
        return pd.DataFrame()

    frames = []
    for user in users:
        # Fetch this user's time entries in the given date range
        entries = fetch_all(
            f"/workspaces/{WORKSPACE_ID}/user/{user['id']}/time-entries",
            params={"start": start_iso, "end": end_iso}
        )
        if not entries:
            continue

        # Normalize JSON into a flat DataFrame
        df = pd.json_normalize(entries, sep='.')

        # Extract IDs for later use
        df['description'] = df.get('description', pd.NA).fillna('').astype(str)
        df['client_id']  = df.get('project.clientId', pd.NA).fillna('').astype(str)
        df['project_id'] = df.get('projectId',       pd.NA).fillna('').astype(str)

        # Add user, client, project and task names
        df['user_name']    = user['name']
        df['client_name']  = df.get('project.clientName', '').fillna('').astype(str)
        df['project_name'] = df.get('project.name',       '').fillna('').astype(str)
        df['task_name'] = df.get('task.name', pd.Series(dtype='object')).fillna('Allgemein').replace('', 'Allgemein').astype(str)


        # Format the start timestamp as DD.MM.YYYY
        df['start'] = pd.to_datetime(df['timeInterval.start'], errors='coerce').dt.strftime('%d.%m.%Y')

        # Calculate duration in hours as a float
        df['duration_hours'] = (
            pd.to_datetime(df['timeInterval.end'])
          - pd.to_datetime(df['timeInterval.start'])
        ).dt.total_seconds() / 3600

        # Keep only the columns we need downstream
        frames.append(df[[
            'description',
            'user_name',
            'client_id',
            'client_name',
            'project_id',
            'project_name',
            'task_name',
            'start',
            'duration_hours'
        ]])

    # Combine all user frames into one DataFrame, or return empty if none
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build_client_name_map(clients: list[dict]) -> dict[str, list[str]]:
    """
    Build a mapping from lowercase client name to list of client IDs.
    Pure function: deterministic, no side effects.
    """
    mp: dict[str, list[str]] = defaultdict(list)
    for c in clients:
        name = c['name'].lower()
        mp[name].append(c['id'])
    return mp


def select_client_id(name_map: dict[str, list[str]], choice: str) -> str:
    """
    Given a name_map and a lowercase choice string,
    return the single client_id or raise:
      - KeyError       if choice not in name_map
      - ValueError     if choice maps to multiple IDs
    Pure function.
    """
    if choice not in name_map:
        raise KeyError(f"No such client: '{choice}'")
    ids = name_map[choice]
    if len(ids) > 1:
        raise ValueError(f"Ambiguous client '{choice}': {ids}")
    return ids[0]


def filter_by_client(df: pd.DataFrame, client_name: str) -> pd.DataFrame:
    """Pure: filter DataFrame by client_name (case-insensitive)."""
    key = client_name.lower()
    return df[df['client_name'].str.lower() == key].copy()


def filter_by_client_inter(df: pd.DataFrame) -> pd.DataFrame:
    """
    Interactive selection menu for choosing a client from a DataFrame.
    Shows only clients actually present in the given period.
    If the client name is ambiguous (multiple IDs), prompts user to pick one.
    """

    # 0️⃣ Clean client_name column
    df = df.copy()
    df['client_name'] = (
        df['client_name']
        .fillna('')
        .astype(str)
        .str.strip()
    )
    df = df[df['client_name'] != ""]

    # 1️⃣ Build client map only from available entries in the DataFrame
    client_records = (
        df[['client_id', 'client_name']]
        .drop_duplicates()
        .sort_values('client_name')
    )

    if client_records.empty:
        print("❌ Keine Clients in diesem Zeitraum vorhanden.")
        return df.iloc[0:0].copy()

    # 2️⃣ Build name_map: lowercase client_name -> list of IDs
    from collections import defaultdict
    name_map = defaultdict(list)
    for _, row in client_records.iterrows():
        name_map[row['client_name'].lower()].append(row['client_id'])

    available_names = sorted(name_map.keys())

    # 3️⃣ Main selection loop
    while True:
        print("\nVerfügbare Clients:")
        for i, client in enumerate(available_names, 1):
            print(f"  {i}. {client}")

        print("\nAuswahlmöglichkeiten:")
        print("  - Clientname/Nummer = genau ein Client auswählen")
        print("    Beispiel: 1 oder 2  oder  Neuroth")
        print("  - 'x' = Beenden")

        choice = input("\nDeine Auswahl: ").strip()

        if choice.lower() == "x":
            print("Programm wird beendet.")
            sys.exit(0)

        # Number = selection by index
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(available_names):
                selected_name = available_names[idx - 1]
                print(f"✅ Ausgewählter Client (Nummer): {selected_name}")
            else:
                print("❌ Fehler: Ungültige Nummer. Bitte erneut versuchen.")
                continue
        else:
            selected_name = choice.lower()
            if selected_name not in name_map:
                print("❌ Fehler: Client nicht gefunden. Bitte erneut versuchen.")
                continue
            print(f"✅ Ausgewählter Client (Name): {selected_name}")

        # 4️⃣ Check IDs for this name
        client_ids = name_map[selected_name]
        if len(client_ids) == 1:
            client_id = client_ids[0]
        else:
            # Multiple IDs found -> ask user to choose
            print(f"\n⚠️ Mehrere IDs für '{selected_name}' gefunden:")
            for i, cid in enumerate(client_ids, 1):
                print(f"  {i}. ID = {cid}")

            while True:
                sub_choice = input("Bitte Nummer oder ID eingeben: ").strip()
                if sub_choice.isdigit():
                    num = int(sub_choice)
                    if 1 <= num <= len(client_ids):
                        client_id = client_ids[num - 1]
                        break
                    if num in client_ids:
                        client_id = num
                        break
                print("❌ Ungültige Eingabe. Bitte erneut versuchen.")

        # 5️⃣ Filter DataFrame for this client
        df_client = df[
            (df['client_name'].str.lower() == selected_name) &
            (df['client_id'] == client_id)
        ]

        if df_client.empty:
            print(f"❌ Keine Einträge für diesen Client in diesem Zeitraum. Bitte anderen auswählen.")
            continue

        print(f"✅ {len(df_client)} Einträge gefunden für '{selected_name}' (ID={client_id})")
        return df_client.copy()


def filter_by_project(df: pd.DataFrame, project_name: str) -> pd.DataFrame:
    """
    Pure: filter df by project_name (case-insensitive), no I/O.
    """
    key = project_name.lower()
    return df[df['project_name'].str.lower() == key].copy()


def filter_by_project_inter(projects_in_client: list[str]) -> list[str]:
    """
    Interactive user selection of projects.

    The user can:
    - press Enter to select all projects
    - enter one project name
    - enter multiple names or numbers separated by comma, dot, or space
    - enter number(s) from the displayed list

    Returns a list of selected project names.
    """
    print("\n Verfügbare Projekte:")
    for i, proj in enumerate(projects_in_client, start=1):
        print(f"  {i}. {proj}")

    print("\n Auswahlmöglichkeiten:")
    print("  - ENTER ohne Eingabe = alle Projekte auswählen")
    print("  - Projektname / Nummer = genau ein Projekt auswählen")
    print("  - mehrere Namen / Nummern mit Komma oder Punkt trennen")
    print("    Beispiel: 1,2  oder  1.2  oder  Apfelsortenreport,Wartung")
    print("  - 'x' = Beenden")

    while True:
        choice = input("\n Deine Auswahl: ").strip()

        if choice.lower() == "x":
            print("Programm wird beendet.")
            sys.exit(0)

        if choice == "":
            # User wants all projects
            print("Alle Projekte ausgewählt.")
            return projects_in_client.copy()

        # Split input by , . or space
        tokens = re.split(r'[,\.]+', choice)
        tokens = [t.strip() for t in tokens if t.strip()]

        if not tokens:
            print("❌ Fehler: Keine Eingabe erkannt. Bitte erneut versuchen.")
            continue

        # Check if all tokens are numbers
        if all(t.isdigit() for t in tokens):
            try:
                idxs = [int(t) for t in tokens]
                selected = []
                for idx in idxs:
                    if 1 <= idx <= len(projects_in_client):
                        selected.append(projects_in_client[idx - 1])
                    else:
                        raise ValueError
                print(f"✅ Ausgewählte Projekte (Nummern): {selected}")
                return selected
            except ValueError:
                print("❌ Fehler: Ungültige Nummer(n). Bitte erneut versuchen.")
                continue

        # Otherwise treat as names
        matched = [p for p in projects_in_client if p in tokens]
        if not matched:
            print("❌ Fehler: Keine gültigen Projektnamen erkannt. Bitte erneut versuchen.")
            continue

        print(f"✅ Ausgewählte Projekte: {matched}")
        return matched


def get_data(client: str, project: str, start: str, end: str) -> pd.DataFrame:
    """Fetch and filter time entries based on client, project, and date range."""
    start_iso = to_iso_format(start, is_end=False)
    end_iso   = to_iso_format(end,   is_end=True)

    df_date   = get_entries_by_date(start_iso, end_iso)
    print(df_date[['user_name','start','project_id']].head())
 
    df_client = filter_by_client(df_date, client)
    df_proj   = filter_by_project(df_client, project)
    return df_proj


def generate_report_pdf(
    output_file,
    logo_path,
    company_name,
    months_range,
    rows,
    total_hours
):
    doc = SimpleDocTemplate(
        output_file,
        pagesize=A4,
        leftMargin=18*mm,
        rightMargin=10*mm,
        topMargin=10*mm,
        bottomMargin=10*mm
    )

    styles = getSampleStyleSheet()
    elements = []

    # HEADER
    header_table_data = []
    header_row = []

    header_row.append(Paragraph(
        company_name,
        ParagraphStyle(
            name='Company',
            fontSize=14,
            alignment=TA_LEFT,
            wordWrap='None',       # отключаем переносы (можно 'None' или 'CJK' для блокировки)
            splitLongWords=False, # запрещаем разрезать слова
            allowWidows=0,
            allowOrphans=0,
            leading=16                # высота строки (чтобы строки не слипались)       
        )
    ))  

    if logo_path and Path(logo_path).exists():
        try:
            img = Image(logo_path, width=25*mm, height=15*mm)
            header_row.append(img)
        except Exception as e:
            print(f"[WARN] Logo konnte nicht geladen werden: {e}")
            header_row.append('')
    else:
        header_row.append('')

    header_table_data.append(header_row)

    header_table = Table(header_table_data, colWidths=[120*mm, None])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (0,0), (0,0), 'LEFT'),   # Название — слева
        ('ALIGN', (1,0), (1,0), 'RIGHT'),  # Логотип — справа
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 24))

    # TITLE
    title_style = ParagraphStyle(
        name='Title',
        fontSize=12,
        leading=14,         # высота строки
        alignment=TA_LEFT,  # выравнивание слева
        spaceAfter=14,      # отступ снизу после заголовка
        fontName='Helvetica-bold'  # жирный шрифт
    )
    title_text = f"Stundenaufstellung {months_range}"
    title_para = Paragraph(title_text, title_style)
    # Оборачиваем параграф в таблицу для дополнительного управления выравниванием и стилями
    title_table = Table([[title_para]], colWidths=[180*mm])  # ширина таблицы по желанию
    title_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, 0), 'LEFT'),
        ('ALIGN', (2,1), (-1,-2), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        # Убрать GRID или поставить тонкую серую линию, если нужно
        # ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
    ]))
    # Добавляем в список элементов для сборки PDF
    elements.append(title_table)
    elements.append(Spacer(1, 24))  # Отступ после заголовка

    # TABLE 
    cell_style = ParagraphStyle(
        name='BodyTextLeft',
        parent=styles['BodyText'],
        alignment=TA_LEFT,
        wordWrap='CJK', # перенос по словам
        leading=12,
    )
    table_data = [['Beschreibung', 'Aufgabe', 'Datum', 'Dauer']]

    for row in rows:
        beschreibung_paragraph = Paragraph(row[0], cell_style)
        aufgabe = row[1]
        datum = row[2]
        dauer = row[3]
        table_data.append([beschreibung_paragraph, aufgabe, datum, dauer])
    table_data.append(['Gesamtaufwand:', '', '', f"{total_hours:.2f}".replace('.', ',') + " h"])

    tbl = Table(table_data, colWidths=[55*mm, 40*mm, 40*mm, 40*mm], repeatRows=1)

    style = TableStyle([
        # Шапка — жирный, все колонки по центру вертикально
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('VALIGN', (0,0), (-1,0), 'MIDDLE'),
        ('BACKGROUND', (0,0), (-1,0), colors.white),
        ('TEXTCOLOR', (0,0), (-1,0), colors.black),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('ALIGN', (0,0), (0,0), 'LEFT'),
        ('VALIGN', (0,0), (0,0), 'MIDDLE'),

        # В шапке колонки 2, 3 и 4 по центру горизонтально
        ('ALIGN', (1,0), (3,0), 'CENTER'),
        ('VALIGN', (1,0), (3,0), 'MIDDLE'),

        # Данные: 1 и 2 колонка — выравнивание слева сверху
        ('ALIGN', (0,1), (0,-2), 'LEFT'),  # Description
        ('VALIGN', (0,1), (0,-2), 'TOP'),
        ('ALIGN', (1,1), (1,-2), 'LEFT'),  # Aufgabe
        ('VALIGN', (1,1), (1,-2), 'MIDDLE'),

        # Данные: 3 и 4 колонка — по центру горизонтально и вертикально
        ('ALIGN', (2,1), (3,-2), 'CENTER'),
        ('VALIGN', (2,1), (3,-2), 'MIDDLE'),

        # Итоговая строка — жирная и светлый фон, с отступами и выравниванием
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor("#eaeaea")),
        ('TOPPADDING', (0,-1), (-1,-1), 6),
        ('BOTTOMPADDING', (0,-1), (-1,-1), 6),
        ('ALIGN', (3,-1), (3,-1), 'CENTER'),

        # Сетка
        ('GRID', (0,0), (-1,-1), 0.001, colors.HexColor("#555555")),
    ])

    for i in range(1, len(table_data)-1):
        if i % 2 == 0:
            style.add('BACKGROUND', (0,i), (-1,i), colors.white)
        else:
            style.add('BACKGROUND', (0,i), (-1,i), colors.HexColor("#eaeaea"))

    last_row = len(table_data) - 1
    style.add('FONTNAME', (0,last_row), (-1,last_row), 'Helvetica-Bold')
    style.add('BACKGROUND', (0,last_row), (-1,last_row), colors.HexColor("#eaeaea"))
    style.add('TOPPADDING', (0,last_row), (-1,last_row), 6)
    style.add('BOTTOMPADDING', (0,last_row), (-1,last_row), 6)
    style.add('ALIGN', (3,last_row), (3,last_row), 'CENTER')

    tbl.setStyle(style)
    elements.append(tbl)
    doc.build(elements)
    print(f"✅ PDF wurde erstellt: {output_file}")


def generate_report_pdf_bytes(
    logo_path,
    company_name,
    months_range,
    rows,
    total_hours
):
    """
    Generates the PDF and returns it as bytes (for use in Streamlit download_button).
    """
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18*mm,
        rightMargin=10*mm,
        topMargin=10*mm,
        bottomMargin=10*mm
    )

    styles = getSampleStyleSheet()
    elements = []

    # HEADER
    header_table_data = []
    header_row = []

    header_row.append(Paragraph(
        company_name,
        ParagraphStyle(
            name='Company',
            fontSize=14,
            alignment=TA_LEFT,
            leading=16,
            wordWrap='None',
            splitLongWords=False,
            allowWidows=0,
            allowOrphans=0
        )
    ))

    if logo_path and Path(logo_path).exists():
        try:
            img = Image(logo_path, width=25*mm, height=15*mm)
            header_row.append(img)
        except Exception as e:
            print(f"[WARN] Logo konnte nicht geladen werden: {e}")
            header_row.append('')
    else:
        header_row.append('')

    header_table_data.append(header_row)

    header_table = Table(header_table_data, colWidths=[120*mm, None])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (0,0), (0,0), 'LEFT'),
        ('ALIGN', (1,0), (1,0), 'RIGHT'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 24))

    # TITLE
    title_style = ParagraphStyle(
        name='Title',
        fontSize=12,
        leading=14,
        alignment=TA_LEFT,
        spaceAfter=14,
        fontName='Helvetica-bold'
    )
    title_text = f"Stundenaufstellung {months_range}"
    title_para = Paragraph(title_text, title_style)
    title_table = Table([[title_para]], colWidths=[180*mm])
    title_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, 0), 'LEFT'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(title_table)
    elements.append(Spacer(1, 24))

    # TABLE
    cell_style = ParagraphStyle(
        name='BodyTextLeft',
        parent=styles['BodyText'],
        alignment=TA_LEFT,
        wordWrap='CJK',
        leading=12,
    )
    table_data = [['Beschreibung', 'Aufgabe', 'Datum', 'Dauer']]

    for row in rows:
        beschreibung_paragraph = Paragraph(row[0], cell_style)
        aufgabe = row[1]
        datum = row[2]
        dauer = row[3]
        table_data.append([beschreibung_paragraph, aufgabe, datum, dauer])

    table_data.append(['Gesamtaufwand:', '', '', f"{total_hours:.2f}".replace('.', ',') + " h"])

    tbl = Table(table_data, colWidths=[55*mm, 40*mm, 40*mm, 40*mm], repeatRows=1)

    style = TableStyle([
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('VALIGN', (0,0), (-1,0), 'MIDDLE'),
        ('BACKGROUND', (0,0), (-1,0), colors.white),
        ('TEXTCOLOR', (0,0), (-1,0), colors.black),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('ALIGN', (0,0), (0,0), 'LEFT'),
        ('VALIGN', (0,0), (0,0), 'MIDDLE'),
        ('ALIGN', (1,0), (3,0), 'CENTER'),
        ('VALIGN', (1,0), (3,0), 'MIDDLE'),
        ('ALIGN', (2,0), (3,0), 'CENTER'),
        ('VALIGN', (2,0), (3,0), 'MIDDLE'),
        ('ALIGN', (0,1), (0,-2), 'LEFT'),  # Description
        ('VALIGN', (0,1), (0,-2), 'TOP'),
        ('ALIGN', (1,1), (1,-2), 'LEFT'),  # Aufgabe
        ('VALIGN', (1,1), (1,-2), 'MIDDLE'),
        ('ALIGN', (2,1), (3,-2), 'CENTER'),
        ('VALIGN', (2,1), (3,-2), 'MIDDLE'),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor("#eaeaea")),
        ('TOPPADDING', (0,-1), (-1,-1), 6),
        ('BOTTOMPADDING', (0,-1), (-1,-1), 6),
        ('ALIGN', (3,-1), (3,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.001, colors.HexColor("#555555")),
    ])

    for i in range(1, len(table_data)-1):
        if i % 2 == 0:
            style.add('BACKGROUND', (0,i), (-1,i), colors.white)
        else:
            style.add('BACKGROUND', (0,i), (-1,i), colors.HexColor("#eaeaea"))

    tbl.setStyle(style)
    elements.append(tbl)

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


def get_months_range_string(df: pd.DataFrame) -> str:
    """
    Returns a string like:
       - 'Juni 2025'
       - 'Mai/Juni 2025'
       - 'Juni/Juli/August 2025'
       - 'Dezember 2024, Januar 2025'
    depending on the dates in the DataFrame.
    """

    # Try setting German locale for month names
    try:
        locale.setlocale(locale.LC_TIME, 'de_DE.UTF-8')
    except locale.Error:
        print("⚠️ Achtung: German locale not available – months will be in English.")

    if df.empty:
        return ""

    # Make sure it's a real copy!
    df = df.copy()

    # Convert 'start' column to datetime, safely
    df["start_dt"] = pd.to_datetime(df["start"], format="%d.%m.%Y", errors="coerce")
    df = df.dropna(subset=["start_dt"])

    if df.empty:
        return ""

    # Extract year and month as Period
    df["year_month"] = df["start_dt"].dt.to_period("M")
    unique_periods = sorted(df["year_month"].unique())

    # Group months by year
    year_to_months = defaultdict(list)
    for p in unique_periods:
        year_to_months[p.year].append(p.month)

    def split_into_consecutive_blocks(months):
        months = sorted(set(months))
        if not months:
            return []
        blocks = []
        block = [months[0]]
        for m in months[1:]:
            if m - block[-1] == 1:
                block.append(m)
            else:
                blocks.append(block)
                block = [m]
        blocks.append(block)
        return blocks

    parts = []
    for year in sorted(year_to_months.keys()):
        months = sorted(set(year_to_months[year]))
        blocks = split_into_consecutive_blocks(months)

        block_parts = []
        for block in blocks:
            if len(block) > 1:
                month_names = [format_date(datetime(year, m, 1), "MMMM", locale='de') for m in block]
                block_parts.append("/".join(month_names) + f" {year}")
            else:
                month_name = datetime(year, block[0], 1).strftime("%B") + f" {year}"
                block_parts.append(month_name)

        parts.append(", ".join(block_parts))

    return ", ".join(parts)


def choose_period() -> tuple[str, str]:
    while True:
        raw_start = input("Start period (DD-MM or DD-MM-YYYY): ").strip()
        raw_end   = input("End   period (DD-MM or DD-MM-YYYY): ").strip()

        try:
            start_iso = to_iso_format(raw_start, is_end=False)
            end_iso   = to_iso_format(raw_end,   is_end=True)

            if start_iso > end_iso:
                print("❌ End date is before start date. Please try again.\n")
                continue

            print(f"✅ Selected period: {start_iso} … {end_iso}\n")
            return start_iso, end_iso

        except ValueError as e:
            print(f"❌ Invalid date: {e}. Please try again.\n")


def load_entries_for_period(start_iso: str, end_iso: str) -> pd.DataFrame:
    df_date = get_entries_by_date(start_iso, end_iso)
    return df_date


def build_pdf_filename(
    client_name: str,
    selected_projects: list[str],
    first_date: pd.Timestamp,
    last_date: pd.Timestamp
) -> str:
    """
    Generate the standard PDF filename including all months in the range.
    Format: Stundenauflistung_Client_Project_MM[_MM...]_YYYY or MM_YYYY-MM_YYYY if years differ
    """
    # Clean projects for filename
    if not selected_projects or all(p.strip().lower() in ("alle projekte", "alle") for p in selected_projects):
        project_part = ""
    elif len(selected_projects) == 1:
        project_part = f"_{selected_projects[0].replace('/', '_').replace(' ', '_')}"
    else:
        project_part = "_" + "_".join(p.replace('/', '_').replace(' ', '_') for p in selected_projects)

    # Collect all months between first_date and last_date
    months = []
    current = first_date.replace(day=1)
    while current <= last_date:
        months.append((f"{current.month:02d}", f"{current.year}"))
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)

    # Group by year
    years = {}
    for m, y in months:
        years.setdefault(y, []).append(m)

    if len(years) == 1:
        jahr = list(years.keys())[0]
        monate_part = "_".join(years[jahr])
        period_part = f"{monate_part}_{jahr}"
    else:
        period_parts = ["_".join(ms) + f"_{y}" for y, ms in years.items()]
        period_part = "--".join(period_parts)

    return f"Stundenauflistung_{client_name}{project_part}_{period_part}.pdf"


def process_reports_loop(df_date: pd.DataFrame, template_path: Path, logo_file: Path, css_file: Path):
    while True:
        # --- Select client ---
        df_client = filter_by_client_inter(df_date)

        # --- Filter available projects for that client ---
        projects_in_client = sorted(
            df_client.get('project_name', df_client.get('project.name', pd.Series()))
            .dropna().unique().tolist()
        )

        if not projects_in_client:
            print("❌ Keine Projekte für diesen Client gefunden. Bitte anderen Client wählen.\n")
            continue

        # --- Project selection ---
        selected_projects = filter_by_project_inter(projects_in_client)

        # --- Filter by selected projects ---
        df_proj = df_client[df_client['project_name'].isin(selected_projects)].copy()
        if df_proj.empty:
            print(f"❌ Keine Einträge für die Auswahl {selected_projects}. Bitte erneut versuchen.\n")
            continue

        # --- Get client name ---
        client_name = df_proj['client_name'].iloc[0]

        # --- Create printable project name ---
        if len(selected_projects) == 1:
            project_name = selected_projects[0]
        else:
            project_name = "_".join(selected_projects)

        print(f"✅ Gewählte Projekte: {project_name} ({len(df_proj)} Einträge).")

        # --- Format month_year column ---
        df_proj['month_year'] = pd.to_datetime(df_proj['start'], dayfirst=True).dt.strftime('%m.%Y')
        months_range = get_months_range_string(df_proj)

        # --- Calculate total hours ---
        total_hours = df_proj['duration_hours'].sum()

        # --- Sort by date ---
        df_proj = df_proj.sort_values(by='start', key=lambda x: pd.to_datetime(x, dayfirst=True), ascending=True)

        # --- Prepare table data ---
        for col in ['description', 'task_name']:
            df_proj[col] = (
                df_proj[col]
                .fillna('Allgemein')
                .astype(str)
                .str.strip()
                .replace(r'^$', 'Allgemein', regex=True)
            )

        data_rows = [
            [row['description'], row['task_name'], row['start'], f"{row['duration_hours']:.2f}".replace('.', ',')]
            for _, row in df_proj.iterrows()
        ]

        # --- Create PDF filename ---
        start_dates = pd.to_datetime(df_proj["start"], dayfirst=True, errors="coerce")
        first_date = start_dates.min()
        last_date = start_dates.max()

        pdf_filename = build_pdf_filename(client_name, selected_projects, first_date, last_date)

        # --- Generate PDF ---
        generate_report_pdf(
            output_file=pdf_filename,
            logo_path=str(logo_file),
            company_name=COMPANY_NAME,
            months_range=months_range,
            rows=data_rows,
            total_hours=total_hours
        )
        print(f"✅ Kompletter Report für {client_name} / {project_name} fertig!\n")

        # --- Ask for another report ---
        again = input("Möchten Sie einen weiteren Report erstellen? (y/N): ").strip().lower()
        if again not in ('y', 'yes'):
            print("✅ Programm wird beendet.")
            break


if __name__ == "__main__":

    if not LOGO_PATH.exists():
        raise FileNotFoundError(f"❌ Logo nicht gefunden: {LOGO_PATH}")
    if not CSS_PATH.exists():
        raise FileNotFoundError(f"❌ CSS-Datei nicht gefunden: {CSS_PATH}")
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"❌ Template nicht gefunden: {TEMPLATE_PATH}")

    # --- Select time period and load data ---
    start_iso, end_iso = choose_period()
    df_date = load_entries_for_period(start_iso, end_iso)

    if df_date.empty:
        print("⚠️ Keine Daten im gewählten Zeitraum!")
        sys.exit(0)

    # --- Start processing reports ---
    process_reports_loop(df_date, TEMPLATE_PATH, LOGO_PATH, CSS_PATH)