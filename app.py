from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from data_loading import ProjectDataset, load_project_data
from risk_model import recommend_better_windows, score_closure


APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
DAYS = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]
CLOSURE_TYPES = ["partial_lane_closure", "full_road_closure", "detour"]
CLOSURE_TYPE_LABELS = {
    "partial_lane_closure": "Částečná uzavírka pruhu",
    "full_road_closure": "Úplná uzavírka silnice",
    "detour": "Objízdná trasa",
}
RISK_CLASS_LABELS = {
    "LOW": "Nízké",
    "MEDIUM": "Střední",
    "HIGH": "Vysoké",
    "CRITICAL": "Kritické",
}
CITY_AVERAGE = "Průměr města"


@st.cache_data(show_spinner=False)
def cached_data() -> ProjectDataset:
    data_dir = DATA_DIR if DATA_DIR.exists() else APP_DIR
    return load_project_data(data_dir)


def format_czk(value: float) -> str:
    return f"{value:,.0f} CZK".replace(",", " ")


def display_risk_class(risk_class: str) -> str:
    return f"{RISK_CLASS_LABELS.get(risk_class, risk_class)} ({risk_class})"


def choose_row(data: pd.DataFrame, city: str, usek_id: str | None, day: str, hour: int) -> pd.Series:
    candidates = data[data["obec"].astype(str) == str(city)].copy()
    if usek_id and usek_id != CITY_AVERAGE and "usek_id" in candidates.columns:
        narrowed = candidates[candidates["usek_id"].astype(str) == str(usek_id)]
        if not narrowed.empty:
            candidates = narrowed

    if "den_v_tydnu" in candidates.columns:
        same_day = candidates[candidates["den_v_tydnu"].astype(str) == str(day)]
        if not same_day.empty:
            candidates = same_day

    if candidates.empty:
        return data.iloc[0]

    candidates["hour_distance"] = (candidates["hodina"].astype(float) - hour).abs()
    return candidates.sort_values(["hour_distance", "hodina"]).iloc[0]


def main() -> None:
    st.set_page_config(page_title="Uzavírka AI", page_icon=":construction:", layout="wide")
    dataset = cached_data()
    data = dataset.data

    st.title("Uzavírka AI")
    st.caption(
        "MVP pro podporu rozhodování při schvalování uzavírek. "
        "Nehraje si na plnou dopravní simulaci; hodnotí riziko podle zranitelnosti konkrétního úseku."
    )

    with st.sidebar:
        st.header("Plánovaná uzavírka")
        cities = sorted(data["obec"].dropna().astype(str).unique())
        default_city = "Mladá Boleslav" if "Mladá Boleslav" in cities else cities[0]
        city = st.selectbox("Město / obec", cities, index=cities.index(default_city))

        city_rows = data[data["obec"].astype(str) == city]
        usek_options = [CITY_AVERAGE]
        if "usek_id" in city_rows.columns:
            usek_options += sorted(city_rows["usek_id"].dropna().astype(str).unique())
        usek_id = st.selectbox("Silniční úsek / usek_id", usek_options)

        day = st.selectbox("Den v týdnu", DAYS, index=0)
        start_hour = st.slider("Plánovaný začátek", 0, 23, 10)
        duration_hours = st.slider("Délka uzavírky v hodinách", 0.5, 12.0, 3.0, 0.5)
        closure_type_label = st.selectbox("Typ uzavírky", list(CLOSURE_TYPE_LABELS.values()))
        closure_type = next(key for key, value in CLOSURE_TYPE_LABELS.items() if value == closure_type_label)
        affects_bus_route = st.checkbox("Ovlivní autobusovou linku", value=False)
        value_of_time = st.number_input("Hodnota času (Kč/hod)", min_value=50, max_value=1000, value=200, step=25)

    selected_row = choose_row(data, city, usek_id, day, start_hour)
    selected_row = selected_row.copy()
    selected_row["hodina"] = start_hour
    selected_row["den_v_tydnu"] = day

    if usek_id == CITY_AVERAGE:
        comparable = city_rows
        if "den_v_tydnu" in comparable.columns:
            same_day = comparable[comparable["den_v_tydnu"].astype(str) == str(day)]
            if not same_day.empty:
                comparable = same_day
        same_hour = comparable[comparable["hodina"].astype(int) == int(start_hour)]
        if not same_hour.empty:
            numeric = same_hour.select_dtypes(include="number").median()
            for col, value in numeric.items():
                selected_row[col] = value

    result = score_closure(
        selected_row,
        duration_hours=duration_hours,
        closure_type=closure_type,
        affects_bus_route=affects_bus_route,
        value_of_time_czk_h=value_of_time,
    )

    score_col, class_col, rec_col, confidence_col = st.columns(4)
    score_col.metric("Rizikové skóre", f"{result.score}/100")
    class_col.metric("Třída rizika", display_risk_class(result.risk_class))
    rec_col.metric("Doporučení", result.recommendation)
    confidence_col.metric("Spolehlivost", f"{result.confidence:.0%}")

    st.subheader("Proč bylo riziko přiděleno")
    reasons = pd.DataFrame(
        [{"Důvod": reason.name, "Příspěvek": reason.contribution, "Vysvětlení": reason.explanation} for reason in result.reasons]
    )
    st.bar_chart(reasons.set_index("Důvod")["Příspěvek"])
    st.dataframe(reasons, hide_index=True, width="stretch")

    baseline_col, model_col = st.columns(2)
    baseline_col.subheader("Baseline")
    baseline_col.metric("Třída podle špičky", display_risk_class(result.baseline_class))
    baseline_col.write("Baseline rozlišuje jen to, zda uzavírka začíná v dopravní špičce.")
    model_col.subheader("ClosureImpact")
    model_col.metric("Třída podle modelu", display_risk_class(result.risk_class))
    model_col.write(
        "ClosureImpact navíc využívá plynulost konkrétního úseku, počet vozidel, rychlost, "
        "bezpečnostní riziko, délku uzavírky, typ uzavírky a dostupné dopravní alternativy."
    )

    st.subheader("Lepší časová okna")
    windows = recommend_better_windows(
        data,
        city=city,
        usek_id=None if usek_id == CITY_AVERAGE else usek_id,
        day=day,
        duration_hours=duration_hours,
        closure_type=closure_type,
        affects_bus_route=affects_bus_route,
        limit=3,
    )
    if windows:
        st.dataframe(
            pd.DataFrame(windows).rename(
                columns={
                    "start_hour": "Začátek",
                    "end_hour": "Konec",
                    "score": "Rizikové skóre",
                    "risk_class": "Třída rizika",
                }
            ).assign(**{"Třída rizika": lambda frame: frame["Třída rizika"].map(display_risk_class)}),
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("V aktuálních datech není pro vybrané město nebo úsek dostupné srovnatelné méně rizikové okno.")

    st.subheader("Odhad ekonomického dopadu")
    roi_col_1, roi_col_2, roi_col_3 = st.columns(3)
    roi_col_1.metric("Dotčené osoby", f"{result.roi['affected_people']:,.0f}".replace(",", " "))
    roi_col_2.metric("Odhad společenské ztráty", format_czk(result.roi["estimated_social_loss_czk"]))
    roi_col_3.metric("Možná úspora při snížení rizika o 30 %", format_czk(result.roi["possible_savings_30pct_czk"]))
    st.caption(
        "Vzorec: dotčené osoby = počet vozidel × délka uzavírky × 1,2 cestujícího. "
        "Zpoždění v minutách závisí na třídě rizika; výchozí hodnota času je 200 Kč/hod."
    )

    with st.expander("Report kvality dat"):
        st.write(f"Načteno {len(data)} řádků ze souborů: {', '.join(dataset.source_files)}")
        report_rows = []
        for filename, report in dataset.reports.items():
            report_rows.append(
                {
                    "Soubor": filename,
                    "Řádky": report["rows"],
                    "Sloupce": len(report["columns"]),
                    "Kódování": report["encoding"],
                    "Doplněné číselné hodnoty": report["numeric_filled"],
                    "Opravené extrémní hodnoty": report["clipped_values"],
                }
            )
        st.dataframe(pd.DataFrame(report_rows), hide_index=True, width="stretch")

    st.subheader("Etická poznámka")
    st.write(
        "Toto MVP používá pouze agregovaná dopravní a obecní kontextová data. Nesleduje jednotlivce. "
        "AI skóre je poradní, zobrazuje spolehlivost odhadu a případy s nedostatkem dat mají projít ruční kontrolou úředníka."
    )


if __name__ == "__main__":
    main()
