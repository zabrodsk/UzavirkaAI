<p align="center">
  <img src="assets/readme-hero.svg" alt="Uzavirka AI branded project banner" width="100%">
</p>

# Uzavirka AI

<p align="center">
  <img alt="AI Olympiad 1st place" src="https://img.shields.io/badge/AI%20Olympiad-1st%20place-D9381E?style=for-the-badge&labelColor=0F0F0F">
  <img alt="B2G SaaS" src="https://img.shields.io/badge/B2G-SaaS-F1EEE7?style=for-the-badge&labelColor=D9381E&color=F1EEE7">
  <img alt="Pilot Mlada Boleslav" src="https://img.shields.io/badge/Pilot-Mlada%20Boleslav-77746F?style=for-the-badge&labelColor=0F0F0F">
  <img alt="Streamlit demo" src="https://img.shields.io/badge/Demo-Streamlit-D9381E?style=for-the-badge&labelColor=0F0F0F">
</p>

<h3 align="center">From permit guesswork to risk-ranked closure decisions in 60 seconds.</h3>

<p align="center">
  <strong>Pick the closure.</strong>
  Score the risk.
  See the reason.
  Move it to a better window.
  Show the detour before the city feels it.
</p>

<p align="center">
  Built for <strong>Stredocesky kraj</strong>.
  Piloted on <strong>Mlada Boleslav</strong>.
  Designed for every ORP city approving roadworks, repairs, and temporary closures.
</p>

<p align="center">
  <a href="assets/uzavirka-ai-demo.mp4"><img alt="Watch demo" src="https://img.shields.io/badge/Watch%20demo-0F0F0F?style=for-the-badge"></a>
  <a href="assets/uzavirka-ai-pitchdeck.pdf"><img alt="Open pitch deck" src="https://img.shields.io/badge/Open%20pitch%20deck-D9381E?style=for-the-badge"></a>
  <a href="TECHNICAL_OVERVIEW.md"><img alt="Technical overview" src="https://img.shields.io/badge/Technical%20overview-77746F?style=for-the-badge"></a>
  <a href="https://www.aiolympiada.cz/krajska-kola"><img alt="Competition page" src="https://img.shields.io/badge/Competition%20page-F1EEE7?style=for-the-badge&labelColor=D9381E&color=F1EEE7"></a>
</p>

## One Closure In, A Decision Out

<table>
  <tr>
    <td width="33%" valign="top">
      <strong>01 / Input</strong><br><br>
      City, road, planned hour, duration, closure type, bus impact, and two map clicks for START and END.
    </td>
    <td width="33%" valign="top">
      <strong>02 / AI Check</strong><br><br>
      Traffic vulnerability score, safety pressure, weak alternatives, external context, and route-graph detour impact.
    </td>
    <td width="33%" valign="top">
      <strong>03 / Decision</strong><br><br>
      Approve, approve with mitigation, reschedule, or reject until the plan changes.
    </td>
  </tr>
</table>

```text
bad closure window -> delayed buses + wasted commuter hours + angry calls
Uzavirka AI        -> score + reasons + better time + visible detour
```

## Why This Is Not Another Map App

Most navigation tools help drivers after a closure exists. Uzavirka AI helps the city before the closure is approved.

| Google Maps answers | Uzavirka AI answers |
| --- | --- |
| How should this driver reroute now? | Should the municipality approve this closure at this time? |
| What is the fastest route today? | Which closure window creates the least public cost? |
| What does traffic look like? | Which risk drivers make this permit dangerous? |
| Individual trip optimization | Municipal approval decision support |

## The Hackathon Wedge

The product is intentionally narrow enough to demo and real enough to buy:

| Constraint | Choice |
| --- | --- |
| First buyer | Municipal transport department / ORP city |
| First geography | Mlada Boleslav, Stredocesky kraj |
| First repeated workflow | Planned closure approval |
| First AI value | Explainable risk score and better-window recommendation |
| First visual proof | OSM-backed START/END closure picker and recomputed detour |

## The Product

Uzavirka AI turns one planned closure into a clear operating decision.

| Input | AI-assisted output |
| --- | --- |
| City, road segment, day, start hour, duration, closure type | Risk score from `0` to `100` |
| Bus impact and local traffic context | Ranked reasons behind the score |
| START and END clicks on the map | Closed segment, detour path, added travel time |
| Current planned window | Safer alternative time windows |
| Available data quality | Confidence and manual-review warning |

The app does not automatically approve or reject permits. It gives officials an explainable recommendation they can challenge.

## Demo

[![Uzavirka AI demo thumbnail](assets/demo-thumbnail.jpg)](assets/uzavirka-ai-demo.mp4)

| Asset | Link |
| --- | --- |
| Product demo video | [`assets/uzavirka-ai-demo.mp4`](assets/uzavirka-ai-demo.mp4) |
| Competition pitch deck | [`assets/uzavirka-ai-pitchdeck.pdf`](assets/uzavirka-ai-pitchdeck.pdf) |
| Technical write-up | [`TECHNICAL_OVERVIEW.md`](TECHNICAL_OVERVIEW.md) |
| Technical PDF | [`TECHNICAL_OVERVIEW.pdf`](TECHNICAL_OVERVIEW.pdf) |

## Why It Won

| Judging angle | What Uzavirka AI shows |
| --- | --- |
| Real regional problem | Municipal closures are a recurring approval workflow, not a one-off app idea. |
| Concrete buyer | ORP cities, municipal transport departments, and silnicni spravni urady. |
| Visible AI value | Transparent risk scoring, explanation, alternative-window ranking, and route-impact simulation. |
| Practical demo | Mlada Boleslav OSM map picker with START/END snapping and recomputed detour. |
| Ethical boundary | Advisory output, confidence notes, aggregate data, and manual review for weak data. |
| Business path | B2G SaaS with setup fees for local data integration and regional reporting. |

## How It Works

```mermaid
flowchart TD
    A["Olympiad CSV traffic data"] --> B["Clean and merge"]
    C["Municipality context"] --> B
    D["OSM road geometry"] --> E["Local road graph"]
    F["Optional regional traffic ZIPs"] --> G["External context"]
    B --> H["Risk model"]
    E --> H
    G --> H
    H --> I["Score, class, reasons"]
    E --> J["Detour path and added delay"]
    I --> K["Streamlit decision console"]
    J --> K
```

## Risk Model

The MVP uses a transparent traffic-vulnerability model. Each score is explainable and bounded.

Risk drivers:

- vehicle count
- traffic flow index
- average speed versus free speed
- morning or afternoon peak hour
- collision-risk index
- public transport alternatives
- P+R capacity
- closure duration
- closure type multiplier
- bus route impact
- selected map detour impact, capped at 10 points
- optional external traffic context, capped at 5 points

| Score | Class | Recommendation |
| ---: | --- | --- |
| `0-30` | LOW | Approve |
| `31-60` | MEDIUM | Approve with mitigation |
| `61-80` | HIGH | Reschedule or require strong mitigation |
| `81-100` | CRITICAL | Do not approve without major changes |

## Route Simulation

For the Mlada Boleslav demo, the app uses cached OpenStreetMap road geometry:

1. Officer clicks START on the map.
2. Officer clicks END on the same road.
3. Python snaps both clicks to the nearest road coordinate.
4. The selected road section is removed from a local `networkx` graph.
5. The app recomputes the shortest available detour.
6. Added time and distance feed back into the risk score and delay forecast.

Map language:

- **red** = closed road section
- **orange** = affected graph edges
- **green** = recomputed detour

## Data

Main AI Olympiad data:

- `01_provoz_useky_gps.csv`
- `02_obce_kontext.csv`
- `03_simpleml_komplet.csv`

Optional external context:

- `DOPR_D_YYYYMMDD.zip` regional traffic files
- cached Mlada Boleslav OSM road geometries in `data/mlada_boleslav_osm_roads.json`

Production upgrade path:

- NDIC / DATEX incidents, restrictions, and roadworks
- PID GTFS public transport alternatives
- municipal closure history and approval outcomes
- school calendars, large employer shift timing, and local events
- post-closure feedback and calibration

## Stack

| Layer | Tools |
| --- | --- |
| UI | Streamlit, folium, streamlit-folium, pydeck |
| Data | pandas, robust CSV normalization |
| Routing | networkx, OSM road graph |
| Model | transparent weighted scoring plus bounded context adjustments |
| Tests | pytest / unittest |

## Quickstart

```bash
pip install -r requirements.txt
streamlit run app.py
```

Run tests:

```bash
pytest
```

## Repository Map

```text
app.py                         Streamlit decision console
data_loading.py                CSV loading, normalization, validation
risk_model.py                  Risk score, recommendations, delay forecast
route_analysis.py              Synthetic and OSM-backed route simulation
osm_roads.py                   Mlada Boleslav OSM fetch/cache logic
external_data.py               Optional traffic ZIP parser
tests/                         Unit tests
assets/                        Brand assets, pitch deck, demo video
TECHNICAL_OVERVIEW.md          Detailed technical write-up
```

## Limits

This is a hackathon MVP, not production permitting infrastructure. It does not yet include historical closure-outcome labels, calibrated prediction intervals, live traffic ingestion, full turn restrictions, or a validated city-scale traffic model. Its role is to prove a sharper workflow: make the risk visible before the closure is approved.

## Business Model

Uzavirka AI is best sold as B2G SaaS for ORP cities and municipal transport departments, with setup fees for local data integration and optional regional reporting. The value is fewer badly timed closures, lower social delay costs, better bus reliability, and a reusable evidence base for transport planning.
