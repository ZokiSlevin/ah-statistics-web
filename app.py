import os
import glob
import json
import csv
from datetime import datetime, date
from collections import Counter
from io import BytesIO

import pandas as pd
import streamlit as st
from openpyxl import Workbook
import matplotlib.pyplot as plt

# ⚠️ OVO MORA BITI PRVI st.* POZIV U APP-U
st.set_page_config(
    page_title="MEVA - Alati",
    page_icon="images/me.png",   # ← tu promijeniš ikonu
    layout="wide",
)

# ---------------------------------------------------------
# PUTEVI
# ---------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
IMAGES_DIR = os.path.join(BASE_DIR, "images")

LOGO_ME = os.path.join(IMAGES_DIR, "me.png")
LOGO_AH = os.path.join(IMAGES_DIR, "ah.png")

ORG_FILE = "Organizations.xlsx"
ORG_SHEET = "Organizations"

os.makedirs(DATA_DIR, exist_ok=True)

# ---------------------------------------------------------
# POMOĆNE FUNKCIJE (STATISTIKA LOGOVA)
# ---------------------------------------------------------

def parse_timestamp(ts_str: str) -> datetime:
    """
    Parsiranje time_stamp stringa u datetime.
    Podržava:
    - 2025-11-01T07:31:56+0000
    - 2025-11-01T07:31:56
    - 2025-11-01T07:31:56Z
    """
    ts_str = (ts_str or "").strip()

    # 1) kompletan format s offsetom
    try:
        return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        pass

    # 2) bez vremenske zone
    try:
        return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        pass

    # 3) ISO "Z" na kraju
    if ts_str.endswith("Z"):
        try:
            return datetime.strptime(ts_str[:-1], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass

    raise ValueError(f"Ne mogu parsirati time_stamp: {ts_str}")


def list_data_files():
    """
    Vraća samo datoteke za AH Statistiku:
    - log*.json
    - log*.csv
    (ignorira *_statistika.csv koje koristi VIN modul)
    """
    files = []
    if os.path.isdir(DATA_DIR):
        for fname in sorted(os.listdir(DATA_DIR)):
            path = os.path.join(DATA_DIR, fname)
            if not os.path.isfile(path):
                continue

            name_lower = fname.lower()
            ext = os.path.splitext(fname)[1].lower()

            # uzmi samo log datoteke
            if name_lower.startswith("log") and ext in (".json", ".csv"):
                files.append(fname)

    return files


@st.cache_data(show_spinner="Učitavanje podataka iz data/ foldera...")
def load_log_data(selected_files):
    """
    Učitava JSON/CSV logove (modul 'AH Statistika').
    """
    data = []
    org_id_to_name = {}
    min_date = None
    max_date = None

    def update_min_max(d: date):
        nonlocal min_date, max_date
        if d is None:
            return
        if min_date is None or d < min_date:
            min_date = d
        if max_date is None or d > max_date:
            max_date = d

    def load_json(path: str):
        nonlocal data, org_id_to_name
        try:
            with open(path, "r", encoding="utf-8") as f:
                arr = json.load(f)
        except Exception as e:
            st.warning(f"Ne mogu učitati JSON datoteku {os.path.basename(path)}: {e}")
            return

        if not isinstance(arr, list):
            st.warning(f"JSON datoteka nije lista zapisa: {os.path.basename(path)}")
            return

        for rec in arr:
            ts_str = rec.get("time_stamp")
            if not ts_str:
                continue

            try:
                d = parse_timestamp(ts_str).date()
                update_min_max(d)
            except Exception:
                continue

            oid = rec.get("organization_id")
            oname = rec.get("organization_name")
            if oid and oname and oid not in org_id_to_name:
                org_id_to_name[oid] = oname

            data.append(rec)

    def load_csv(path: str):
        nonlocal data, org_id_to_name
        try:
            with open(path, "r", encoding="cp1250", newline="") as f:
                reader = csv.DictReader(f, delimiter=";")
                for row in reader:
                    vin = (row.get("vin") or "").strip()
                    order_date = (row.get("order_date") or "").strip()
                    org_id = (row.get("organisation") or "").strip()
                    user_id = (row.get("order_client") or "").strip()

                    if not vin or not order_date:
                        continue

                    try:
                        dt = datetime.strptime(order_date, "%Y-%m-%d %H:%M:%S")
                        d = dt.date()
                        update_min_max(d)
                        time_stamp = dt.strftime("%Y-%m-%dT%H:%M:%S+0000")
                    except ValueError:
                        continue

                    org_name = org_id_to_name.get(org_id, org_id)

                    rec = {
                        "user_id": user_id,
                        "organization_id": org_id,
                        "organization_name": org_name,
                        "query_vin": vin,
                        "time_stamp": time_stamp,
                        "response_type": None,
                    }
                    data.append(rec)
        except Exception as e:
            st.warning(f"Ne mogu učitati CSV datoteku {os.path.basename(path)}: {e}")

    for fname in sorted(selected_files):
        path = os.path.join(DATA_DIR, fname)
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext == ".json":
            load_json(path)
        elif ext == ".csv":
            load_csv(path)

    org_names = sorted(
        {
            i.get("organization_name")
            for i in data
            if i.get("organization_name")
        }
    )

    return data, org_names, min_date, max_date


def calculate_stats(data, org_name, d_from: date, d_to: date):
    """
    Filtriranje logova + statistika.
    """
    unique_records = {}
    per_day = Counter()
    vin_counter = Counter()

    for item in data:
        if org_name and item.get("organization_name") != org_name:
            continue

        ts_str = item.get("time_stamp")
        if not ts_str:
            continue

        try:
            ts = parse_timestamp(ts_str)
        except ValueError:
            continue

        d = ts.date()
        if not (d_from <= d <= d_to):
            continue

        qvin = item.get("query_vin")
        key = (qvin, ts_str)

        if key not in unique_records:
            row = {
                "user_id": item.get("user_id"),
                "organization_id": item.get("organization_id"),
                "organization_name": item.get("organization_name"),
                "query_vin": item.get("query_vin"),
                "time_stamp": item.get("time_stamp"),
            }
            unique_records[key] = row

            per_day[d] += 1

            if qvin:
                vin_counter[qvin] += 1

    export_rows = list(unique_records.values())
    top_vins = vin_counter.most_common(5)

    return export_rows, per_day, top_vins


def make_excel_bytes(rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "Upiti"

    headers = ["user_id", "organization_id", "organization_name", "query_vin", "time_stamp"]
    ws.append(headers)

    for r in rows:
        ws.append(
            [
                r.get("user_id"),
                r.get("organization_id"),
                r.get("organization_name"),
                r.get("query_vin"),
                r.get("time_stamp"),
            ]
        )

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()

# ---------------------------------------------------------
# POMOĆNE FUNKCIJE (STATISTIČKI CSV + VIN SEARCH)
# ---------------------------------------------------------

@st.cache_data(show_spinner="Učitavanje statistike...")
def load_stat_data():
    """
    Učitava sve *_statistika.csv + Organizations.xlsx
    (modul 'Pretraga VIN brojeva').
    """
    pattern = os.path.join(DATA_DIR, "*_statistika.csv")
    files = sorted(glob.glob(pattern))

    if not files:
        return None, "Nisam našao statističke CSV datoteke u 'data/'"

    frames = []

    for path in files:
        year = os.path.basename(path).split("_")[0]  # npr. 2018

        try:
            df = pd.read_csv(path, dtype=str, encoding="utf-8")
        except Exception as e:
            return None, f"Problem pri čitanju CSV datoteke {os.path.basename(path)}: {e}"

        if "CUSTOMERID" in df.columns:
            df["CUSTOMERID"] = df["CUSTOMERID"].astype(str).str.zfill(9)
        else:
            return None, f"U datoteci {os.path.basename(path)} nedostaje kolona 'CUSTOMERID'."

        if "MANUFACTURERCODE" in df.columns:
            df["MANUFACTURERCODE"] = df["MANUFACTURERCODE"].astype(str).str.zfill(2)

        df["YEAR"] = year
        frames.append(df)

    stat_df = pd.concat(frames, ignore_index=True)

    org_path = os.path.join(DATA_DIR, ORG_FILE)
    if not os.path.exists(org_path):
        return None, f"Nisam našao {ORG_FILE} u 'data/'"

    try:
        org_df = pd.read_excel(org_path, sheet_name=ORG_SHEET, dtype=str)
    except Exception as e:
        return None, f"Problem pri čitanju {ORG_FILE}: {e}"

    if "CODE" not in org_df.columns:
        return None, f"U {ORG_FILE} nedostaje kolona 'CODE'."

    org_df["CODE"] = org_df["CODE"].astype(str).str.zfill(9)
    org_df = org_df.rename(columns={"CODE": "CUSTOMERID"})

    full_df = stat_df.merge(org_df, on="CUSTOMERID", how="left")

    return full_df, None

# ---------------------------------------------------------
# AUTH
# ---------------------------------------------------------

def check_password():
    """Jednostavna login forma, vraća True ako je korisnik ulogiran."""

    # ako smo već ulogirani, preskoči login formu
    if st.session_state.get("authenticated"):
        return True

    # pokušaj čitanja iz secrets; ako nema, fallback na admin/admin
    try:
        auth_conf = st.secrets["auth"]
        valid_username = auth_conf.get("username")
        valid_password = auth_conf.get("password")
    except Exception:
        valid_username = "admin"
        valid_password = "admin"

    # FORM = omogućuje Enter za submit
    with st.form("login_form", clear_on_submit=False):
        st.markdown("### 🔐 Prijava")

        username = st.text_input("Korisničko ime", key="login_username")
        password = st.text_input("Lozinka", type="password", key="login_password")
        submitted = st.form_submit_button("Prijavi se")

    if submitted:
        if username == valid_username and password == valid_password:
            st.session_state["authenticated"] = True
            st.success("Uspješna prijava.")

            # odmah napravimo rerun da nestane login blok
            try:
                st.rerun()              # noviji Streamlit
            except Exception:
                st.experimental_rerun()  # fallback za starije verzije
        else:
            st.error("Neispravno korisničko ime ili lozinka.")

    # dok se uspješno ne ulogira, vraća False
    return False

# ---------------------------------------------------------
# HEADER-I ZA DVA MODULA
# ---------------------------------------------------------

def render_header_stats():
    col_left, col_center, col_right = st.columns([1, 3, 1])

    with col_left:
        if os.path.exists(LOGO_ME):
            st.image(LOGO_ME, width="stretch")

    with col_center:
        st.markdown(
            """
            <div style="text-align: center; padding-top: 10px;">
                <div style="font-size: 28px; font-weight: 700; margin-bottom: 4px;">
                    MEVA - AH Statistika
                </div>
                <div style="font-size: 14px; color: #666;">
                    Web verzija alata za pregled i analizu upita
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_right:
        if os.path.exists(LOGO_AH):
            st.image(LOGO_AH, width="stretch")


def render_header_vin():
    col_left, col_center, col_right = st.columns([1, 3, 1])

    with col_left:
        if os.path.exists(LOGO_ME):
            st.image(LOGO_ME, width="stretch")
        else:
            st.write("")

    with col_center:
        st.markdown(
            """
            <div style="text-align: center; padding-top: 10px;">
                <div style="font-size: 28px; font-weight: 700; margin-bottom: 4px;">
                    MEVA - Pretraga VIN brojeva
                </div>
                <div style="font-size: 14px; color: #666;">
                    Web verzija alata za pregled kalkulacija po VIN broju
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_right:
        st.write("")

# ---------------------------------------------------------
# MODUL 1: AH STATISTIKA
# ---------------------------------------------------------

def run_stats_module():
    render_header_stats()
    st.markdown("---")

    all_files = list_data_files()
    if not all_files:
        st.warning(
            "U `data/` folderu nisu pronađene JSON/CSV datoteke.\n"
            "Dodaj baze u repo pa redeployaj aplikaciju."
        )
        return

    st.markdown("### Baze")

    selected_files = st.multiselect(
        "Odaberi baze koje želiš uključiti u izračun:",
        options=all_files,
        default=all_files,
    )

    if not selected_files:
        st.info("Odaberi barem jednu bazu iz liste iznad.")
        return

    data, org_names, min_date, max_date = load_log_data(tuple(selected_files))

    if not data:
        st.warning(
            "Nema podataka u `data/` folderu.\n\n"
            "Dodaj JSON/CSV datoteke (isti format kao u desktop aplikaciji) "
            "i redeployaj aplikaciju."
        )
        return

    st.markdown("### Kriteriji pretrage")

    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        org_options = ["(Sve organizacije)"] + org_names
        selected_org = st.selectbox("🏢 Naziv organizacije", org_options)

    with col2:
        default_from = min_date or date(2020, 1, 1)
        d_from = st.date_input("📅 Datum OD", value=default_from)

    with col3:
        default_to = max_date or date.today()
        d_to = st.date_input("📅 Datum DO", value=default_to)

    if d_from > d_to:
        st.error("❌ Datum OD ne može biti veći od datuma DO.")
        return

    if st.button("🔍 Prikaži rezultat", key="btn_stats"):
        org_filter = selected_org if selected_org != "(Sve organizacije)" else ""

        export_rows, per_day, top_vins = calculate_stats(data, org_filter, d_from, d_to)

        st.markdown("### Rezultat")

        st.metric("📊 Broj upita", len(export_rows))

        if not export_rows:
            st.info("Nema zapisa za zadane kriterije.")
        else:
            st.write("Prvih 200 zapisa:")
            st.dataframe(export_rows[:200], width="stretch")

            excel_bytes = make_excel_bytes(export_rows)

            file_name_org = (
                org_filter.replace(" d.d.", "")
                .replace(" d.d", "")
                .replace(" ", "_")
                .replace(".", "")
            )
            if file_name_org:
                file_name = f"AH_{file_name_org}.xlsx"
            else:
                file_name = "AH_SVE_ORGANIZACIJE.xlsx"

            st.download_button(
                label="📥 Preuzmi Excel",
                data=excel_bytes,
                file_name=file_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_stats",
            )

            st.success("Export je spreman za preuzimanje.")

            # GRAFOVI
            st.markdown("### Grafovi")

            if not per_day and not top_vins:
                st.info("Nema podataka za prikaz grafova.")
            else:
                fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 7))
                plt.tight_layout(pad=3.0)

                # 1) po danu / mjesecu
                if per_day:
                    if len(per_day) <= 31:
                        dates_sorted = sorted(per_day.keys())
                        x_labels = [d.strftime("%d.%m.") for d in dates_sorted]
                        y_values = [per_day[d] for d in dates_sorted]
                        x_pos = range(len(x_labels))
                        ax1.bar(x_pos, y_values)
                        ax1.set_title("Broj upita po danu")
                    else:
                        per_month = Counter()
                        for d, cnt in per_day.items():
                            key = (d.year, d.month)
                            per_month[key] += cnt

                        months_sorted = sorted(per_month.keys())
                        x_labels = [f"{m:02d}.{y}" for (y, m) in months_sorted]
                        y_values = [per_month[k] for k in months_sorted]
                        x_pos = range(len(x_labels))
                        ax1.bar(x_pos, y_values)
                        ax1.set_title("Broj upita po mjesecu")

                    ax1.set_ylabel("Broj upita")
                    ax1.set_xticks(list(x_pos))
                    ax1.set_xticklabels(x_labels, rotation=45, ha="right", fontsize=8)

                    ymax = max(y_values) if y_values else 0
                    if ymax > 0:
                        ax1.set_ylim(0, ymax * 1.15)
                        for i, val in enumerate(y_values):
                            ax1.text(i, val, str(val), ha="center", va="bottom", fontsize=8)
                else:
                    ax1.text(0.5, 0.5, "Nema podataka", ha="center", va="center")
                    ax1.axis("off")

                # 2) top VIN-ovi
                if top_vins:
                    vins, counts = zip(*top_vins)
                    positions = range(len(vins))
                    ax2.barh(positions, counts)
                    ax2.set_yticks(list(positions))
                    ax2.set_yticklabels(vins, fontsize=8)
                    ax2.invert_yaxis()
                    ax2.set_xlabel("Broj upita")
                    ax2.set_title("Top 5 najčešće provjeravanih VIN-ova")

                    max_count = max(counts)
                    offset = max_count * 0.02
                    for i, val in enumerate(counts):
                        ax2.text(val + offset, i, str(val), va="center", fontsize=8)
                else:
                    ax2.text(0.5, 0.5, "Nema podataka", ha="center", va="center")
                    ax2.axis("off")

                st.pyplot(fig)
    else:
        st.info("Odaberi kriterije i klikni **'Prikaži rezultat'**.")

# ---------------------------------------------------------
# MODUL 2: PRETRAGA VIN BROJEVA
# ---------------------------------------------------------

def run_vin_module():
    render_header_vin()
    st.markdown("---")

    # CSS za search traku
    st.markdown(
        """
        <style>
        div[data-testid="stHorizontalBlock"] div[data-testid="column"]:first-child input[type="text"] {
            max-width: 320px;
            height: 38px;
            font-size: 14px;
        }
        div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-child(2) button {
            background-color: #006400;
            color: whitesmoke;
            border: 1px solid #006400;
            width: 130px;
            height: 38px;
            border-radius: 6px;
        }
        div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-child(2) button:hover {
            background-color: whitesmoke;
            color: #006400;
            border: 1px solid #006400;
        }
        div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-child(3) button {
            background-color: #ff6666;
            color: whitesmoke;
            border: 1px solid #ff6666;
            width: 130px;
            height: 38px;
            border-radius: 6px;
        }
        div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-child(3) button:hover {
            background-color: whitesmoke;
            color: #ff6666;
            border: 1px solid #ff6666;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    df, err = load_stat_data()
    if err:
        st.error(err)
        return

    if df is None or df.empty:
        st.warning("Nema podataka za prikaz.")
        return

    st.markdown("### 🔎 Pretraga po VIN broju")

    if "vin_input" not in st.session_state:
        st.session_state.vin_input = ""

    col1, col2, col3 = st.columns([6, 1.5, 1.5])

    with col1:
        vin = st.text_input(
            "Unesi VIN (točan match):",
            value=st.session_state.vin_input,
            max_chars=50,
            key="vin_input",
        )

    enter_trigger = False
    if not st.session_state.get("last_vin", "") == st.session_state.vin_input:
        enter_trigger = True
    st.session_state.last_vin = st.session_state.vin_input

    with col2:
        search_clicked = st.button("🔍 Pretraži", width="stretch", key="search_vin")

    with col3:
        clear_clicked = st.button("🧹 Očisti", width="stretch", key="clear_vin")

    if clear_clicked:
        st.session_state.vin_input = ""
        st.experimental_rerun()

    if (search_clicked or enter_trigger) and vin.strip():
        vin_query = vin.strip().upper()

        if "VINNUMBER" not in df.columns:
            st.error("U podacima ne postoji kolona 'VINNUMBER'.")
            return

        mask = df["VINNUMBER"].fillna("").str.upper() == vin_query
        results = df[mask].copy()

        if results.empty:
            st.info(f"Nema rezultata za VIN: **{vin_query}**")
            return

        sort_cols = []
        if "YEAR" in results.columns:
            sort_cols.append("YEAR")
        if "TSTAMP" in results.columns:
            sort_cols.append("TSTAMP")

        if sort_cols:
            results = results.sort_values(sort_cols)

        st.markdown(f"### Rezultati za VIN: `{vin_query}`")
        st.metric("Broj pronađenih zapisa", len(results))

        if "YEAR" in results.columns:
            years = list(results["YEAR"].dropna().unique())
            years.sort()

            for year in years:
                sub = results[results["YEAR"] == year].copy()
                st.markdown(f"#### Godina {year}")

                if "YEAR" in sub.columns:
                    sub = sub.drop(columns=["YEAR"])

                st.dataframe(sub, width="stretch")
        else:
            st.dataframe(results, width="stretch")
    else:
        st.info("Unesi VIN broj i klikni **Pretraži**.")

# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():
    # ---------------- AUTH ----------------
    if not check_password():
        st.stop()

    # ---------------- SIDEBAR - ODABIR MODULA ----------------
    st.sidebar.markdown("### Odaberi modul")

    module = st.sidebar.radio(
        "Odaberi modul",  # neka normalna labela
        ("MEVA - AH Statistika", "MEVA - Pretraga VIN brojeva"),
        key="selected_module",
        label_visibility="collapsed",  # ako ne želiš da se labela vidi
    )

    # kratak opis ispod radio gumba
    if module == "MEVA - AH Statistika":
        st.sidebar.markdown(
            """
            **AH Statistika**

            - analiza broja upita  
            - filtriranje po organizaciji i datumu  
            - Excel export i grafovi
            """
        )
    else:
        st.sidebar.markdown(
            """
            **Pretraga VIN brojeva**

            - traženje kalkulacija po VIN-u  
            - pregled po godinama  
            - detaljan prikaz iz statističkih CSV-ova
            """
        )

    # ---------------- POZIV ODGOVARAJUĆEG MODULA ----------------
    if module == "MEVA - AH Statistika":
        run_stats_module()
    else:
        run_vin_module()

if __name__ == "__main__":
    main()
