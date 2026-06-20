import json
from math import radians, sin, cos, sqrt, atan2
from pathlib import Path

import folium
import networkx as nx
from branca.element import MacroElement, Template
from folium.plugins import Fullscreen, MiniMap, MeasureControl


BATTERY_CONSUMPTION_RATE = 0.18
TRAFFIC_PENALTY_PER_AIRCRAFT = 2.0


def calculate_air_distance_km(lat1, lon1, lat2, lon2):
    """
    Calculate straight-line air distance between two latitude/longitude points.
    Suitable for simplified eVTOL air corridors.
    """
    earth_radius_km = 6371.0

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)

    lat1 = radians(lat1)
    lat2 = radians(lat2)

    a = (
        sin(dlat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    )

    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return round(earth_radius_km * c, 2)


class AirNode:
    def __init__(
        self,
        node_id,
        name,
        node_type,
        lat,
        lon,
        description="",
        capacity=1,
        priority_level=1,
        zone_type="mixed",
        demand_score=10,
        weather_zone="central",
        current_load=0,
    ):
        self.id = node_id
        self.name = name
        self.node_type = node_type
        self.lat = lat
        self.lon = lon
        self.description = description

        self.capacity = capacity
        self.current_load = current_load
        self.priority_level = priority_level
        self.zone_type = zone_type
        self.demand_score = demand_score
        self.weather_zone = weather_zone

    @property
    def available_slots(self):
        return max(self.capacity - self.current_load, 0)

    @property
    def availability_status(self):
        if self.available_slots <= 0:
            return "full"
        if self.available_slots <= 2:
            return "busy"
        return "available"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "type": self.node_type,
            "lat": self.lat,
            "lon": self.lon,
            "description": self.description,
            "capacity": self.capacity,
            "current_load": self.current_load,
            "available_slots": self.available_slots,
            "availability_status": self.availability_status,
            "priority_level": self.priority_level,
            "zone_type": self.zone_type,
            "demand_score": self.demand_score,
            "weather_zone": self.weather_zone,
        }


class AirRoute:
    def __init__(
        self,
        start,
        end,
        route_type="standard",
        distance=None,
        noise_penalty=0,
        weather_penalty=0,
        current_aircraft_count=0,
    ):
        self.start = start
        self.end = end
        self.route_type = route_type
        self.distance = distance

        self.noise_penalty = noise_penalty
        self.weather_penalty = weather_penalty
        self.current_aircraft_count = current_aircraft_count

        self.battery_cost = None
        self.traffic_penalty = None
        self.total_cost = None

    def recalculate_costs(self):
        if self.distance is None:
            raise ValueError("Distance must be calculated before route cost.")

        self.battery_cost = round(self.distance * BATTERY_CONSUMPTION_RATE, 2)
        self.traffic_penalty = round(
            self.current_aircraft_count * TRAFFIC_PENALTY_PER_AIRCRAFT,
            2,
        )

        self.total_cost = round(
            self.distance
            + self.battery_cost
            + self.noise_penalty
            + self.weather_penalty
            + self.traffic_penalty,
            2,
        )

    def to_dict(self):
        return {
            "start": self.start,
            "end": self.end,
            "route_type": self.route_type,
            "distance_km": self.distance,
            "battery_consumption_rate": BATTERY_CONSUMPTION_RATE,
            "battery_cost": self.battery_cost,
            "noise_penalty": self.noise_penalty,
            "weather_penalty": self.weather_penalty,
            "current_aircraft_count": self.current_aircraft_count,
            "traffic_penalty_per_aircraft": TRAFFIC_PENALTY_PER_AIRCRAFT,
            "traffic_penalty": self.traffic_penalty,
            "total_cost": self.total_cost,
        }


class RestrictMapBounds(MacroElement):
    """
    Restricts map panning to the Munich + Airport operating region.
    """

    def __init__(self, bounds):
        super().__init__()
        self._name = "RestrictMapBounds"
        self.bounds = bounds

        self._template = Template(
            """
            {% macro script(this, kwargs) %}
                var bounds = L.latLngBounds(
                    [{{ this.bounds[0][0] }}, {{ this.bounds[0][1] }}],
                    [{{ this.bounds[1][0] }}, {{ this.bounds[1][1] }}]
                );
                {{ this._parent.get_name() }}.setMaxBounds(bounds);
                {{ this._parent.get_name() }}.options.maxBoundsViscosity = 1.0;
            {% endmacro %}
            """
        )


class MunichAirspaceDigitalTwin:
    def __init__(self):
        self.nodes = {}
        self.routes = []
        self.graph = nx.Graph()

    def add_node(self, air_node):
        self.nodes[air_node.name] = air_node

        self.graph.add_node(
            air_node.name,
            id=air_node.id,
            type=air_node.node_type,
            lat=air_node.lat,
            lon=air_node.lon,
            description=air_node.description,
            capacity=air_node.capacity,
            current_load=air_node.current_load,
            available_slots=air_node.available_slots,
            availability_status=air_node.availability_status,
            priority_level=air_node.priority_level,
            zone_type=air_node.zone_type,
            demand_score=air_node.demand_score,
            weather_zone=air_node.weather_zone,
        )

    def sync_node_to_graph(self, node_name):
        node = self.nodes[node_name]

        self.graph.nodes[node_name]["current_load"] = node.current_load
        self.graph.nodes[node_name]["available_slots"] = node.available_slots
        self.graph.nodes[node_name]["availability_status"] = node.availability_status

    def add_route(self, air_route):
        if air_route.start not in self.nodes:
            raise ValueError(f"Start node does not exist: {air_route.start}")

        if air_route.end not in self.nodes:
            raise ValueError(f"End node does not exist: {air_route.end}")

        start_node = self.nodes[air_route.start]
        end_node = self.nodes[air_route.end]

        distance = calculate_air_distance_km(
            start_node.lat,
            start_node.lon,
            end_node.lat,
            end_node.lon,
        )

        air_route.distance = distance
        air_route.recalculate_costs()

        self.routes.append(air_route)
        self.sync_route_to_graph(air_route)

    def sync_route_to_graph(self, air_route):
        self.graph.add_edge(
            air_route.start,
            air_route.end,
            weight=air_route.total_cost,
            distance_km=air_route.distance,
            battery_consumption_rate=BATTERY_CONSUMPTION_RATE,
            battery_cost=air_route.battery_cost,
            noise_penalty=air_route.noise_penalty,
            weather_penalty=air_route.weather_penalty,
            current_aircraft_count=air_route.current_aircraft_count,
            traffic_penalty_per_aircraft=TRAFFIC_PENALTY_PER_AIRCRAFT,
            traffic_penalty=air_route.traffic_penalty,
            total_cost=air_route.total_cost,
            route_type=air_route.route_type,
        )

    def get_route(self, start, end):
        for route in self.routes:
            if (route.start == start and route.end == end) or (
                route.start == end and route.end == start
            ):
                return route

        raise ValueError(f"Route does not exist: {start} <-> {end}")

    def update_route_congestion(self, start, end, current_aircraft_count):
        """
        Update congestion on a route.
        traffic_penalty is automatically recalculated as:
        current_aircraft_count * TRAFFIC_PENALTY_PER_AIRCRAFT
        """
        if current_aircraft_count < 0:
            raise ValueError("current_aircraft_count cannot be negative.")

        route = self.get_route(start, end)
        route.current_aircraft_count = current_aircraft_count
        route.recalculate_costs()
        self.sync_route_to_graph(route)

        return route.to_dict()

    def increase_route_congestion(self, start, end, amount=1):
        route = self.get_route(start, end)
        new_count = route.current_aircraft_count + amount
        return self.update_route_congestion(start, end, new_count)

    def decrease_route_congestion(self, start, end, amount=1):
        route = self.get_route(start, end)
        new_count = max(route.current_aircraft_count - amount, 0)
        return self.update_route_congestion(start, end, new_count)

    def get_pad_availability(self, node_name):
        if node_name not in self.nodes:
            raise ValueError(f"Node does not exist: {node_name}")

        node = self.nodes[node_name]

        return {
            "node": node.name,
            "type": node.node_type,
            "capacity": node.capacity,
            "current_load": node.current_load,
            "available_slots": node.available_slots,
            "status": node.availability_status,
            "priority_level": node.priority_level,
            "demand_score": node.demand_score,
            "zone_type": node.zone_type,
            "weather_zone": node.weather_zone,
        }

    def occupy_landing_slot(self, node_name):
        if node_name not in self.nodes:
            raise ValueError(f"Node does not exist: {node_name}")

        node = self.nodes[node_name]

        if node.current_load >= node.capacity:
            return False

        node.current_load += 1
        self.sync_node_to_graph(node_name)
        return True

    def release_landing_slot(self, node_name):
        if node_name not in self.nodes:
            raise ValueError(f"Node does not exist: {node_name}")

        node = self.nodes[node_name]

        if node.current_load > 0:
            node.current_load -= 1

        self.sync_node_to_graph(node_name)
        return True

    def get_all_availability(self):
        return {
            node_name: self.get_pad_availability(node_name)
            for node_name in self.nodes
        }

    def build_world(self):
        node_data = [
            (
                1,
                "Munich Airport",
                "pad",
                48.3538,
                11.7861,
                "Major airport pad and northern entry point for eVTOL traffic.",
                8,
                8,
                "airport",
                100,
                "north",
                3,
            ),
            (
                2,
                "Munich Central Station (Hauptbahnhof)",
                "pad",
                48.1402,
                11.5584,
                "High passenger-transfer pad near the main railway station.",
                6,
                8,
                "commercial",
                95,
                "central",
                4,
            ),
            (
                3,
                "Marienplatz",
                "pad",
                48.1374,
                11.5755,
                "Central Munich pad with high passenger demand.",
                5,
                9,
                "commercial",
                90,
                "central",
                5,
            ),
            (
                4,
                "TUM Main Campus",
                "pad",
                48.1486,
                11.5682,
                "University and technology district pad.",
                4,
                7,
                "educational",
                85,
                "central",
                1,
            ),
            (
                5,
                "LMU Munich",
                "pad",
                48.1508,
                11.5806,
                "University district pad near Maxvorstadt.",
                4,
                7,
                "educational",
                80,
                "central",
                2,
            ),
            (
                6,
                "Allianz Arena",
                "pad",
                48.2188,
                11.6247,
                "Event-based northern mobility pad.",
                4,
                6,
                "event",
                70,
                "north",
                1,
            ),
            (
                7,
                "Messe München",
                "pad",
                48.1356,
                11.6903,
                "Trade fair and business mobility pad.",
                5,
                7,
                "commercial",
                75,
                "east",
                2,
            ),
            (
                8,
                "Olympiapark",
                "pad",
                48.1739,
                11.5461,
                "Event and leisure mobility pad.",
                4,
                6,
                "event",
                70,
                "northwest",
                2,
            ),
            (
                9,
                "Schwabing",
                "pad",
                48.1665,
                11.5860,
                "Dense residential and business district pad.",
                3,
                6,
                "residential",
                75,
                "central",
                2,
            ),
            (
                10,
                "Sendlinger Tor",
                "pad",
                48.1330,
                11.5668,
                "Inner-city transfer pad.",
                4,
                7,
                "commercial",
                80,
                "central",
                2,
            ),
            (
                11,
                "TUM Klinikum Rechts der Isar",
                "hospital",
                48.1355,
                11.5991,
                "Central medical landing point near Rechts der Isar.",
                4,
                10,
                "medical",
                70,
                "central",
                1,
            ),
            (
                12,
                "Großhadern Clinic",
                "hospital",
                48.1113,
                11.4697,
                "Large hospital zone in southwest Munich.",
                5,
                10,
                "medical",
                75,
                "west",
                2,
            ),
            (
                13,
                "München Klinik Schwabing",
                "hospital",
                48.1678,
                11.5826,
                "Hospital landing point in Schwabing.",
                3,
                10,
                "medical",
                65,
                "central",
                2,
            ),
            (
                14,
                "Munich Clinic Bogenhausen",
                "hospital",
                48.1525,
                11.6215,
                "Hospital landing point in eastern Munich.",
                3,
                10,
                "medical",
                65,
                "east",
                1,
            ),
            (
                15,
                "München Klinik Neuperlach",
                "hospital",
                48.1039,
                11.6460,
                "Southeast medical landing point.",
                3,
                10,
                "medical",
                60,
                "southeast",
                0,
            ),
            (
                16,
                "Harlaching Hospital",
                "hospital",
                48.1027,
                11.5798,
                "Southern medical landing point.",
                3,
                10,
                "medical",
                60,
                "south",
                1,
            ),
            (
                17,
                "Charging Hub A - Airport",
                "charging_hub",
                48.3600,
                11.7800,
                "Airport charging hub for recharging between airport missions.",
                8,
                6,
                "airport",
                85,
                "north",
                4,
            ),
            (
                18,
                "Charging Hub B - TUM Tech Area",
                "charging_hub",
                48.1525,
                11.5740,
                "Technology-district charging hub near TUM and LMU.",
                5,
                6,
                "educational",
                80,
                "central",
                2,
            ),
            (
                19,
                "Charging Hub C - City Center",
                "charging_hub",
                48.1390,
                11.5810,
                "City-center charging hub near Marienplatz for high passenger turnover.",
                5,
                6,
                "commercial",
                85,
                "central",
                4,
            ),
            (
                20,
                "Charging Hub D - Großhadern Zone",
                "charging_hub",
                48.1090,
                11.4750,
                "Southwest charging hub supporting Großhadern emergency logistics.",
                5,
                6,
                "medical",
                70,
                "west",
                1,
            ),
            (
                21,
                "Charging Hub E - East Munich",
                "charging_hub",
                48.1350,
                11.6700,
                "Eastern charging hub supporting Messe München and Bogenhausen corridors.",
                5,
                6,
                "commercial",
                75,
                "east",
                3,
            ),
        ]

        for (
            node_id,
            name,
            node_type,
            lat,
            lon,
            description,
            capacity,
            priority_level,
            zone_type,
            demand_score,
            weather_zone,
            current_load,
        ) in node_data:
            self.add_node(
                AirNode(
                    node_id=node_id,
                    name=name,
                    node_type=node_type,
                    lat=lat,
                    lon=lon,
                    description=description,
                    capacity=capacity,
                    priority_level=priority_level,
                    zone_type=zone_type,
                    demand_score=demand_score,
                    weather_zone=weather_zone,
                    current_load=current_load,
                )
            )

        route_pairs = [
            # start, end, route_type, noise_penalty, weather_penalty, current_aircraft_count

            ("Munich Airport", "Charging Hub A - Airport", "charging_corridor", 0, 0, 1),
            ("Munich Airport", "Allianz Arena", "airport_corridor", 1, 0, 3),
            ("Munich Airport", "Munich Central Station (Hauptbahnhof)", "airport_corridor", 4, 0, 5),
            ("Munich Airport", "Messe München", "airport_corridor", 2, 0, 4),

            ("Allianz Arena", "Schwabing", "city_corridor", 7, 0, 2),
            ("Allianz Arena", "Olympiapark", "city_corridor", 4, 0, 2),
            ("Olympiapark", "Munich Central Station (Hauptbahnhof)", "city_corridor", 6, 0, 3),
            ("Olympiapark", "LMU Munich", "city_corridor", 7, 0, 2),

            ("Schwabing", "LMU Munich", "city_corridor", 8, 0, 3),
            ("Schwabing", "München Klinik Schwabing", "medical_corridor", 8, 0, 1),
            ("Schwabing", "Munich Clinic Bogenhausen", "medical_corridor", 6, 0, 2),
            ("LMU Munich", "TUM Main Campus", "city_corridor", 7, 0, 2),
            ("LMU Munich", "Marienplatz", "city_corridor", 6, 0, 3),
            ("TUM Main Campus", "Marienplatz", "city_corridor", 5, 0, 4),
            ("TUM Main Campus", "TUM Klinikum Rechts der Isar", "medical_corridor", 4, 0, 1),
            ("TUM Main Campus", "Charging Hub B - TUM Tech Area", "charging_corridor", 3, 0, 1),

            ("Marienplatz", "Munich Central Station (Hauptbahnhof)", "city_corridor", 7, 0, 5),
            ("Marienplatz", "Sendlinger Tor", "city_corridor", 7, 0, 4),
            ("Marienplatz", "Charging Hub C - City Center", "charging_corridor", 6, 0, 2),
            ("Marienplatz", "TUM Klinikum Rechts der Isar", "medical_corridor", 5, 0, 2),
            ("Marienplatz", "Messe München", "city_corridor", 3, 0, 3),

            ("Messe München", "Munich Clinic Bogenhausen", "medical_corridor", 3, 0, 1),
            ("Messe München", "München Klinik Neuperlach", "medical_corridor", 4, 0, 1),
            ("Munich Clinic Bogenhausen", "TUM Klinikum Rechts der Isar", "medical_corridor", 5, 0, 2),
            ("Charging Hub E - East Munich", "Messe München", "charging_corridor", 2, 0, 1),
            ("Charging Hub E - East Munich", "Munich Clinic Bogenhausen", "charging_corridor", 3, 0, 1),
            ("Charging Hub E - East Munich", "München Klinik Neuperlach", "charging_corridor", 3, 0, 1),

            ("Sendlinger Tor", "Harlaching Hospital", "medical_corridor", 5, 0, 1),
            ("Sendlinger Tor", "Großhadern Clinic", "medical_corridor", 5, 0, 2),
            ("Sendlinger Tor", "Munich Central Station (Hauptbahnhof)", "city_corridor", 7, 0, 4),
            ("Großhadern Clinic", "Charging Hub D - Großhadern Zone", "charging_corridor", 2, 0, 1),
            ("Großhadern Clinic", "Harlaching Hospital", "medical_corridor", 4, 0, 1),
            ("Harlaching Hospital", "München Klinik Neuperlach", "medical_corridor", 4, 0, 1),
        ]

        for (
            start,
            end,
            route_type,
            noise_penalty,
            weather_penalty,
            current_aircraft_count,
        ) in route_pairs:
            self.add_route(
                AirRoute(
                    start=start,
                    end=end,
                    route_type=route_type,
                    noise_penalty=noise_penalty,
                    weather_penalty=weather_penalty,
                    current_aircraft_count=current_aircraft_count,
                )
            )

    def find_shortest_path(self, start, end):
        if start not in self.nodes:
            raise ValueError(f"Start location does not exist: {start}")

        if end not in self.nodes:
            raise ValueError(f"Destination location does not exist: {end}")

        path = nx.shortest_path(
            self.graph,
            source=start,
            target=end,
            weight="weight",
        )

        total_cost = nx.shortest_path_length(
            self.graph,
            source=start,
            target=end,
            weight="weight",
        )

        distance_km = 0

        for source, target in zip(path[:-1], path[1:]):
            distance_km += self.graph[source][target]["distance_km"]

        return path, round(distance_km, 2), round(total_cost, 2)

    def export_world_json(self, filename="backend/world.json"):
        world = {
            "simulation_parameters": {
                "battery_consumption_rate": BATTERY_CONSUMPTION_RATE,
                "traffic_penalty_per_aircraft": TRAFFIC_PENALTY_PER_AIRCRAFT,
                "cost_formula": (
                    "total_cost = distance_km + battery_cost + noise_penalty "
                    "+ weather_penalty + traffic_penalty"
                ),
            },
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [route.to_dict() for route in self.routes],
        }

        output_path = Path(filename)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(world, file, indent=4, ensure_ascii=False)

        return world

    def get_network_stats(self):
        counts = {
            "pad": 0,
            "hospital": 0,
            "charging_hub": 0,
        }

        full_nodes = 0
        busy_nodes = 0
        available_nodes = 0
        total_aircraft_on_routes = 0

        for node in self.nodes.values():
            counts[node.node_type] = counts.get(node.node_type, 0) + 1

            if node.availability_status == "full":
                full_nodes += 1
            elif node.availability_status == "busy":
                busy_nodes += 1
            else:
                available_nodes += 1

        for route in self.routes:
            total_aircraft_on_routes += route.current_aircraft_count

        return {
            "total_nodes": len(self.nodes),
            "total_routes": len(self.routes),
            "pads": counts["pad"],
            "hospitals": counts["hospital"],
            "charging_hubs": counts["charging_hub"],
            "available_nodes": available_nodes,
            "busy_nodes": busy_nodes,
            "full_nodes": full_nodes,
            "total_aircraft_on_routes": total_aircraft_on_routes,
        }

    def _get_marker_color(self, node):
        if node.availability_status == "full":
            return "#7f1d1d"
        if node.availability_status == "busy":
            return "#f97316"

        color_by_type = {
            "pad": "#1f78b4",
            "hospital": "#e31a1c",
            "charging_hub": "#33a02c",
        }

        return color_by_type.get(node.node_type, "#666666")

    def _get_marker_html(self, node):
        label_by_type = {
            "pad": "P",
            "hospital": "H",
            "charging_hub": "C",
        }

        color = self._get_marker_color(node)
        label = label_by_type.get(node.node_type, "?")

        return f"""
        <div style="
            background: {color};
            color: white;
            border: 2px solid white;
            border-radius: 50%;
            width: 32px;
            height: 32px;
            line-height: 29px;
            text-align: center;
            font-weight: bold;
            font-size: 13px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.35);
        ">
            {label}
        </div>
        """

    def _get_label_html(self, node):
        return f"""
        <div style="
            font-size: 11px;
            font-weight: 600;
            color: #111827;
            background: rgba(255,255,255,0.85);
            padding: 2px 5px;
            border-radius: 4px;
            border: 1px solid rgba(0,0,0,0.15);
            white-space: nowrap;
            box-shadow: 0 1px 3px rgba(0,0,0,0.2);
        ">
            {node.name}
        </div>
        """

    def _get_hover_tooltip_html(self, node):
        status_color = {
            "available": "#16a34a",
            "busy": "#f97316",
            "full": "#dc2626",
        }.get(node.availability_status, "#6b7280")

        return f"""
        <div style="
            font-family: Arial, sans-serif;
            font-size: 13px;
            min-width: 240px;
        ">
            <div style="font-size: 15px; font-weight: 800; margin-bottom: 4px;">
                {node.name}
            </div>

            <div><b>Type:</b> {node.node_type}</div>
            <div><b>Capacity:</b> {node.capacity}</div>
            <div><b>Current load:</b> {node.current_load}</div>
            <div><b>Available slots:</b> {node.available_slots}</div>
            <div>
                <b>Status:</b>
                <span style="color:{status_color}; font-weight:800;">
                    {node.availability_status.upper()}
                </span>
            </div>

            <hr style="margin: 6px 0;">

            <div><b>Priority:</b> {node.priority_level}</div>
            <div><b>Demand:</b> {node.demand_score}</div>
            <div><b>Zone:</b> {node.zone_type}</div>
            <div><b>Weather zone:</b> {node.weather_zone}</div>
        </div>
        """

    def create_interactive_map(
        self,
        filename="backend/munich_airspace_map.html",
        highlight_path=None,
    ):
        output_path = Path(filename)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        munich_bounds = [
            [48.06, 11.42],
            [48.38, 11.82],
        ]

        munich_map = folium.Map(
            location=[48.17, 11.62],
            zoom_start=11,
            min_zoom=10,
            max_zoom=18,
            tiles=None,
            max_bounds=True,
            control_scale=True,
        )

        folium.TileLayer(
            tiles="OpenStreetMap",
            name="OpenStreetMap",
            control=True,
        ).add_to(munich_map)

        folium.TileLayer(
            tiles="CartoDB positron",
            name="Light map",
            control=True,
        ).add_to(munich_map)

        folium.TileLayer(
            tiles="CartoDB dark_matter",
            name="Dark map",
            control=True,
        ).add_to(munich_map)

        munich_map.fit_bounds(munich_bounds)
        munich_map.add_child(RestrictMapBounds(munich_bounds))

        pads_group = folium.FeatureGroup(name="Pads", show=True)
        hospitals_group = folium.FeatureGroup(name="Hospitals", show=True)
        charging_group = folium.FeatureGroup(name="Charging Hubs", show=True)

        airport_routes_group = folium.FeatureGroup(name="Airport Corridors", show=True)
        city_routes_group = folium.FeatureGroup(name="City Corridors", show=True)
        medical_routes_group = folium.FeatureGroup(name="Medical Corridors", show=True)
        charging_routes_group = folium.FeatureGroup(name="Charging Corridors", show=True)
        labels_group = folium.FeatureGroup(name="Location Labels", show=True)
        highlighted_path_group = folium.FeatureGroup(name="Highlighted Lowest-Cost Path", show=True)

        route_style = {
            "airport_corridor": {"color": "#6366f1", "weight": 5, "dash_array": None},
            "city_corridor": {"color": "#6b7280", "weight": 3, "dash_array": None},
            "medical_corridor": {"color": "#ef4444", "weight": 4, "dash_array": "8, 6"},
            "charging_corridor": {"color": "#22c55e", "weight": 4, "dash_array": "4, 6"},
        }

        route_group_by_type = {
            "airport_corridor": airport_routes_group,
            "city_corridor": city_routes_group,
            "medical_corridor": medical_routes_group,
            "charging_corridor": charging_routes_group,
        }

        for route in self.routes:
            start_node = self.nodes[route.start]
            end_node = self.nodes[route.end]

            style = route_style.get(
                route.route_type,
                {"color": "#555555", "weight": 3, "dash_array": None},
            )

            route_weight = style["weight"] + min(route.current_aircraft_count, 5) * 0.4

            route_popup = f"""
            <div style="font-family: Arial; width: 300px;">
                <h4 style="margin-bottom: 6px;">Air Corridor</h4>
                <b>From:</b> {route.start}<br>
                <b>To:</b> {route.end}<br>
                <b>Type:</b> {route.route_type}<br>
                <b>Distance:</b> {route.distance} km<br>
                <b>Battery rate:</b> {BATTERY_CONSUMPTION_RATE}<br>
                <b>Battery cost:</b> {route.battery_cost}<br>
                <b>Noise penalty:</b> {route.noise_penalty}<br>
                <b>Weather penalty:</b> {route.weather_penalty}<br>
                <b>Aircraft on route:</b> {route.current_aircraft_count}<br>
                <b>Traffic penalty:</b> {route.traffic_penalty}<br>
                <b>Total cost:</b> {route.total_cost}
            </div>
            """

            folium.PolyLine(
                locations=[
                    [start_node.lat, start_node.lon],
                    [end_node.lat, end_node.lon],
                ],
                color=style["color"],
                weight=route_weight,
                opacity=0.85,
                dash_array=style["dash_array"],
                tooltip=(
                    f"{route.start} ↔ {route.end} | "
                    f"aircraft {route.current_aircraft_count} | "
                    f"cost {route.total_cost}"
                ),
                popup=folium.Popup(route_popup, max_width=340),
            ).add_to(route_group_by_type[route.route_type])

        if highlight_path and len(highlight_path) >= 2:
            for start, end in zip(highlight_path[:-1], highlight_path[1:]):
                if start in self.nodes and end in self.nodes:
                    start_node = self.nodes[start]
                    end_node = self.nodes[end]

                    folium.PolyLine(
                        locations=[
                            [start_node.lat, start_node.lon],
                            [end_node.lat, end_node.lon],
                        ],
                        color="#facc15",
                        weight=8,
                        opacity=0.95,
                        tooltip=f"Highlighted path: {start} → {end}",
                    ).add_to(highlighted_path_group)

        for node in self.nodes.values():
            popup_html = f"""
            <div style="font-family: Arial; width: 320px;">
                <h3 style="margin-bottom: 4px;">{node.name}</h3>
                <b>Type:</b> {node.node_type}<br>
                <b>Capacity:</b> {node.capacity}<br>
                <b>Current load:</b> {node.current_load}<br>
                <b>Available slots:</b> {node.available_slots}<br>
                <b>Status:</b> {node.availability_status}<br>
                <b>Priority level:</b> {node.priority_level}<br>
                <b>Zone type:</b> {node.zone_type}<br>
                <b>Demand score:</b> {node.demand_score}<br>
                <b>Weather zone:</b> {node.weather_zone}<br>
                <b>Latitude:</b> {node.lat}<br>
                <b>Longitude:</b> {node.lon}<br>
                <p style="margin-top: 8px;">{node.description}</p>
            </div>
            """

            marker = folium.Marker(
                location=[node.lat, node.lon],
                tooltip=folium.Tooltip(
                    self._get_hover_tooltip_html(node),
                    sticky=True,
                    direction="top",
                    opacity=0.95,
                ),
                popup=folium.Popup(popup_html, max_width=360),
                icon=folium.DivIcon(
                    html=self._get_marker_html(node),
                    icon_size=(32, 32),
                    icon_anchor=(16, 16),
                ),
            )

            if node.node_type == "pad":
                marker.add_to(pads_group)
            elif node.node_type == "hospital":
                marker.add_to(hospitals_group)
            elif node.node_type == "charging_hub":
                marker.add_to(charging_group)

            folium.Marker(
                location=[node.lat + 0.002, node.lon + 0.002],
                icon=folium.DivIcon(
                    html=self._get_label_html(node),
                    icon_size=(220, 20),
                    icon_anchor=(0, 0),
                ),
            ).add_to(labels_group)

        airport_routes_group.add_to(munich_map)
        city_routes_group.add_to(munich_map)
        medical_routes_group.add_to(munich_map)
        charging_routes_group.add_to(munich_map)
        highlighted_path_group.add_to(munich_map)

        pads_group.add_to(munich_map)
        hospitals_group.add_to(munich_map)
        charging_group.add_to(munich_map)
        labels_group.add_to(munich_map)

        Fullscreen(
            position="topright",
            title="Full screen",
            title_cancel="Exit full screen",
            force_separate_button=True,
        ).add_to(munich_map)

        MiniMap(
            toggle_display=True,
            minimized=True,
            position="bottomright",
        ).add_to(munich_map)

        MeasureControl(
            position="topleft",
            primary_length_unit="kilometers",
            secondary_length_unit="meters",
            primary_area_unit="sqmeters",
        ).add_to(munich_map)

        folium.LayerControl(collapsed=False).add_to(munich_map)

        stats = self.get_network_stats()

        sidebar_html = f"""
        <div style="
            position: fixed;
            top: 20px;
            left: 50px;
            z-index: 9999;
            width: 360px;
            background: rgba(255,255,255,0.96);
            padding: 16px;
            border-radius: 14px;
            border: 1px solid #d1d5db;
            box-shadow: 0 8px 24px rgba(0,0,0,0.22);
            font-family: Arial, sans-serif;
            color: #111827;
        ">
            <div style="font-size: 18px; font-weight: 800; margin-bottom: 4px;">
                Munich Airspace Digital Twin
            </div>
            <div style="font-size: 12px; color: #4b5563; margin-bottom: 12px;">
                Phase 1.5: pad availability + route congestion
            </div>

            <div style="
                display: grid;
                grid-template-columns: 1fr 1fr 1fr;
                gap: 8px;
                margin-bottom: 12px;
            ">
                <div style="background:#dcfce7; padding:8px; border-radius:8px;">
                    <b>{stats["available_nodes"]}</b><br><span style="font-size:12px;">Available</span>
                </div>
                <div style="background:#ffedd5; padding:8px; border-radius:8px;">
                    <b>{stats["busy_nodes"]}</b><br><span style="font-size:12px;">Busy</span>
                </div>
                <div style="background:#fee2e2; padding:8px; border-radius:8px;">
                    <b>{stats["full_nodes"]}</b><br><span style="font-size:12px;">Full</span>
                </div>
            </div>

            <div style="
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 8px;
                margin-bottom: 12px;
            ">
                <div style="background:#eff6ff; padding:8px; border-radius:8px;">
                    <b>{stats["pads"]}</b><br><span style="font-size:12px;">Pads</span>
                </div>
                <div style="background:#fef2f2; padding:8px; border-radius:8px;">
                    <b>{stats["hospitals"]}</b><br><span style="font-size:12px;">Hospitals</span>
                </div>
                <div style="background:#f0fdf4; padding:8px; border-radius:8px;">
                    <b>{stats["charging_hubs"]}</b><br><span style="font-size:12px;">Charging Hubs</span>
                </div>
                <div style="background:#f9fafb; padding:8px; border-radius:8px;">
                    <b>{stats["total_aircraft_on_routes"]}</b><br><span style="font-size:12px;">Aircraft on Routes</span>
                </div>
            </div>

            <div style="font-size: 13px; line-height: 1.55;">
                <b>Cost formula</b><br>
                distance + battery + noise + weather + traffic<br><br>

                <b>Battery</b><br>
                battery cost = distance × {BATTERY_CONSUMPTION_RATE}<br><br>

                <b>Traffic</b><br>
                traffic penalty = aircraft count × {TRAFFIC_PENALTY_PER_AIRCRAFT}<br><br>

                <b>Marker colors</b><br>
                <span style="color:#1f78b4; font-weight:bold;">●</span> Available pad<br>
                <span style="color:#f97316; font-weight:bold;">●</span> Busy / low slots<br>
                <span style="color:#7f1d1d; font-weight:bold;">●</span> Full / no slots<br>
                <span style="color:#e31a1c; font-weight:bold;">●</span> Hospital<br>
                <span style="color:#33a02c; font-weight:bold;">●</span> Charging hub<br>
            </div>

            <div style="
                margin-top: 12px;
                padding-top: 10px;
                border-top: 1px solid #e5e7eb;
                font-size: 12px;
                color: #4b5563;
            ">
                Hover over markers for pad availability.
                Hover over corridors for congestion and cost.
            </div>
        </div>
        """

        munich_map.get_root().html.add_child(folium.Element(sidebar_html))

        title_html = """
        <div style="
            position: fixed;
            top: 20px;
            right: 70px;
            z-index: 9999;
            background: rgba(17,24,39,0.90);
            color: white;
            padding: 10px 14px;
            border-radius: 10px;
            font-family: Arial, sans-serif;
            font-size: 13px;
            box-shadow: 0 4px 14px rgba(0,0,0,0.25);
        ">
            <b>Layer 3:</b> Shared Munich airspace environment
        </div>
        """

        munich_map.get_root().html.add_child(folium.Element(title_html))

        munich_map.save(output_path)
        return str(output_path)