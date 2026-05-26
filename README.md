# Uzavírka AI

Uzavírka AI is a local MVP for municipal road-closure decision support in Středočeský kraj. It estimates the risk of a planned road or lane closure, builds a small local road graph around the selected segment, and shows the likely direction of detour pressure. It is a decision-support simulator for officials, not a production navigation engine or a validated full-city traffic model.

## Target Customer

The target users are municipal transport departments, silniční správní úřady, and ORP cities. The first pilot scenario is Mladá Boleslav, with expansion potential to Kladno, Kolín, Příbram, Beroun, Mělník, and other medium-sized cities in Středočeský kraj.

## Data Sources

The MVP uses the AI Olympiad CSV files as the main dataset:

- `01_provoz_useky_gps.csv`
- `02_obce_kontext.csv`
- `03_simpleml_komplet.csv`

The app first looks for these files in `data/`. If that folder is not present, it falls back to the project root, which matches the current GitHub repository layout.

The Dopravní portál Středočeského kraje publishes open traffic data in machine-readable format. The downloaded `DOPR_D_YYYYMMDD.zip` files are treated as optional external validation context and are not required for the MVP. In production, PID open data could provide GTFS/public transport data, and NDIC/DATEX could provide roadworks, closures, incidents, and traffic events.

## How To Run

Install dependencies, then start the Streamlit app:

```powershell
pip install -r requirements.txt
streamlit run app.py
```

If you use the bundled Codex Python runtime, replace `pip` with that environment's pip command.

## What The App Does

The officer enters:

- city / obec
- road segment / `usek_id` if available
- day of week
- planned start hour
- duration
- closure type
- whether a bus route is affected

The app returns a risk score from 0 to 100, a risk class, a recommendation, ranked reasons, baseline comparison, better time windows, a simple ROI estimate, confidence, network impact metrics, likely detour paths, and an ethics note.

The map route picker is interactive: the user clicks two snap points on a full map preview. The app treats those clicks as snapped points on the nearest local graph segment. If both points resolve to the same segment, that segment is used for prediction. The red layer is the closed segment, orange shows affected graph edges, and green shows the recomputed detour path. Current route geometries are deterministic local demo geometries built from the available `usek_id` rows and city coordinates; production should replace them with real road geometry and true GIS snapping.

## Network Impact

For the selected closure, the app compares the baseline shortest path with a recomputed path after removing the closed edge from a local `networkx` graph. It reports affected edges, added distance, added travel time, and the share of sampled routes that become unreachable. Edge weights use distance plus available speed, flow, and vehicle-volume indicators.

## Risk Score

The MVP uses transparent traffic-vulnerability scoring. Components include:

- higher vehicle count
- lower flow index
- lower average speed
- morning or afternoon peak hour
- higher collision-risk index
- weaker public transport alternatives
- weaker P+R capacity
- longer closure duration
- closure type multiplier
- bus route impact

Risk classes:

- `0-30 LOW`: approve
- `31-60 MEDIUM`: approve with mitigation
- `61-80 HIGH`: reschedule or require strong mitigation
- `81-100 CRITICAL`: do not approve without major changes

## Baseline

The baseline is intentionally simple: peak hour means high risk, shoulder hour means medium risk, and off-peak means low risk. ClosureImpact is better for this use case because it also uses segment-specific flow, vehicle count, speed, safety risk, duration, closure type, and mobility alternatives.

## Better Time Windows

For the same selected city or road segment, the app scans available rows and suggests the 2-3 lower-risk time windows. When scores are similar, workday off-peak hours around 10:00-13:00 are preferred.

## ROI

The MVP estimates social loss with:

```text
affected_people = vehicle_count * duration_hours * 1.2 passengers
social_loss = affected_people * delay_minutes * value_of_time
```

Delay minutes are assumed from risk class: LOW 2, MEDIUM 5, HIGH 10, CRITICAL 20. The default value of time is 200 CZK/hour. The app also shows possible savings if risk is reduced by 30%.

## Limitations

This MVP does not have real historical closure-outcome labels. It does not claim exact route diversion, precise travel-time impact, or full city simulation. The current model is a transparent risk score built from traffic vulnerability indicators. Low-data or unusual cases should be manually reviewed.

## Production Data Plan

A production version should retrain and validate against historical closure outcomes from municipal and regional systems, NDIC/DATEX roadworks and incidents, Dopravní portál Středočeského kraje feeds, PID GTFS/public transport data, school calendars, major employer shift timing, and citizen feedback after closures.

## Ethics

The MVP uses aggregate data only and avoids individual tracking. The AI is advisory, not an automatic approval system. The app shows confidence and explanations so officers can challenge the result. Data gaps should trigger manual review rather than blind automation.

## Business Model

The likely model is a B2G SaaS subscription for ORP cities and municipal transport departments, with setup fees for data integration and optional regional reporting. Value comes from fewer badly timed closures, lower social delay costs, better coordination with public transport, and reusable data collection over time.
