import json
from math import radians, sin, cos, sqrt, atan2
from pathlib import Path
from datetime import datetime

import folium
import networkx as nx
from branca.element import MacroElement, Template
from folium.plugins import Fullscreen, MiniMap, MeasureControl


BATTERY_CONSUMPTION_RATE = 0.18
TRAFFIC_PENALTY_PER_AIRCRAFT = 2.0

MISSION_TYPES = {
    "passenger",
    "emergency",
}


def calculate_air_distance_km(lat1, lon1, lat2, lon2):
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
        node_category,
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
        self.node_category = node_category
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
            "category": self.node_category,
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
        route_type="city_corridor",
        noise_penalty=0,
        weather_penalty=0,
        current_aircraft_count=0,
        safety_status="open",
        altitude_layer="standard",
    ):
        self.start = start
        self.end = end
        self.route_type = route_type

        self.distance = None
        self.battery_cost = None
        self.noise_penalty = noise_penalty
        self.weather_penalty = weather_penalty
        self.current_aircraft_count = current_aircraft_count
        self.traffic_penalty = None
        self.safety_status = safety_status
        self.altitude_layer = altitude_layer
        self.base_total_cost = None

    def recalculate_costs(self):
        if self.distance is None:
            raise ValueError("Distance must be calculated before route cost.")

        self.battery_cost = round(self.distance * BATTERY_CONSUMPTION_RATE, 2)

        self.traffic_penalty = round(
            self.current_aircraft_count * TRAFFIC_PENALTY_PER_AIRCRAFT,
            2,
        )

        self.base_total_cost = round(
            self.distance
            + self.battery_cost
            + self.noise_penalty
            + self.weather_penalty
            + self.traffic_penalty,
            2,
        )

    def mission_cost(self, mission_type):
        if mission_type not in MISSION_TYPES:
            raise ValueError(f"Unknown mission type: {mission_type}")

        if self.safety_status == "closed":
            return float("inf")

        if self.safety_status == "restricted" and mission_type != "emergency":
            return float("inf")

        if self.altitude_layer == "emergency" and mission_type != "emergency":
            return float("inf")

        if mission_type == "passenger":
            cost = (
                self.distance
                + self.battery_cost
                + self.noise_penalty
                + self.weather_penalty
                + self.traffic_penalty
            )

        elif mission_type == "emergency":
            cost = (
                self.distance
                + self.weather_penalty
                + 0.3 * self.traffic_penalty
            )

        else:
            cost = self.base_total_cost

        return round(cost, 2)

    def to_dict(self):
        return {
            "start": self.start,
            "end": self.end,
            "route_type": self.route_type,
            "altitude_layer": self.altitude_layer,
            "safety_status": self.safety_status,
            "distance_km": self.distance,
            "battery_consumption_rate": BATTERY_CONSUMPTION_RATE,
            "battery_cost": self.battery_cost,
            "noise_penalty": self.noise_penalty,
            "weather_penalty": self.weather_penalty,
            "current_aircraft_count": self.current_aircraft_count,
            "traffic_penalty_per_aircraft": TRAFFIC_PENALTY_PER_AIRCRAFT,
            "traffic_penalty": self.traffic_penalty,
            "base_total_cost": self.base_total_cost,
            "mission_costs": {
                mission_type: self.mission_cost(mission_type)
                for mission_type in MISSION_TYPES
            },
        }


class RestrictMapBounds(MacroElement):
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
        self.event_log = []

    def log_event(self, event_type, message):
        event = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "event_type": event_type,
            "message": message,
        }
        self.event_log.append(event)
        return event

    def add_node(self, air_node):
        self.nodes[air_node.name] = air_node

        self.graph.add_node(
            air_node.name,
            id=air_node.id,
            type=air_node.node_type,
            category=air_node.node_category,
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

        air_route.distance = calculate_air_distance_km(
            start_node.lat,
            start_node.lon,
            end_node.lat,
            end_node.lon,
        )

        air_route.recalculate_costs()

        self.routes.append(air_route)
        self.sync_route_to_graph(air_route)

    def sync_route_to_graph(self, route):
        self.graph.add_edge(
            route.start,
            route.end,
            weight=route.base_total_cost,
            route_type=route.route_type,
            altitude_layer=route.altitude_layer,
            safety_status=route.safety_status,
            distance_km=route.distance,
            battery_cost=route.battery_cost,
            noise_penalty=route.noise_penalty,
            weather_penalty=route.weather_penalty,
            current_aircraft_count=route.current_aircraft_count,
            traffic_penalty=route.traffic_penalty,
            base_total_cost=route.base_total_cost,
        )

    def get_route(self, start, end):
        for route in self.routes:
            if (route.start == start and route.end == end) or (
                route.start == end and route.end == start
            ):
                return route

        raise ValueError(f"Route does not exist: {start} <-> {end}")

    def update_route_congestion(self, start, end, current_aircraft_count):
        if current_aircraft_count < 0:
            raise ValueError("current_aircraft_count cannot be negative.")

        route = self.get_route(start, end)
        route.current_aircraft_count = current_aircraft_count
        route.recalculate_costs()
        self.sync_route_to_graph(route)

        self.log_event(
            "ROUTE_CONGESTION_UPDATE",
            f"{start} ↔ {end} now has {current_aircraft_count} aircraft. Traffic penalty = {route.traffic_penalty}.",
        )

        return route.to_dict()

    def increase_route_congestion(self, start, end, amount=1):
        route = self.get_route(start, end)
        return self.update_route_congestion(
            start,
            end,
            route.current_aircraft_count + amount,
        )

    def decrease_route_congestion(self, start, end, amount=1):
        route = self.get_route(start, end)
        return self.update_route_congestion(
            start,
            end,
            max(route.current_aircraft_count - amount, 0),
        )

    def update_weather_penalty(self, start, end, weather_penalty):
        if weather_penalty < 0:
            raise ValueError("weather_penalty cannot be negative.")

        route = self.get_route(start, end)
        route.weather_penalty = weather_penalty
        route.recalculate_costs()
        self.sync_route_to_graph(route)

        self.log_event(
            "WEATHER_UPDATE",
            f"{start} ↔ {end} weather penalty updated to {weather_penalty}.",
        )

        return route.to_dict()

    def update_corridor_status(self, start, end, safety_status):
        if safety_status not in {"open", "restricted", "closed"}:
            raise ValueError("safety_status must be open, restricted, or closed.")

        route = self.get_route(start, end)
        route.safety_status = safety_status
        route.recalculate_costs()
        self.sync_route_to_graph(route)

        self.log_event(
            "CORRIDOR_STATUS_UPDATE",
            f"{start} ↔ {end} status changed to {safety_status}.",
        )

        return route.to_dict()

    def close_corridor(self, start, end):
        return self.update_corridor_status(start, end, "closed")

    def restrict_corridor(self, start, end):
        return self.update_corridor_status(start, end, "restricted")

    def reopen_corridor(self, start, end):
        return self.update_corridor_status(start, end, "open")

    def get_pad_availability(self, node_name):
        if node_name not in self.nodes:
            raise ValueError(f"Node does not exist: {node_name}")

        node = self.nodes[node_name]

        return {
            "node": node.name,
            "type": node.node_type,
            "category": node.node_category,
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
            self.log_event(
                "LANDING_DENIED",
                f"{node_name} is full. Landing slot unavailable.",
            )
            return False

        node.current_load += 1
        self.sync_node_to_graph(node_name)

        self.log_event(
            "LANDING_SLOT_OCCUPIED",
            f"{node_name}: {node.current_load}/{node.capacity} slots occupied.",
        )

        return True

    def release_landing_slot(self, node_name):
        if node_name not in self.nodes:
            raise ValueError(f"Node does not exist: {node_name}")

        node = self.nodes[node_name]

        if node.current_load > 0:
            node.current_load -= 1

        self.sync_node_to_graph(node_name)

        self.log_event(
            "LANDING_SLOT_RELEASED",
            f"{node_name}: {node.current_load}/{node.capacity} slots occupied.",
        )

        return True

    def get_all_availability(self):
        return {
            node_name: self.get_pad_availability(node_name)
            for node_name in self.nodes
        }

    def route_allowed_for_mission(self, route, mission_type):
        if route.safety_status == "closed":
            return False

        if route.safety_status == "restricted" and mission_type != "emergency":
            return False

        if route.altitude_layer == "emergency" and mission_type != "emergency":
            return False

        return True

    def build_mission_graph(self, mission_type):
        mission_graph = nx.Graph()

        for node_name, node in self.nodes.items():
            mission_graph.add_node(
                node_name,
                lat=node.lat,
                lon=node.lon,
                type=node.node_type,
                category=node.node_category,
            )

        for route in self.routes:
            if not self.route_allowed_for_mission(route, mission_type):
                continue

            mission_graph.add_edge(
                route.start,
                route.end,
                weight=route.mission_cost(mission_type),
                distance_km=route.distance,
            )

        return mission_graph

    def find_best_route(self, start, destination, mission_type="passenger"):
        if mission_type not in MISSION_TYPES:
            raise ValueError(f"Unknown mission type: {mission_type}")

        if start not in self.nodes:
            raise ValueError(f"Start node does not exist: {start}")

        if destination not in self.nodes:
            raise ValueError(f"Destination node does not exist: {destination}")

        mission_graph = self.build_mission_graph(mission_type)

        path = nx.shortest_path(
            mission_graph,
            source=start,
            target=destination,
            weight="weight",
        )

        total_cost = nx.shortest_path_length(
            mission_graph,
            source=start,
            target=destination,
            weight="weight",
        )

        distance_km = 0

        for source, target in zip(path[:-1], path[1:]):
            route = self.get_route(source, target)
            distance_km += route.distance

        result = {
            "mission_type": mission_type,
            "start": start,
            "destination": destination,
            "path": path,
            "distance_km": round(distance_km, 2),
            "total_cost": round(total_cost, 2),
        }

        self.log_event(
            "ROUTE_SELECTED",
            f"{mission_type} route selected: {' → '.join(path)} | cost={round(total_cost, 2)}",
        )

        return result

    def find_nearest_node_by_type(self, start, node_type, mission_type="passenger"):
        candidates = [
            node.name
            for node in self.nodes.values()
            if node.node_type == node_type and node.available_slots > 0
        ]

        if not candidates:
            raise ValueError(f"No available nodes found for type: {node_type}")

        best_result = None

        for candidate in candidates:
            try:
                result = self.find_best_route(start, candidate, mission_type)
            except nx.NetworkXNoPath:
                continue

            if best_result is None or result["total_cost"] < best_result["total_cost"]:
                best_result = result

        if best_result is None:
            raise ValueError(f"No reachable available node found for type: {node_type}")

        return best_result

    def find_nearest_charging_hub(self, start):
        return self.find_nearest_node_by_type(
            start=start,
            node_type="charging_hub",
            mission_type="passenger",
        )

    def find_nearest_hospital(self, start):
        return self.find_nearest_node_by_type(
            start=start,
            node_type="hospital",
            mission_type="emergency",
        )

    def build_world(self):
        node_data = [
            # Transport hubs
            (1, "Munich Airport", "pad", "transport_hub", 48.3538, 11.7861, "Airport transport hub and northern airspace entry point.", 10, 9, "airport", 100, "north", 3),
            (2, "Munich Central Station (Hauptbahnhof)", "pad", "transport_hub", 48.1402, 11.5584, "Main railway hub and central passenger transfer point.", 8, 8, "transport", 95, "central", 4),
            (3, "Ostbahnhof", "pad", "transport_hub", 48.1272, 11.6046, "Eastern railway hub and transfer node.", 6, 7, "transport", 80, "east", 2),
            (4, "Pasing Bahnhof", "pad", "transport_hub", 48.1498, 11.4617, "Western railway hub and transfer node.", 6, 7, "transport", 75, "west", 2),

            # City and business
            (5, "Marienplatz", "pad", "city_business", 48.1374, 11.5755, "Central city demand node.", 5, 9, "commercial", 90, "central", 5),
            (6, "Sendlinger Tor", "pad", "city_business", 48.1330, 11.5668, "Inner-city transfer and business node.", 5, 7, "commercial", 80, "central", 2),
            (7, "Schwabing", "pad", "city_business", 48.1665, 11.5860, "Residential and business district node.", 4, 6, "residential", 75, "central", 2),
            (8, "Arabellapark", "pad", "city_business", 48.1527, 11.6189, "Business district node in east Munich.", 4, 6, "business", 70, "east", 1),
            (9, "Messe München", "pad", "city_business", 48.1356, 11.6903, "Trade fair and conference node.", 7, 7, "commercial", 85, "east", 2),
            (10, "Neuperlach", "pad", "city_business", 48.1000, 11.6450, "Southeast demand and business zone.", 4, 6, "residential", 65, "southeast", 1),

            # Universities
            (11, "TUM Main Campus", "pad", "university", 48.1486, 11.5682, "University and technology node.", 5, 7, "educational", 85, "central", 1),
            (12, "LMU Munich", "pad", "university", 48.1508, 11.5806, "University district node.", 5, 7, "educational", 80, "central", 2),
            (13, "TUM Garching Campus", "pad", "university", 48.2623, 11.6671, "Research campus and north transit node.", 6, 7, "educational", 80, "north", 2),

            # Events and tourism
            (14, "Allianz Arena", "pad", "event_tourism", 48.2188, 11.6247, "Event node for stadium traffic.", 6, 6, "event", 75, "north", 1),
            (15, "Olympiapark", "pad", "event_tourism", 48.1739, 11.5461, "Event and tourism node.", 5, 6, "event", 70, "northwest", 2),
            (16, "BMW Welt", "pad", "event_tourism", 48.1768, 11.5567, "Tourism and business attraction node.", 4, 6, "tourism", 70, "northwest", 1),

            # Residential / demand zones
            (17, "Bogenhausen", "pad", "residential_demand", 48.1540, 11.6250, "Residential demand zone in east Munich.", 4, 6, "residential", 65, "east", 1),
            (18, "Riem", "pad", "residential_demand", 48.1370, 11.6860, "East Munich residential and demand zone.", 4, 6, "residential", 60, "east", 1),
            (19, "Harlaching", "pad", "residential_demand", 48.0960, 11.5700, "South Munich residential demand zone.", 4, 6, "residential", 60, "south", 1),
            (20, "Maxvorstadt", "pad", "residential_demand", 48.1510, 11.5680, "Central university and residential demand zone.", 4, 7, "residential", 80, "central", 2),

            # Hospitals
            (21, "TUM Klinikum Rechts der Isar", "hospital", "emergency_hospital", 48.1355, 11.5991, "Central emergency hospital.", 4, 10, "medical", 70, "central", 1),
            (22, "Klinikum der Universität München Großhadern", "hospital", "emergency_hospital", 48.1113, 11.4697, "Southwest major hospital.", 5, 10, "medical", 75, "west", 2),
            (23, "München Klinik Schwabing", "hospital", "emergency_hospital", 48.1678, 11.5826, "North-central hospital.", 3, 10, "medical", 65, "central", 2),
            (24, "München Klinik Bogenhausen", "hospital", "emergency_hospital", 48.1525, 11.6215, "East Munich hospital.", 3, 10, "medical", 65, "east", 1),
            (25, "München Klinik Neuperlach", "hospital", "emergency_hospital", 48.1039, 11.6460, "Southeast hospital.", 3, 10, "medical", 60, "southeast", 0),
            (26, "TUM Klinikum Deutsches Herzzentrum", "hospital", "emergency_hospital", 48.1585, 11.5696, "Specialist cardiac emergency hospital.", 3, 10, "medical", 70, "central", 1),

            # Charging hubs
            (27, "Airport Charging Hub", "charging_hub", "charging_hub", 48.3600, 11.7800, "Airport charging hub.", 8, 6, "airport", 85, "north", 4),
            (28, "Central Charging Hub", "charging_hub", "charging_hub", 48.1390, 11.5810, "Central charging hub near Marienplatz.", 6, 6, "commercial", 85, "central", 3),
            (29, "Ismaning Transit Charging Hub", "charging_hub", "charging_hub", 48.2260, 11.6750, "North transit charging hub supporting Airport-City, Garching, Allianz Arena, and Messe corridors.", 6, 7, "transit", 85, "north", 2),
            (30, "East Munich Charging Hub", "charging_hub", "charging_hub", 48.1350, 11.6700, "East Munich charging hub near Messe and Riem.", 5, 6, "commercial", 75, "east", 3),
            (31, "Großhadern Charging Hub", "charging_hub", "charging_hub", 48.1090, 11.4750, "Southwest medical charging hub.", 5, 6, "medical", 70, "west", 1),
        ]

        for data in node_data:
            self.add_node(AirNode(*data))

        route_data = [
            # start, end, type, noise, weather, aircraft_count, status, altitude_layer

            # Airport and north transit corridor
            ("Munich Airport", "Airport Charging Hub", "charging_corridor", 0, 0, 2, "open", "transit"),
            ("Airport Charging Hub", "Ismaning Transit Charging Hub", "charging_corridor", 1, 0, 3, "open", "transit"),
            ("Munich Airport", "Ismaning Transit Charging Hub", "airport_corridor", 1, 0, 4, "open", "transit"),
            ("Ismaning Transit Charging Hub", "TUM Garching Campus", "charging_corridor", 2, 0, 2, "open", "transit"),
            ("Ismaning Transit Charging Hub", "Allianz Arena", "charging_corridor", 2, 0, 2, "open", "transit"),
            ("Ismaning Transit Charging Hub", "Messe München", "charging_corridor", 3, 0, 2, "open", "transit"),
            ("TUM Garching Campus", "Allianz Arena", "city_corridor", 2, 0, 2, "open", "transit"),
            ("TUM Garching Campus", "Schwabing", "city_corridor", 5, 0, 3, "open", "standard"),
            ("Allianz Arena", "Olympiapark", "city_corridor", 4, 0, 2, "open", "standard"),
            ("Olympiapark", "BMW Welt", "city_corridor", 3, 0, 2, "open", "standard"),
            ("BMW Welt", "Schwabing", "city_corridor", 5, 0, 2, "open", "standard"),

            # Central city corridor
            ("Schwabing", "LMU Munich", "city_corridor", 8, 0, 3, "open", "standard"),
            ("LMU Munich", "Maxvorstadt", "city_corridor", 7, 0, 2, "open", "standard"),
            ("Maxvorstadt", "TUM Main Campus", "city_corridor", 6, 0, 2, "open", "standard"),
            ("TUM Main Campus", "Marienplatz", "city_corridor", 5, 0, 4, "open", "standard"),
            ("Marienplatz", "Central Charging Hub", "charging_corridor", 6, 0, 3, "open", "standard"),
            ("Marienplatz", "Sendlinger Tor", "city_corridor", 7, 0, 4, "open", "standard"),
            ("Sendlinger Tor", "Munich Central Station (Hauptbahnhof)", "city_corridor", 7, 0, 4, "open", "standard"),
            ("Marienplatz", "Munich Central Station (Hauptbahnhof)", "city_corridor", 7, 0, 5, "open", "standard"),

            # East corridor
            ("Marienplatz", "Ostbahnhof", "city_corridor", 6, 0, 4, "open", "standard"),
            ("Ostbahnhof", "Arabellapark", "city_corridor", 4, 0, 2, "open", "standard"),
            ("Arabellapark", "Bogenhausen", "city_corridor", 5, 0, 2, "open", "standard"),
            ("Bogenhausen", "Messe München", "city_corridor", 5, 0, 2, "open", "standard"),
            ("Ostbahnhof", "Messe München", "city_corridor", 4, 0, 3, "open", "standard"),
            ("Messe München", "Riem", "city_corridor", 3, 0, 2, "open", "standard"),
            ("Riem", "East Munich Charging Hub", "charging_corridor", 2, 0, 2, "open", "standard"),
            ("Messe München", "East Munich Charging Hub", "charging_corridor", 2, 0, 2, "open", "standard"),
            ("East Munich Charging Hub", "Neuperlach", "charging_corridor", 2, 0, 1, "open", "standard"),

            # West and southwest corridor
            ("Munich Central Station (Hauptbahnhof)", "Pasing Bahnhof", "city_corridor", 5, 0, 3, "open", "standard"),
            ("Pasing Bahnhof", "Klinikum der Universität München Großhadern", "medical_corridor", 4, 0, 1, "open", "emergency"),
            ("Klinikum der Universität München Großhadern", "Großhadern Charging Hub", "charging_corridor", 2, 0, 1, "open", "emergency"),
            ("Sendlinger Tor", "Klinikum der Universität München Großhadern", "medical_corridor", 5, 0, 2, "open", "emergency"),

            # South corridor
            ("Sendlinger Tor", "Harlaching", "city_corridor", 5, 0, 2, "open", "standard"),
            ("Harlaching", "Neuperlach", "city_corridor", 4, 0, 1, "open", "standard"),
            ("Harlaching", "München Klinik Neuperlach", "medical_corridor", 4, 0, 1, "open", "emergency"),
            ("Neuperlach", "München Klinik Neuperlach", "medical_corridor", 3, 0, 1, "open", "emergency"),

            # Emergency hospital access corridors
            ("Schwabing", "München Klinik Schwabing", "medical_corridor", 6, 0, 1, "open", "emergency"),
            ("Bogenhausen", "München Klinik Bogenhausen", "medical_corridor", 4, 0, 1, "open", "emergency"),
            ("Messe München", "München Klinik Neuperlach", "medical_corridor", 4, 0, 1, "open", "emergency"),
            ("Harlaching", "TUM Klinikum Rechts der Isar", "medical_corridor", 4, 0, 1, "open", "emergency"),
            ("Marienplatz", "TUM Klinikum Rechts der Isar", "medical_corridor", 5, 0, 2, "open", "emergency"),
            ("TUM Main Campus", "TUM Klinikum Rechts der Isar", "medical_corridor", 4, 0, 1, "open", "emergency"),
            ("TUM Main Campus", "TUM Klinikum Deutsches Herzzentrum", "medical_corridor", 4, 0, 1, "open", "emergency"),
            ("Maxvorstadt", "TUM Klinikum Deutsches Herzzentrum", "medical_corridor", 5, 0, 1, "open", "emergency"),
            ("München Klinik Bogenhausen", "TUM Klinikum Rechts der Isar", "medical_corridor", 5, 0, 1, "open", "emergency"),
        ]

        for (
            start,
            end,
            route_type,
            noise_penalty,
            weather_penalty,
            current_aircraft_count,
            safety_status,
            altitude_layer,
        ) in route_data:
            self.add_route(
                AirRoute(
                    start=start,
                    end=end,
                    route_type=route_type,
                    noise_penalty=noise_penalty,
                    weather_penalty=weather_penalty,
                    current_aircraft_count=current_aircraft_count,
                    safety_status=safety_status,
                    altitude_layer=altitude_layer,
                )
            )

        self.log_event(
            "WORLD_BUILT",
            f"Munich digital twin created with {len(self.nodes)} nodes and {len(self.routes)} corridors.",
        )

    def export_world_json(self, filename="backend/world.json"):
        world = {
            "simulation_parameters": {
                "battery_consumption_rate": BATTERY_CONSUMPTION_RATE,
                "traffic_penalty_per_aircraft": TRAFFIC_PENALTY_PER_AIRCRAFT,
                "mission_types": sorted(list(MISSION_TYPES)),
                "cost_formula": "Passenger and medical emergency routing over fixed air corridors.",
                "mission_type_note": "Emergency mission means medical emergency only. Low battery and aircraft failure are aircraft states, not mission types.",
            },
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [route.to_dict() for route in self.routes],
            "event_log": self.event_log,
        }

        output_path = Path(filename)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(world, file, indent=4, ensure_ascii=False)

        return world

    def get_network_stats(self):
        counts = {"pad": 0, "hospital": 0, "charging_hub": 0}
        category_counts = {}
        full_nodes = 0
        busy_nodes = 0
        available_nodes = 0
        total_aircraft_on_routes = 0
        closed_corridors = 0
        restricted_corridors = 0

        for node in self.nodes.values():
            counts[node.node_type] = counts.get(node.node_type, 0) + 1
            category_counts[node.node_category] = category_counts.get(node.node_category, 0) + 1

            if node.availability_status == "full":
                full_nodes += 1
            elif node.availability_status == "busy":
                busy_nodes += 1
            else:
                available_nodes += 1

        for route in self.routes:
            total_aircraft_on_routes += route.current_aircraft_count

            if route.safety_status == "closed":
                closed_corridors += 1
            elif route.safety_status == "restricted":
                restricted_corridors += 1

        return {
            "total_nodes": len(self.nodes),
            "total_routes": len(self.routes),
            "pads": counts["pad"],
            "hospitals": counts["hospital"],
            "charging_hubs": counts["charging_hub"],
            "category_counts": category_counts,
            "available_nodes": available_nodes,
            "busy_nodes": busy_nodes,
            "full_nodes": full_nodes,
            "total_aircraft_on_routes": total_aircraft_on_routes,
            "closed_corridors": closed_corridors,
            "restricted_corridors": restricted_corridors,
            "event_count": len(self.event_log),
        }

    def _get_marker_color(self, node):
        if node.availability_status == "full":
            return "#991b1b"
        if node.availability_status == "busy":
            return "#f97316"

        color_by_type = {
            "pad": "#1f78b4",
            "hospital": "#dc2626",
            "charging_hub": "#16a34a",
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

    def _get_node_tooltip_html(self, node):
        status_color = {
            "available": "#16a34a",
            "busy": "#f97316",
            "full": "#dc2626",
        }.get(node.availability_status, "#6b7280")

        return f"""
        <div style="font-family: Arial, sans-serif; font-size: 13px; min-width: 260px;">
            <div style="font-size: 15px; font-weight: 800; margin-bottom: 4px;">
                {node.name}
            </div>
            <div><b>Letter:</b> {node.node_type}</div>
            <div><b>Category:</b> {node.node_category}</div>
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

    def _get_route_popup(self, route):
        return f"""
        <div style="font-family: Arial; width: 340px;">
            <h4 style="margin-bottom: 6px;">Fixed Air Corridor</h4>
            <b>From:</b> {route.start}<br>
            <b>To:</b> {route.end}<br>
            <b>Corridor type:</b> {route.route_type}<br>
            <b>Altitude layer:</b> {route.altitude_layer}<br>
            <b>Safety status:</b> {route.safety_status}<br>
            <b>Distance:</b> {route.distance} km<br>
            <b>Battery cost:</b> {route.battery_cost}<br>
            <b>Noise penalty:</b> {route.noise_penalty}<br>
            <b>Weather penalty:</b> {route.weather_penalty}<br>
            <b>Aircraft count:</b> {route.current_aircraft_count}<br>
            <b>Traffic penalty:</b> {route.traffic_penalty}<br>
            <hr>
            <b>Passenger cost:</b> {route.mission_cost("passenger")}<br>
            <b>Medical emergency cost:</b> {route.mission_cost("emergency")}<br>
        </div>
        """

    def _get_route_style(self, route):
        route_style = {
            "airport_corridor": {"color": "#6366f1", "weight": 5, "dash_array": None},
            "city_corridor": {"color": "#6b7280", "weight": 3, "dash_array": None},
            "medical_corridor": {"color": "#dc2626", "weight": 4, "dash_array": "8, 6"},
            "charging_corridor": {"color": "#16a34a", "weight": 4, "dash_array": "4, 6"},
        }

        style = route_style.get(
            route.route_type,
            {"color": "#555555", "weight": 3, "dash_array": None},
        )

        opacity = 0.85
        if route.safety_status == "closed":
            opacity = 0.20
        elif route.safety_status == "restricted":
            opacity = 0.55

        weight = style["weight"] + min(route.current_aircraft_count, 5) * 0.4

        return style["color"], weight, style["dash_array"], opacity

    def _add_route_to_group(self, route, group):
        start_node = self.nodes[route.start]
        end_node = self.nodes[route.end]

        color, weight, dash_array, opacity = self._get_route_style(route)

        folium.PolyLine(
            locations=[[start_node.lat, start_node.lon], [end_node.lat, end_node.lon]],
            color=color,
            weight=weight,
            opacity=opacity,
            dash_array=dash_array,
            tooltip=(
                f"{route.start} ↔ {route.end} | "
                f"{route.route_type} | {route.altitude_layer} | "
                f"{route.safety_status} | aircraft={route.current_aircraft_count}"
            ),
            popup=folium.Popup(self._get_route_popup(route), max_width=370),
        ).add_to(group)

    def create_interactive_map(
        self,
        filename="backend/munich_airspace_map.html",
        highlight_path=None,
    ):
        output_path = Path(filename)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        munich_bounds = [[48.06, 11.42], [48.38, 11.82]]

        munich_map = folium.Map(
            location=[48.17, 11.62],
            zoom_start=11,
            min_zoom=10,
            max_zoom=18,
            tiles=None,
            max_bounds=True,
            control_scale=True,
        )

        folium.TileLayer("OpenStreetMap", name="OpenStreetMap", control=True).add_to(munich_map)
        folium.TileLayer("CartoDB positron", name="Light map", control=True).add_to(munich_map)
        folium.TileLayer("CartoDB dark_matter", name="Dark map", control=True).add_to(munich_map)

        munich_map.fit_bounds(munich_bounds)
        munich_map.add_child(RestrictMapBounds(munich_bounds))

        # ============================================================
        # LOCATION / NODE CATEGORY LAYERS
        # These are ON by default.
        # Corridors are NOT included in these layers.
        # ============================================================

        transport_hubs_group = folium.FeatureGroup(name="Transport Hubs", show=True)
        city_business_group = folium.FeatureGroup(name="City & Business", show=True)
        universities_group = folium.FeatureGroup(name="Universities", show=True)
        events_tourism_group = folium.FeatureGroup(name="Events & Tourism", show=True)
        residential_demand_group = folium.FeatureGroup(name="Residential / Demand Zones", show=True)
        emergency_hospitals_group = folium.FeatureGroup(name="Emergency Hospitals", show=True)
        charging_hubs_group = folium.FeatureGroup(name="Charging Hubs", show=True)

        # ============================================================
        # CORRIDOR / ROUTE LAYERS
        # These are OFF by default because corridor connections are
        # separate from location category selection.
        # ============================================================

        airport_routes_group = folium.FeatureGroup(name="Airport / Transit Corridors", show=False)
        city_routes_group = folium.FeatureGroup(name="City Corridors", show=False)
        medical_routes_group = folium.FeatureGroup(name="Medical Emergency Corridors", show=False)
        charging_routes_group = folium.FeatureGroup(name="Charging Corridors", show=False)
        full_airspace_group = folium.FeatureGroup(name="Full Airspace Corridors", show=False)
        highlighted_path_group = folium.FeatureGroup(name="Highlighted Best Route", show=False)
        labels_group = folium.FeatureGroup(name="All Location Labels", show=False)

        route_group_by_type = {
            "airport_corridor": airport_routes_group,
            "city_corridor": city_routes_group,
            "medical_corridor": medical_routes_group,
            "charging_corridor": charging_routes_group,
        }

        # Add routes only to route/corridor layers, not node category layers.
        for route in self.routes:
            self._add_route_to_group(route, route_group_by_type[route.route_type])
            self._add_route_to_group(route, full_airspace_group)

        if highlight_path and len(highlight_path) >= 2:
            for start, end in zip(highlight_path[:-1], highlight_path[1:]):
                if start in self.nodes and end in self.nodes:
                    start_node = self.nodes[start]
                    end_node = self.nodes[end]

                    folium.PolyLine(
                        locations=[[start_node.lat, start_node.lon], [end_node.lat, end_node.lon]],
                        color="#facc15",
                        weight=9,
                        opacity=0.95,
                        tooltip=f"Highlighted best route: {start} → {end}",
                    ).add_to(highlighted_path_group)

        # Add node markers to their category layers.
        for node in self.nodes.values():
            popup_html = f"""
            <div style="font-family: Arial; width: 330px;">
                <h3 style="margin-bottom: 4px;">{node.name}</h3>
                <b>Node type:</b> {node.node_type}<br>
                <b>Node category:</b> {node.node_category}<br>
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
                    self._get_node_tooltip_html(node),
                    sticky=True,
                    direction="top",
                    opacity=0.95,
                ),
                popup=folium.Popup(popup_html, max_width=370),
                icon=folium.DivIcon(
                    html=self._get_marker_html(node),
                    icon_size=(32, 32),
                    icon_anchor=(16, 16),
                ),
            )

            if node.node_category == "transport_hub":
                marker.add_to(transport_hubs_group)
            elif node.node_category == "city_business":
                marker.add_to(city_business_group)
            elif node.node_category == "university":
                marker.add_to(universities_group)
            elif node.node_category == "event_tourism":
                marker.add_to(events_tourism_group)
            elif node.node_category == "residential_demand":
                marker.add_to(residential_demand_group)
            elif node.node_category == "emergency_hospital":
                marker.add_to(emergency_hospitals_group)
            elif node.node_category == "charging_hub":
                marker.add_to(charging_hubs_group)

            folium.Marker(
                location=[node.lat + 0.002, node.lon + 0.002],
                icon=folium.DivIcon(
                    html=self._get_label_html(node),
                    icon_size=(240, 20),
                    icon_anchor=(0, 0),
                ),
            ).add_to(labels_group)

        # Add location layers first. Visible by default.
        transport_hubs_group.add_to(munich_map)
        city_business_group.add_to(munich_map)
        universities_group.add_to(munich_map)
        events_tourism_group.add_to(munich_map)
        residential_demand_group.add_to(munich_map)
        emergency_hospitals_group.add_to(munich_map)
        charging_hubs_group.add_to(munich_map)

        # Add corridor layers second. Hidden by default.
        airport_routes_group.add_to(munich_map)
        city_routes_group.add_to(munich_map)
        medical_routes_group.add_to(munich_map)
        charging_routes_group.add_to(munich_map)
        full_airspace_group.add_to(munich_map)
        highlighted_path_group.add_to(munich_map)
        labels_group.add_to(munich_map)

        Fullscreen(position="topright", force_separate_button=True).add_to(munich_map)
        MiniMap(toggle_display=True, minimized=True, position="bottomright").add_to(munich_map)
        MeasureControl(
            position="topleft",
            primary_length_unit="kilometers",
            secondary_length_unit="meters",
        ).add_to(munich_map)

        folium.LayerControl(collapsed=False).add_to(munich_map)

        stats = self.get_network_stats()

        sidebar_html = f"""
        <div style="
            position: fixed;
            top: 20px;
            left: 50px;
            z-index: 9999;
            width: 390px;
            max-height: 88vh;
            overflow-y: auto;
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
                Default view: all hubs shown, corridors hidden
            </div>

            <div style="
                display: grid;
                grid-template-columns: 1fr 1fr 1fr;
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
                    <b>{stats["charging_hubs"]}</b><br><span style="font-size:12px;">Charging</span>
                </div>
                <div style="background:#f9fafb; padding:8px; border-radius:8px;">
                    <b>{stats["total_routes"]}</b><br><span style="font-size:12px;">Corridors</span>
                </div>
                <div style="background:#fff7ed; padding:8px; border-radius:8px;">
                    <b>{stats["total_aircraft_on_routes"]}</b><br><span style="font-size:12px;">Aircraft</span>
                </div>
                <div style="background:#fee2e2; padding:8px; border-radius:8px;">
                    <b>{stats["closed_corridors"]}</b><br><span style="font-size:12px;">Closed</span>
                </div>
            </div>

            <div style="font-size: 13px; line-height: 1.55;">
                <b>Location category filters</b><br>
                Transport Hubs = 4 locations<br>
                City & Business = 6 locations<br>
                Universities = 3 locations<br>
                Events & Tourism = 3 locations<br>
                Residential / Demand Zones = 4 locations<br>
                Emergency Hospitals = 6 locations<br>
                Charging Hubs = 5 locations<br><br>

                <b>Important</b><br>
                Selecting a category shows only locations in that category.
                It does not show corridors automatically.
                Corridor design is controlled separately using the corridor layers.<br><br>

                <b>Mission types</b><br>
                Passenger = normal air taxi routing<br>
                Emergency = medical emergency only<br><br>

                <b>Letter analogy</b><br>
                <span style="color:#1f78b4; font-weight:800;">● P</span> = Pad / passenger or demand node<br>
                <span style="color:#dc2626; font-weight:800;">● H</span> = Hospital / medical emergency node<br>
                <span style="color:#16a34a; font-weight:800;">● C</span> = Charging hub<br><br>

                <b>Marker colors</b><br>
                <span style="color:#1f78b4; font-weight:800;">●</span> Blue = available pad<br>
                <span style="color:#f97316; font-weight:800;">●</span> Orange = busy / low slots<br>
                <span style="color:#991b1b; font-weight:800;">●</span> Dark red = full / no slots<br>
                <span style="color:#dc2626; font-weight:800;">●</span> Red = hospital<br>
                <span style="color:#16a34a; font-weight:800;">●</span> Green = charging hub<br><br>

                <b>Corridor colors</b><br>
                <span style="color:#6366f1; font-weight:900;">━━</span> Purple = airport / transit corridor<br>
                <span style="color:#6b7280; font-weight:900;">━━</span> Grey = city corridor<br>
                <span style="color:#dc2626; font-weight:900;">- - -</span> Red dashed = medical emergency corridor<br>
                <span style="color:#16a34a; font-weight:900;">- - -</span> Green dashed = charging corridor<br>
                <span style="color:#facc15; font-weight:900;">━━</span> Yellow = highlighted best route<br><br>

                <b>Cost formula</b><br>
                Passenger = distance + battery + noise + weather + traffic<br>
                Emergency = distance + weather + reduced traffic
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