# Munich eVTOL Airspace Digital Twin

Hackathon prototype for graph-based eVTOL routing in Munich-inspired airspace.

A deterministic simulation and digital-twin platform modeling a 30-aircraft 
eVTOL fleet (autonomous pad-to-pad transits + hospital medical transfers) 
operating over a Munich-inspired airspace graph. Includes dynamic corridor 
congestion, weather rerouting, battery-based emergency diversion logic, 
hospital dispatch prioritization, and a React dashboard consuming live 
simulation data from a FastAPI backend.

## Setup

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run the current smoke demo

```powershell
python -m backend.test_graph
```

The command prints network and route information, refreshes
`backend/world.json`, and writes the interactive map to
`backend/munich_airspace_map.html`.

## Run the single eVTOL replay

```powershell
python -m backend.single_scenario
```

This produces `backend/single_evtol_scenario_map.html`. Every run selects a
random occupied departure pad and a different available destination pad. Open
it in a browser to watch E1 fly the highlighted route, with replay controls in
the bottom-right corner.

## Run the three-eVTOL fleet replay

```powershell
python -m backend.fleet_scenario
```

This produces `backend/fleet_evtol_scenario_map.html`. It creates three
concurrent pad-to-pad flights and updates traffic/noise every 10 simulated
seconds. Aircraft exchange local position, route-intent, edge-condition, and
landing-intent messages; a shared reservation bulletin board gates edge entry
and landing windows.
Routes under 10 km use the inbound profile (300 m, 100 km/h); longer routes use
the outbound profile (600 m, 140 km/h). The replay panel shows each aircraft's
speed, altitude, status, neighbor count, current corridor traffic/noise, active
message count, and reservation count.

## Run the 30-eVTOL operational scenario

```powershell
python -m backend.operational_scenario
```

This is the scaled deterministic scenario used by the React dashboard. All 30
eVTOLs have a defined origin and destination: 26 autonomous pad-to-pad
transits and four hospital-to-hospital medical transfers carrying either an
organ-preservation container or an emergency medical kit. It includes
corridor-slot congestion and a weather closure with rerouting. A critical
battery fault diverts one autonomous transit to a charger, then resumes its
original pad-to-pad mission. Medical transfers remain independent hospital
deliveries. A controllable technical failure affects a different non-medical
aircraft and diverts it to the nearest charging/maintenance station, where it
is grounded and its original mission is aborted.

## Run the C4/C5 safety-decision demo

```powershell
python -m backend.emergency_scenario
```

This deterministic console scenario demonstrates the two required emergency
paths and dynamic rerouting:

- Below 30% battery, an eVTOL diverts to the lowest-cost reachable charging
  hub that leaves the documented energy reserve.
- At or below 15% battery, the charging diversion uses emergency status,
  altitude, speed, and the existing emergency reservation priority.
- A hospital alert selects the fastest healthy eVTOL at a safe node that can
  reach the hospital with reserve battery, then broadcasts its emergency route
  to local neighbors.
- A blocked corridor is excluded from routing. High weather risk and material
  dynamic-cost improvements can also cause a reroute at the next safe node.

The normal three-aircraft Folium replay is deliberately unchanged in this
phase. It remains the C0-C3 communication/reservation demo; wiring scripted
emergency events into its map replay is a later dashboard step.

## React control-center dashboard

The React dashboard now consumes the same 30-aircraft operational replay used by the
Python simulation. The backend is the only live data source; the old
`frontend/src/data/worldData.ts` values are retained solely as an offline UI
fallback when the API is unavailable.

Run these in separate PowerShell terminals from the repository root:

```powershell
python -m backend.api_server
```

```powershell
cd frontend
npm run dev
```

Open the Vite URL shown in the second terminal. Vite proxies `/api` to the
Python service at `http://127.0.0.1:8000`. The dashboard polls once per second
and loops through the deterministic 30-eVTOL replay. For a fixed tick during
debugging, open `http://127.0.0.1:8000/api/simulation?tick=0`.

Each API snapshot includes the 21 digital-twin nodes, 33 dynamic corridor
records, node occupancy, 30 agent positions/statuses/battery/altitude/speed,
medical cargo and incident state, route intent, local communication and
constraint views, decision reasons, alerts, weather-zone state, and
protocol/reservation metrics.

## Project layout

- `backend/planner.py` — dynamic weighted route planner and cost breakdowns.
- `backend/emergency.py` — battery, charging, hospital-dispatch, and rerouting decisions.
- `backend/emergency_scenario.py` — repeatable C4/C5 console demonstration.

- `backend/graph.py` — static graph model, routing helper, JSON export, and
  Folium map generation.
- `backend/models.py` — eVTOL state, health, speed, and altitude-level models.
- `backend/communication.py` — local messages, TTL, and neighbor discovery.
- `backend/reservations.py` — deterministic edge and landing-window bulletin board.
- `backend/fleet_scenario.py` — concurrent three-eVTOL tick simulation.
- `backend/fleet_ui.py` — animated Folium fleet replay.
- `backend/test_graph.py` — manual smoke demo for the current static layer.
- `backend/world.json` — generated graph snapshot.
